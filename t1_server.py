#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t1_server.py —— T1-Remote ATVV 上位机（Pro）

本地 Web 服务：把已验证的 BLE/ATVV 线程 + Raw Input 监听 + 动作系统
通过 HTTP + SSE 暴露给前端界面。

  python t1_server.py                # 启动并打开窗口
  python t1_server.py --port 8791    # 指定端口
  python t1_server.py --no-browser   # 只起服务

与 t1_gui.py（tkinter 版）共用同一套后端线程与同一份配置文件，
两者可以同时存在，但 BLE 连接是独占的，同一时间只能有一个连着。
"""
import argparse
import base64
import io
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback
import wave
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

# exe 下：资源在临时解压目录，可写数据在 exe 旁边
if getattr(sys, "frozen", False):
    RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    BASE_DIR = os.path.dirname(sys.executable)
else:
    RES_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = RES_DIR
sys.path.insert(0, RES_DIR)

import codec  # noqa: E402
import keysynth  # noqa: E402
from t1_gui import (BleWorker, CFG_DEFAULT, CFG_PATH, RawInputWatcher,  # noqa: E402
                    USAGE_NAME, VK_NAME, hx)

WEB_DIR = os.path.join(RES_DIR, "web")
LOG_LIMIT = 600


# ---------------------------------------------------------------- 配置

def load_cfg():
    cfg = json.loads(json.dumps(CFG_DEFAULT))
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    if not os.path.isabs(cfg.get("outdir", "")):
        cfg["outdir"] = os.path.join(BASE_DIR, cfg.get("outdir", "recordings"))
    km = {}
    for k, v in (cfg.get("keymap") or {}).items():
        kk = k if ":" in str(k) else "C:" + str(k)
        km[kk] = v
    cfg["keymap"] = km
    return cfg


def save_cfg(cfg):
    try:
        os.makedirs(cfg.get("outdir") or BASE_DIR, exist_ok=True)
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 全局状态

class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.q = queue.Queue()
        self.subs = set()
        self.cfg = load_cfg()
        self.info = {}
        self.caps = {}
        self.state = {"value": "idle", "text": "未连接"}
        self.segments = {}           # id -> meta（含 raw）
        self.order = []              # id 顺序
        self.logs = deque(maxlen=LOG_LIMIT)
        self.wave_cache = {}         # id -> {"peaks": [...], "wav": bytes}
        self.rate = {"fps": 0.0, "frames": 0, "kb": 0.0}
        self.keycounts = {}
        self.last_key = None
        self.ble = None
        self.hid = None
        self.listening = False
        self.uptime0 = time.time()
        self.speech = {"active": False, "seg": 0, "dur": 0.0, "frames": 0,
                       "level": 0.0}

    # ---- 广播
    def subscribe(self):
        q = queue.Queue(maxsize=4000)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)

    def broadcast(self, ev):
        data = json.dumps(ev, ensure_ascii=False, default=str)
        with self.lock:
            dead = []
            for q in self.subs:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.subs.discard(q)

    def log(self, text, level="info"):
        item = {"t": "log", "text": text, "level": level,
                "ts": time.strftime("%H:%M:%S")}
        with self.lock:
            self.logs.append(item)
        self.broadcast(item)


S = State()


# ---------------------------------------------------------------- 事件泵

def segment_pcm(sid, decode=True):
    """取段的 PCM 与波形峰值（带缓存）"""
    seg = S.segments.get(sid)
    if not seg:
        return None, []
    with S.lock:
        hit = S.wave_cache.get(sid)
    if hit is not None:
        return hit.get("pcm"), hit.get("peaks") or []

    if not decode:
        return None, []

    cfg = S.cfg
    try:
        pcm = codec.decode_stream(seg["raw"],
                                  skip=int(cfg.get("skip_bytes", 4)),
                                  hp=float(cfg.get("hpf_hz", 120.0)),
                                  sr=seg.get("sr", 16000),
                                  norm=bool(cfg.get("normalize", True)))
    except Exception:
        pcm = []

    # 峰值包络：分成 96 桶
    buckets = 96
    peaks = []
    if pcm:
        step = max(1, len(pcm) // buckets)
        for i in range(0, len(pcm), step):
            chunk = pcm[i:i + step]
            m = max(abs(v) for v in chunk) if chunk else 0
            peaks.append(round(min(1.0, m / 32768.0), 3))
        peaks = peaks[:buckets]
    with S.lock:
        S.wave_cache[sid] = {"pcm": pcm, "peaks": peaks}
    return pcm, peaks


def wav_bytes(sid):
    pcm, _ = segment_pcm(sid)
    seg = S.segments.get(sid) or {}
    sr = seg.get("sr", 16000)
    buf = io.BytesIO()
    vals = [max(-32768, min(32767, int(v))) for v in (pcm or [])]
    import struct as _struct
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(_struct.pack("<%dh" % len(vals), *vals) if vals else b"")
    return buf.getvalue()


def prepare_segment(sid):
    """后台解码，让前端列表立刻能画波形"""
    try:
        _, peaks = segment_pcm(sid)
        S.broadcast({"t": "peaks", "id": sid, "peaks": peaks})
    except Exception:
        pass


def pump():
    """把后端线程的事件队列翻译成前端事件"""
    while True:
        try:
            kind, ev = S.q.get()
        except Exception:
            time.sleep(0.05)
            continue
        try:
            handle_event(kind, dict(ev))
        except Exception:
            S.log("事件处理异常: %s" % traceback.format_exc(limit=2), "err")


def handle_event(kind, ev):
    if kind == "log":
        S.log(ev.get("text", ""), ev.get("level", "info"))
        return

    if kind == "state":
        S.state = {"value": ev.get("value", "idle"), "text": ev.get("text", "")}
        S.broadcast({"t": "state", **S.state})
        return

    if kind == "info":
        S.info[ev.get("key", "?")] = ev.get("value", "")
        S.broadcast({"t": "info", "key": ev.get("key"), "value": ev.get("value")})
        return

    if kind == "caps":
        S.caps = {k: v for k, v in ev.items()}
        S.broadcast({"t": "caps", **S.caps})
        return

    if kind == "listening":
        S.listening = bool(ev.get("value"))
        S.broadcast({"t": "listening", "value": S.listening})
        return

    if kind == "ctl":
        S.broadcast({"t": "ctl", "op": ev.get("op"), "name": ev.get("name"),
                     "hex": ev.get("hex")})
        return

    if kind == "seg_begin":
        sid = ev.get("id")
        with S.lock:
            S.segments.setdefault(sid, {})
            S.segments[sid].update({"id": sid, "sid": ev.get("sid"),
                                    "sr": ev.get("sr", 16000), "raw": bytearray(),
                                    "frames": 0, "dur": 0.0, "t0": time.time(),
                                    "reason": "", "live": True})
            if sid not in S.order:
                S.order.append(sid)
        S.speech.update({"active": True, "seg": sid, "dur": 0.0, "frames": 0})
        S.broadcast({"t": "seg_begin", "id": sid, "sid": ev.get("sid"),
                     "sr": ev.get("sr", 16000)})
        S.broadcast({"t": "speech", **S.speech})
        return

    if kind == "audio":
        lvl = min(1.0, float(ev.get("rms") or 0) / 6000.0)
        seg = ev.get("seg")
        with S.lock:
            cur = S.segments.get(seg)
            if cur is not None:
                cur["frames"] = ev.get("frames", 0)
                cur["dur"] = ev.get("dur", 0.0)
        S.speech.update({"active": True, "seg": seg,
                         "dur": ev.get("dur", 0.0),
                         "frames": ev.get("frames", 0), "level": lvl})
        S.broadcast({"t": "audio", "level": lvl, "seg": seg,
                     "frames": ev.get("frames"), "dur": ev.get("dur")})
        return

    if kind == "seg_end":
        sid = ev.get("id")
        raw = ev.pop("raw", b"") or b""
        with S.lock:
            cur = S.segments.setdefault(sid, {"id": sid})
            cur.update({"raw": bytearray(raw), "frames": ev.get("frames"),
                        "dur": ev.get("dur"), "reason": ev.get("reason"),
                        "nbytes": ev.get("nbytes"), "sr": ev.get("sr", 16000),
                        "t0": ev.get("t0", time.time()), "live": False,
                        "sid": ev.get("sid")})
            S.wave_cache.pop(sid, None)
            if sid not in S.order:
                S.order.append(sid)
        S.speech.update({"active": False, "level": 0.0})
        S.broadcast({"t": "seg_end", "id": sid,
                     "dur": ev.get("dur"), "frames": ev.get("frames"),
                     "nbytes": ev.get("nbytes"), "reason": ev.get("reason"),
                     "sr": ev.get("sr", 16000), "t0": ev.get("t0"),
                     "sid": ev.get("sid")})
        S.broadcast({"t": "speech", **S.speech})
        threading.Thread(target=prepare_segment, args=(sid,), daemon=True).start()
        return

    if kind == "rate":
        S.rate = {"fps": round(ev.get("fps", 0), 1), "frames": ev.get("frames", 0),
                  "kb": round(ev.get("kb", 0), 1)}
        S.broadcast({"t": "rate", **S.rate})
        return

    if kind == "rawkey":
        key_id = ev.get("key_id")
        with S.lock:
            S.keycounts[key_id] = S.keycounts.get(key_id, 0) + 1
            S.last_key = {"id": key_id, "hex": ev.get("hex"),
                          "src": ev.get("src"), "code": ev.get("code"),
                          "label": (S.cfg.get("keymap", {}).get(key_id) or {}).get("label")
                          or (VK_NAME.get(ev.get("code")) if ev.get("src") == "key"
                              else USAGE_NAME.get(ev.get("code")))
                          or "未知按键"}
            cnt = S.keycounts[key_id]
        S.broadcast({"t": "rawkey", **S.last_key, "count": cnt})
        act = (S.cfg.get("keymap", {}).get(key_id) or {}).get("action") or ""
        if act:
            run_action(act, key_id)
        return

    if kind == "hidok":
        S.broadcast({"t": "hid", "ok": True, "msg": ev.get("msg", "")})
        return
    if kind == "hidfail":
        S.broadcast({"t": "hid", "ok": False, "msg": ev.get("reason", "")})
        return

    S.broadcast({"t": kind, **{k: v for k, v in ev.items()}})


def run_action(act, key_id=""):
    """动作分发，与 tkinter 版保持一致的语义"""
    act = (act or "").strip()
    if not act:
        return
    kind, _, arg = act.partition(":")
    kind, arg = kind.strip().lower(), arg.strip()
    tag = " [%s]" % key_id if key_id else ""

    if kind in ("key", "text"):
        if not S.cfg.get("run_key", True):
            S.log("已拦截%s：未开启「允许合成按键」" % tag, "warn")
            return
        try:
            if kind == "key":
                names = keysynth.send_hotkey(arg)
                S.log("合成按键%s → %s" % (tag, "+".join(names)), "ok")
            else:
                keysynth.send_text(arg)
                S.log("键入%s: %s" % (tag, arg[:40]), "ok")
        except Exception as e:
            S.log("动作失败%s: %s" % (tag, e), "err")
        return

    if kind in ("open", "cmd"):
        if not S.cfg.get("run_cmd", False):
            S.log("已拦截%s：未开启「允许执行命令」" % tag, "warn")
            return
        try:
            if kind == "cmd":
                subprocess.Popen(arg, shell=True)
                S.log("执行%s: %s" % (tag, arg), "ok")
            else:
                target = arg
                if target and not os.path.isabs(target) and "://" not in target:
                    cand = os.path.join(S.cfg.get("outdir") or BASE_DIR, target)
                    if os.path.exists(cand):
                        target = cand
                os.startfile(target)
                S.log("打开%s: %s" % (tag, target), "ok")
        except Exception as e:
            S.log("失败%s: %s" % (tag, e), "err")
        return

    S.log("未知动作%s: %s" % (tag, act), "warn")


# ---------------------------------------------------------------- 设备控制

def connect_ble():
    if S.ble and S.ble.is_alive():
        S.log("已经连接中", "warn")
        return
    S.ble = BleWorker(S.q, S.cfg)
    S.ble.start()


def disconnect_ble():
    if S.ble:
        S.ble.stop()
        S.log("正在断开…", "mut")


def start_hid():
    if S.hid and S.hid.is_alive():
        return
    S.hid = RawInputWatcher(S.q, S.cfg.get("mac", ""))
    S.hid.start()


def snapshot():
    with S.lock:
        segs = []
        for sid in S.order:
            seg = S.segments.get(sid) or {}
            segs.append({
                "id": sid,
                "sid": seg.get("sid"),
                "dur": round(seg.get("dur") or 0, 3),
                "frames": seg.get("frames") or 0,
                "nbytes": seg.get("nbytes") or 0,
                "reason": seg.get("reason") or "",
                "sr": seg.get("sr", 16000),
                "t0": seg.get("t0"),
                "live": bool(seg.get("live")),
                "peaks": (S.wave_cache.get(sid) or {}).get("peaks") or [],
            })
        return {
            "ok": True,
            "cfg": S.cfg,
            "info": S.info,
            "caps": S.caps,
            "state": S.state,
            "listening": S.listening,
            "segments": segs,
            "logs": list(S.logs),
            "rate": S.rate,
            "keycounts": S.keycounts,
            "last_key": S.last_key,
            "speech": S.speech,
            "hid": bool(S.hid and S.hid.is_alive()),
            "uptime": round(time.time() - S.uptime0, 1),
            "outdir": S.cfg.get("outdir"),
            "cfg_path": CFG_PATH,
            "presets": keysynth.PRESETS,
            "vk_name": {("K:0x%02X" % k): v for k, v in VK_NAME.items()},
            "usage_name": {("C:0x%04X" % k): v for k, v in USAGE_NAME.items()},
        }


# ---------------------------------------------------------------- HTTP

PORT = [8791]

MIME = {".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png",
        ".woff2": "font/woff2", ".ico": "image/x-icon"}


class QuietServer(ThreadingHTTPServer):
    """浏览器断开 / SSE 重连会抛 BrokenPipe，这里静默掉，不刷屏"""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "T1Pro/1.0"

    def log_message(self, fmt, *a):
        pass

    # ---- 工具
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": msg}, code)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    def _file(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        ext = os.path.splitext(path)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        p = unquote(u.path)

        if p in ("/", "/index.html"):
            return self._file(os.path.join(WEB_DIR, "index.html"))
        if p.startswith("/static/"):
            rel = p[len("/static/"):].lstrip("/")
            full = os.path.normpath(os.path.join(WEB_DIR, rel))
            if full.startswith(WEB_DIR) and os.path.isfile(full):
                return self._file(full)
            self.send_error(404)
            return
        if p == "/api/bootstrap":
            return self._json(snapshot())
        if p == "/api/events":
            return self.sse()
        if p.startswith("/api/seg/"):
            return self.seg_get(p)
        self.send_error(404)

    def seg_get(self, p):
        rest = p[len("/api/seg/"):]
        if rest.endswith(".wav"):
            sid = rest[:-4]
            try:
                sid = int(sid)
            except ValueError:
                return self._err("bad id")
            if sid not in S.segments:
                return self._err("no such segment", 404)
            data = wav_bytes(sid)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass
            return
        if rest.endswith(".adpcm"):
            sid = rest[:-6]
            try:
                sid = int(sid)
            except ValueError:
                return self._err("bad id")
            seg = S.segments.get(sid)
            if not seg:
                return self._err("no such segment", 404)
            data = bytes(seg.get("raw") or b"")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             'attachment; filename="voice_%02d.adpcm"' % sid)
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass
            return
        self.send_error(404)

    def sse(self):
        q = S.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 1500\n\n")
            self.wfile.flush()
            last_ping = time.time()
            while True:
                try:
                    data = q.get(timeout=8.0)
                    self.wfile.write(("data: %s\n\n" % data).encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    if time.time() - last_ping > 3600:
                        break
                    last_ping = time.time()
        except Exception:
            pass
        finally:
            S.unsubscribe(q)

    # ---- POST
    def do_POST(self):
        p = unquote(urlparse(self.path).path)
        b = self._body()

        if p == "/api/connect":
            mac = (b.get("mac") or "").strip()
            name = (b.get("name") or "").strip()
            # 显式传空表示「不用地址」，要能清掉旧值
            if "mac" in b:
                S.cfg["mac"] = mac.upper()
            if "name" in b:
                S.cfg["name"] = name
            if "mac" in b or "name" in b:
                save_cfg(S.cfg)
            connect_ble()
            return self._json({"ok": True})

        if p == "/api/disconnect":
            disconnect_ble()
            return self._json({"ok": True})

        if p == "/api/caps":
            if S.ble and S.ble.is_alive():
                S.ble.call(S.ble.cmd_get_caps)
                return self._json({"ok": True})
            return self._err("未连接")

        if p == "/api/config":
            cfg = b.get("cfg") or {}
            for k, v in cfg.items():
                if k == "keymap":
                    continue
                S.cfg[k] = v
            save_cfg(S.cfg)
            S.log("配置已保存", "ok")
            return self._json({"ok": True, "cfg": S.cfg})

        if p == "/api/key":
            kid = b.get("id")
            if not kid:
                return self._err("缺少 id")
            km = S.cfg.setdefault("keymap", {})
            if b.get("delete"):
                km.pop(kid, None)
            else:
                km[kid] = {"label": b.get("label") or "未命名",
                           "action": (b.get("action") or "").strip()}
            save_cfg(S.cfg)
            return self._json({"ok": True, "keymap": S.cfg["keymap"]})

        if p == "/api/key/reset":
            S.cfg["keymap"] = json.loads(json.dumps(CFG_DEFAULT["keymap"]))
            # 补上方向键默认条目
            for kid, lb in (("K:0x26", "上 ↑"), ("K:0x28", "下 ↓"),
                            ("K:0x25", "左 ←"), ("K:0x27", "右 →"),
                            ("K:0x0D", "OK / 确认")):
                S.cfg["keymap"].setdefault(kid, {"label": lb, "action": ""})
            save_cfg(S.cfg)
            return self._json({"ok": True, "keymap": S.cfg["keymap"]})

        if p == "/api/action/test":
            act = (b.get("action") or "").strip()
            if not act:
                return self._err("动作为空")
            run_action(act, "测试")
            return self._json({"ok": True,
                               "desc": keysynth.describe(act)})

        if p == "/api/action/describe":
            act = (b.get("action") or "").strip()
            return self._json({"ok": True, "desc": keysynth.describe(act) if act else "无动作（仅记录）"})

        if p == "/api/seg/delete":
            sid = b.get("id")
            with S.lock:
                S.segments.pop(sid, None)
                S.wave_cache.pop(sid, None)
                if sid in S.order:
                    S.order.remove(sid)
            return self._json({"ok": True})

        if p == "/api/seg/save":
            sid = b.get("id")
            seg = S.segments.get(sid)
            if not seg:
                return self._err("无此段")
            outdir = S.cfg.get("outdir") or os.path.join(BASE_DIR, "recordings")
            os.makedirs(outdir, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(seg.get("t0") or time.time()))
            base = "voice_%s_%02d" % (stamp, sid)
            wav_path = os.path.join(outdir, base + ".wav")
            with open(wav_path, "wb") as f:
                f.write(wav_bytes(sid))
            if b.get("with_raw"):
                with open(os.path.join(outdir, base + ".adpcm"), "wb") as f:
                    f.write(bytes(seg.get("raw") or b""))
            S.log("已保存 %s" % wav_path, "ok")
            return self._json({"ok": True, "path": wav_path})

        if p == "/api/open/dir":
            target = b.get("path") or S.cfg.get("outdir") or BASE_DIR
            try:
                os.makedirs(target, exist_ok=True)
                os.startfile(target)
                return self._json({"ok": True})
            except Exception as e:
                return self._err(str(e))

        if p == "/api/pickdir":
            try:
                code = ("import tkinter as tk;from tkinter import filedialog;"
                        "r=tk.Tk();r.withdraw();r.attributes('-topmost',True);"
                        "p=filedialog.askdirectory(title='选择录音输出目录');print(p or '')")
                out = subprocess.run([sys.executable, "-c", code],
                                     capture_output=True, text=True, timeout=180)
                path = (out.stdout or "").strip().splitlines()
                path = path[-1].strip() if path else ""
                return self._json({"ok": True, "path": path})
            except Exception as e:
                return self._err(str(e))

        if p == "/api/window":
            page = (b.get("page") or "").strip()
            open_id = (b.get("open") or "").strip()
            qs = []
            if page:
                qs.append("page=" + page)
            if open_id:
                qs.append("open=" + open_id)
            extra = ("?" + "&".join(qs)) if qs else ""
            open_window("http://127.0.0.1:%d/%s" % (PORT[0], extra))
            return self._json({"ok": True})

        if p == "/api/hid/start":
            start_hid()
            return self._json({"ok": True})

        if p == "/api/logs/clear":
            with S.lock:
                S.logs.clear()
            return self._json({"ok": True})

        if p == "/api/shutdown":
            self._json({"ok": True})
            threading.Timer(0.35, lambda: os._exit(0)).start()
            return

        self.send_error(404)


# ---------------------------------------------------------------- 启动

def pick_port(start=8791, tries=12):
    for i in range(tries):
        p = start + i
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def open_window(url):
    """优先用 Edge/Chrome 的 app 模式包一层无浏览器外壳的窗口"""
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    profile = os.path.join(BASE_DIR, ".browser_profile")
    for exe in cands:
        if os.path.isfile(exe):
            try:
                subprocess.Popen([exe, "--app=" + url,
                                  "--window-size=1360,900",
                                  "--user-data-dir=" + profile,
                                  "--no-first-run",
                                  "--no-default-browser-check",
                                  "--disable-features=Translate"])
                return True
            except Exception:
                continue
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    port = args.port or pick_port()
    PORT[0] = port
    threading.Thread(target=pump, daemon=True).start()

    if S.cfg.get("hid_listen", True):
        threading.Thread(target=start_hid, daemon=True).start()

    httpd = QuietServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port

    print("T1-Remote 上位机 Pro")
    print("  地址: %s" % url)
    print("  配置: %s" % CFG_PATH)
    print("  输出: %s" % S.cfg.get("outdir"))
    print("  按 Ctrl+C 退出")

    if not args.no_browser:
        threading.Timer(0.6, lambda: open_window(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        try:
            if S.ble:
                S.ble.stop()
        except Exception:
            pass
        httpd.server_close()


if __name__ == "__main__":
    main()
