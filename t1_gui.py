#!/usr/bin/env python3
"""T1-Remote 自定义配置上位机

协议栈:
  AB5E0001   Google ATVV (Voice-over-BLE v1.0)
    AB5E0002 TX     主机 -> 遥控器  GET_CAPS / MIC_OPEN / MIC_CLOSE / MIC_EXTEND
    AB5E0003 AUDIO  遥控器 -> 主机  IMA ADPCM 4bit 16kHz, 128 字节/帧
    AB5E0004 CTL    遥控器 -> 主机  事件: AUDIO_START/STOP/SYNC/CAPS_RESP/...
  HID 0x1812        按键 (Consumer RID 0x02)  —— 通过 hidapi 旁路读取

音频链路: 段首 4 字节 0x77 填充 -> 跳过 -> IMA 解码 -> 高通去 DC 漂移 -> 归一化

用法:
  python t1_gui.py
"""
import asyncio
import ctypes
import datetime
import json
import math
import os
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes
from tkinter import filedialog, messagebox, ttk

try:
    from bleak import BleakClient, BleakScanner
    HAVE_BLE = True
    try:
        import bleak_compat

        bleak_compat.apply()
    except Exception:
        pass
except ImportError:
    HAVE_BLE = False

try:
    import hid
    HAVE_HID = True
except ImportError:
    HAVE_HID = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codec  # noqa: E402

try:
    import keysynth  # noqa: E402
    HAVE_KEYSYNTH = True
except Exception:  # pragma: no cover
    keysynth = None
    HAVE_KEYSYNTH = False

try:
    import stt  # noqa: E402
    HAVE_STT = stt.available()
except Exception:  # pragma: no cover
    stt = None
    HAVE_STT = False

user32 = ctypes.WinDLL("user32", use_last_error=True)


def foreground_title():
    """当前前台窗口标题，用来确认识别出的字打到了哪里"""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value
    except Exception:
        return ""

# ---------------------------------------------------------------- 常量

APP_TITLE = "T1-Remote 上位机"
# 打包成 exe 后 __file__ 指向 PyInstaller 的临时解压目录，
# 可写数据（配置/录音）必须落在 exe 旁边，否则每次重启都丢
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE_DIR, "t1_ui_config.json")

TX_UUID = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
AUDIO_UUID = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
CTL_UUID = "ab5e0004-5a21-4f05-bc7d-af01f617b664"

EV_AUDIO_STOP = 0x00
EV_AUDIO_START = 0x04
EV_START_SEARCH = 0x08
EV_AUDIO_SYNC = 0x0A
EV_CAPS_RESP = 0x0B
EV_MIC_OPEN_ERROR = 0x0C

EV_NAME = {
    EV_AUDIO_STOP: "AUDIO_STOP", EV_AUDIO_START: "AUDIO_START",
    EV_START_SEARCH: "START_SEARCH", EV_AUDIO_SYNC: "AUDIO_SYNC",
    EV_CAPS_RESP: "CAPS_RESP", EV_MIC_OPEN_ERROR: "MIC_OPEN_ERROR",
}
STOP_REASON = {
    0x00: "MIC_CLOSE", 0x02: "松开按键", 0x04: "将开始新流",
    0x08: "音频传输超时", 0x10: "通知被关闭", 0x80: "其他",
}
OPEN_ERR = {
    0x0F01: "保留 / 未就绪", 0x0F02: "遥控器未激活",
    0x0F03: "音频通知未开启", 0x0F80: "PTT/HTT 进行中", 0x0FFF: "内部错误",
}
USAGE_NAME = {
    0x00E2: "静音 Mute", 0x00E9: "音量 +", 0x00EA: "音量 −",
    0x0219: "电视/输入源", 0x0221: "语音搜索（语音键）",
    0x0223: "主页 / GoTo", 0x0224: "返回（Browser Back）",
    0x0183: "媒体播放暂停", 0x0184: "上一曲", 0x0185: "下一曲",
    0x00B0: "播放", 0x00B1: "暂停", 0x00B3: "快进", 0x00B4: "快退",
    0x00CD: "播放暂停", 0x00E0: "音量控制", 0x00EF: "升音", 0x00EE: "降音",
    0x006F: "亮度+", 0x0070: "亮度−", 0x029C: "AC 频道+", 0x029D: "AC 频道−",
}

# 键盘集合（COL01）实际的 VKey —— 方向键与确认键都走这里
VK_NAME = {
    0x26: "上 ↑", 0x28: "下 ↓", 0x25: "左 ←", 0x27: "右 →",
    0x0D: "OK / 确认", 0xA6: "返回", 0xA7: "前进", 0x24: "主页 Home",
    0x23: "End", 0x21: "PageUp", 0x22: "PageDown", 0x1B: "Esc",
    0x08: "Backspace", 0x09: "Tab", 0x20: "空格", 0x2E: "Delete",
    0x5B: "Win", 0xA4: "Alt", 0xA2: "Ctrl", 0xA0: "Shift",
    0x6A: "*", 0x6D: "-", 0x6B: "+", 0x0A: "全屏", 0x5D: "菜单",
}

FRAME = 128
DEFAULT_SR = 16000

CFG_DEFAULT = {
    "mac": "AA:BB:CC:DD:EE:FF",
    "name": "T1",
    "outdir": os.path.join(BASE_DIR, "recordings"),
    "auto_save": True,
    "skip_bytes": 4,
    "hpf_hz": 120.0,
    "normalize": True,
    "auto_mic_open": True,
    "mic_extend_ms": 2000,
    "max_segment_s": 30,
    "voice_input": True,
    "wave_frames": 320,
    "hid_listen": True,
    "run_key": True,
    "run_cmd": False,
    "keymap": {
        "C:0x0221": {"label": "语音键", "action": ""},
        "C:0x0223": {"label": "主页", "action": ""},
        "C:0x0224": {"label": "返回", "action": ""},
        "C:0x00E9": {"label": "音量+", "action": ""},
        "C:0x00EA": {"label": "音量−", "action": ""},
        "C:0x00E2": {"label": "静音", "action": ""},
        "K:0x26": {"label": "上 ↑", "action": ""},
        "K:0x28": {"label": "下 ↓", "action": ""},
        "K:0x25": {"label": "左 ←", "action": ""},
        "K:0x27": {"label": "右 →", "action": ""},
        "K:0x0D": {"label": "OK / 确认", "action": ""},
    },
}

# ---------------------------------------------------------------- 配色

C_BG = "#eef1f5"
C_CARD = "#ffffff"
C_EDGE = "#d5dbe4"
C_TEXT = "#1b2330"
C_MUT = "#66707f"
C_ACC = "#1f6feb"
C_ACC_D = "#1854b3"
C_OK = "#17864a"
C_WARN = "#b8811a"
C_ERR = "#c0392b"
C_WAVE_BG = "#131a26"
C_WAVE_BAR = "#49d17f"
C_WAVE_PEAK = "#2f8f5b"


def now_str():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def hx(b):
    return " ".join("%02X" % x for x in b)


# ---------------------------------------------------------------- 流式解码器

class StreamDecoder:
    """维护跨帧连续的 IMA 状态 + 一阶高通，仅用于实时波形显示"""

    def __init__(self, sr=DEFAULT_SR, hpf=120.0):
        self.sr = sr
        self.pred = 0
        self.index = 0
        self.a = math.exp(-2.0 * math.pi * max(hpf, 1.0) / sr)
        self.x1 = 0.0
        self.y1 = 0.0
        self.pending_skip = 4
        self.total = 0

    def reset(self, skip=4):
        self.pred = 0
        self.index = 0
        self.x1 = self.y1 = 0.0
        self.pending_skip = skip
        self.total = 0

    def feed(self, data):
        if self.pending_skip:
            n = min(self.pending_skip, len(data))
            data = data[n:]
            self.pending_skip -= n
            if not data:
                return []
        out = []
        push = out.append
        pred, index = self.pred, self.index
        a, x1, y1 = self.a, self.x1, self.y1
        for byte in data:
            for nib in ((byte >> 4) & 0x0F, byte & 0x0F):
                step = codec.IMA_STEP[index]
                diff = step >> 3
                if nib & 4:
                    diff += step
                if nib & 2:
                    diff += step >> 1
                if nib & 1:
                    diff += step >> 2
                pred += -diff if (nib & 8) else diff
                if pred > 32767:
                    pred = 32767
                elif pred < -32768:
                    pred = -32768
                index += codec.IMA_INDEX[nib]
                if index < 0:
                    index = 0
                elif index > 88:
                    index = 88
                y = a * (y1 + pred - x1)
                x1, y1 = pred, y
                push(y)
        self.pred, self.index = pred, index
        self.x1, self.y1 = x1, y1
        self.total += len(out)
        return out


# ---------------------------------------------------------------- BLE 工作线程

class BleWorker(threading.Thread):
    def __init__(self, out_q, cfg):
        super().__init__(daemon=True)
        self.q = out_q
        self.cfg = cfg
        self.loop = None
        self.client = None
        self.stop_flag = threading.Event()
        self.listening = False
        self.seg_id = 0
        self.cur = None
        self.sr = DEFAULT_SR
        self.dec = StreamDecoder(self.sr, cfg.get("hpf_hz", 120.0))
        self.extend_task = None
        self.frames_total = 0
        self.burst = 0
        self.burst_t0 = time.time()
        self.last_rate = 0.0
        self.max_since = 0.0
        # 语音识别：段结束后排队，由独立线程做 SAPI 识别再键入
        self.stt_q = queue.Queue()
        self.stt_thread = None
        self._stt_warned = False

    # ---- 事件投递
    def emit(self, kind, **kw):
        self.q.put((kind, kw))

    def log(self, text, level="info"):
        self.emit("log", text=text, level=level)

    # ---- 连接
    def _paired_devices(self):
        """Windows 已绑定的 BLE 设备 -> [(名称, MAC)]

        走注册表 Enum\\BTHLE：设备与系统连接后就不再广播，扫描找不到，
        而这里无论是否在广播都能拿到地址。"""
        try:
            import winreg
        except ImportError:
            return []
        base = r"SYSTEM\CurrentControlSet\Enum\BTHLE"
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except OSError as e:
            self.log("读取已配对设备失败: %s" % e, "warn")
            return []
        out = []
        try:
            total = winreg.QueryInfoKey(root)[0]
            for i in range(total):
                try:
                    dev = winreg.EnumKey(root, i)
                except OSError:
                    break
                if not dev.startswith("Dev_") or len(dev) != 16:
                    continue
                raw = dev[4:]
                mac = ":".join(raw[j:j + 2].upper() for j in range(0, 12, 2))
                name = ""
                try:
                    dk = winreg.OpenKey(root, dev)
                    try:
                        for j in range(winreg.QueryInfoKey(dk)[0]):
                            try:
                                ik = winreg.OpenKey(dk, winreg.EnumKey(dk, j))
                            except OSError:
                                continue
                            try:
                                name = str(winreg.QueryValueEx(ik, "FriendlyName")[0] or "")
                            except OSError:
                                name = ""
                            finally:
                                winreg.CloseKey(ik)
                            if name:
                                break
                    finally:
                        winreg.CloseKey(dk)
                except OSError:
                    pass
                out.append((name.strip(), mac))
        finally:
            winreg.CloseKey(root)
        return out

    async def _scan_devices(self, seconds=5.0):
        try:
            devs = await BleakScanner.discover(timeout=seconds)
        except Exception as e:
            self.log("扫描失败: %s: %s" % (type(e).__name__, e), "warn")
            return []
        return [((d.name or "").strip(), d.address) for d in devs]

    @staticmethod
    def _match(dev_name, want):
        a, b = (dev_name or "").lower(), (want or "").lower()
        return bool(a) and bool(b) and (b in a or a in b)

    @staticmethod
    def _drop_requester(client):
        """取消连接后放掉 WinRT 句柄，避免残留占用"""
        try:
            req = getattr(client._backend, "_requester", None)
            if req is not None:
                req.close()
                client._backend._requester = None
        except Exception:
            pass

    async def _open(self, addr, label, timeout=12.0):
        self.log("尝试连接 %s（%s）" % (addr, label))
        client = BleakClient(addr, timeout=timeout)
        try:
            client._backend._device_info = int(addr.replace(":", ""), 16)
        except Exception:
            pass
        # bleak 的 timeout 只作用于「找设备」，真正的 GATT 连接会无限期挂住
        try:
            await asyncio.wait_for(client.connect(), timeout=timeout)
        except asyncio.TimeoutError:
            self._drop_requester(client)
            raise RuntimeError(
                "%.0f 秒内没有连上：遥控器可能休眠、没在 Windows 蓝牙里处于"
                "「已连接」，或被别的程序占用" % timeout)
        except Exception:
            self._drop_requester(client)
            raise
        return client

    async def _connect(self):
        mac = (self.cfg.get("mac") or "").strip().upper()
        name = (self.cfg.get("name") or "").strip()
        self.emit("state", value="connecting", text="连接中…")

        client = None
        tried = set()
        errs = []

        if mac:
            try:
                client = await self._open(mac, "配置里的 MAC")
            except Exception as e:
                errs.append("%s: %s: %s" % (mac, type(e).__name__, e))
                self.log("MAC 直连失败: %s: %s" % (type(e).__name__, e), "warn")
            tried.add(mac)

        if client is None and name:
            self.log("按名称「%s」查找设备…" % name)
            paired = self._paired_devices()
            if paired:
                self.log("已配对设备: %s" % ", ".join(
                    "%s(%s)" % (n or "?", m) for n, m in paired))
            hits = [(n, m) for n, m in paired if self._match(n, name)]

            # 已配对列表里能找到就直接连，省掉 5s 扫描
            if not hits:
                scanned = await self._scan_devices()
                if scanned:
                    self.log("广播中的设备: %s" % ", ".join(
                        "%s(%s)" % (n or "?", a) for n, a in scanned))
                hits = [(n, a) for n, a in scanned if self._match(n, name)]

            for dname, addr in hits:
                if addr in tried:
                    continue
                tried.add(addr)
                try:
                    client = await self._open(addr, "名称匹配到 %s" % dname)
                    break
                except Exception as e:
                    errs.append("%s: %s: %s" % (addr, type(e).__name__, e))
                    self.log("连接 %s 失败: %s" % (addr, e), "warn")

        if client is None:
            if errs:
                raise RuntimeError("没连上。已尝试 -> " + " | ".join(errs))
            if not name:
                raise RuntimeError("没连上：设备名称是空的，无法按名称查找")
            raise RuntimeError("没连上：按名称「%s」没有找到匹配的设备" % name)

        if mac and client.address.upper() != mac:
            self.log("实际连接地址 %s 与配置 MAC %s 不同，建议更新 mac"
                     % (client.address, mac), "warn")

        self.client = client
        mtu = getattr(client, "mtu_size", 0) or 0
        self.emit("state", value="connected", text="已连接")
        self.log("BLE 已连接，MTU=%s" % (mtu or "?"))

        await client.start_notify(CTL_UUID, self.on_ctl)
        await client.start_notify(AUDIO_UUID, self.on_audio)
        self.log("已订阅 CTL + AUDIO")

        # 设备身份
        try:
            man = await client.read_gatt_char("00002a29-0000-1000-8000-00805f9b34fb")
            self.emit("info", key="厂商", value=man.decode(errors="ignore").strip("\x00"))
        except Exception:
            pass
        try:
            model = await client.read_gatt_char("00002a24-0000-1000-8000-00805f9b34fb")
            self.emit("info", key="型号", value=model.decode(errors="ignore").strip("\x00"))
        except Exception:
            pass
        try:
            fw = await client.read_gatt_char("00002a26-0000-1000-8000-00805f9b34fb")
            self.emit("info", key="固件", value=fw.decode(errors="ignore").strip("\x00"))
        except Exception:
            pass
        try:
            bat = await client.read_gatt_char("00002a19-0000-1000-8000-00805f9b34fb")
            raw = bat[0]
            pct = int(raw / 2) if raw > 100 else raw
            self.emit("info", key="电量", value="%d%% (raw 0x%02X)" % (pct, raw))
        except Exception:
            pass

        await self.cmd_get_caps()

    async def cmd_get_caps(self):
        await self.send("GET_CAPS", [0x0A, 0x01, 0x00, 0x00, 0x03, 0x03])

    async def send(self, name, payload):
        if not self.client:
            return
        try:
            await self.client.write_gatt_char(TX_UUID, bytes(payload), response=False)
            self.log("TX %s → %s" % (name, hx(payload)))
        except Exception as e:
            self.log("TX %s 失败: %s: %s" % (name, type(e).__name__, e), "err")

    async def mic_open(self, capture=True):
        await self.send("MIC_OPEN", [0x0C, 0x01 if capture else 0x00])

    async def mic_close(self, sid=0xFF):
        await self.send("MIC_CLOSE", [0x0D, sid])

    # ---- MIC_EXTEND 续期（HTT 模式按住期间防止 3s 超时）
    async def _extend_loop(self, sid):
        interval = max(self.cfg.get("mic_extend_ms", 2000), 500) / 1000.0
        try:
            while True:
                await asyncio.sleep(interval)
                if not self.cur:
                    break
                await self.send("MIC_EXTEND", [0x0E, sid & 0xFF])
        except asyncio.CancelledError:
            pass

    # ---- 段管理
    def _begin_segment(self, sid, reason, codec_id):
        self.seg_id += 1
        self.sr = 16000 if codec_id == 0x02 else 8000
        self.dec = StreamDecoder(self.sr, self.cfg.get("hpf_hz", 120.0))
        self.dec.reset(self.cfg.get("skip_bytes", 4))
        self.cur = {
            "id": self.seg_id,
            "sid": sid,
            "reason": reason,
            "t0": time.time(),
            "raw": bytearray(),
            "frames": 0,
        }
        self.max_since = time.time()
        self.emit("seg_begin", id=self.seg_id, sid=sid, sr=self.sr)
        if self.extend_task:
            self.extend_task.cancel()
        self.extend_task = asyncio.ensure_future(self._extend_loop(sid))

    def _end_segment(self, reason_txt):
        cur = self.cur
        if not cur:
            return
        if self.extend_task:
            self.extend_task.cancel()
            self.extend_task = None
        dur = cur["frames"] * FRAME * 2 / self.sr
        self.emit("seg_end", id=cur["id"], sid=cur["sid"], frames=cur["frames"],
                  dur=dur, nbytes=len(cur["raw"]), reason=reason_txt,
                  sr=self.sr, t0=cur["t0"], raw=bytes(cur["raw"]))
        self.cur = None
        self._queue_voice_input(cur, dur)

    # ---- 语音输入：段落结束 -> 解码成 wav -> 交给识别线程
    def _queue_voice_input(self, cur, dur):
        if not cur["raw"]:
            return
        if not self.stt_thread:
            if not self._stt_warned:
                self._stt_warned = True
                self.log("语音输入未启用：voice_input=%r，识别模块可用=%s"
                         % (self.cfg.get("voice_input"), HAVE_STT), "warn")
            return
        if dur < 0.3:
            self.log("语音段太短（%.2fs），跳过识别" % dur, "mut")
            return
        try:
            pcm = codec.decode_stream(bytes(cur["raw"]),
                                      self.cfg.get("skip_bytes", 4),
                                      self.cfg.get("hpf_hz", 120.0),
                                      self.sr,
                                      self.cfg.get("normalize", True))
            # 音量过低说明遥控器没收到人声，识别必然是空，先说清楚
            rms = 0.0
            if pcm:
                step = max(1, len(pcm) // 2000)
                sample = pcm[::step]
                rms = (sum(v * v for v in sample) / len(sample)) ** 0.5
            if rms < 400:
                self.log("语音段音量过低（RMS=%.0f），没有收到人声，跳过识别" % rms, "warn")
                return

            path = os.path.join(tempfile.gettempdir(),
                                "t1_stt_%d_%d.wav" % (os.getpid(), cur["id"]))
            codec.write_wav(path, pcm, self.sr)
            self.stt_q.put(path)
        except Exception as e:
            self.log("生成识别音频失败: %s: %s" % (type(e).__name__, e), "warn")

    def _stt_loop(self):
        while True:
            try:
                path = self.stt_q.get(timeout=0.5)
            except queue.Empty:
                if self.stop_flag.is_set():
                    return
                continue
            if path is None:
                return
            t0 = time.time()
            try:
                text = stt.recognize_wav(path)
            except Exception as e:
                text = ""
                self.log("语音识别异常: %s: %s" % (type(e).__name__, e), "err")
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            cost = time.time() - t0
            if not text:
                self.log("语音识别：没有识别出内容（%.1fs）" % cost, "warn")
                continue
            self.log("语音识别（%.1fs）: %s" % (cost, text), "ok")
            if not HAVE_KEYSYNTH:
                self.log("keysynth 不可用，无法键入", "err")
                continue
            try:
                self.log("目标前台窗口: %s" % (foreground_title() or "?"), "mut")
                keysynth.send_text(text)
                self.log("已键入到当前窗口: %s" % text, "ok")
            except Exception as e:
                self.log("键入失败: %s: %s" % (type(e).__name__, e), "err")

    # ---- notify 回调
    def on_ctl(self, _sender, data):
        b = bytes(data)
        if not b:
            return
        op = b[0]
        self.emit("ctl", op=op, name=EV_NAME.get(op, "UNK_0x%02X" % op), hex=hx(b))

        if op == EV_START_SEARCH:
            self.log("◀ 语音键按下 (START_SEARCH)" + (" → MIC_OPEN" if self.cfg.get("auto_mic_open") else " (自动开麦已关闭)"))
            if self.cfg.get("auto_mic_open"):
                asyncio.ensure_future(self.mic_open(capture=True))

        elif op == EV_CAPS_RESP and len(b) >= 8:
            ver = struct.unpack(">H", b[1:3])[0]
            codecs, model = b[3], b[4]
            frame = struct.unpack(">H", b[5:7])[0]
            self.sr = 16000 if (codecs & 0x02) else 8000
            info = {"version": "0x%04X" % ver, "codecs": "0x%02X" % codecs,
                    "model": "0x%02X" % model, "frame": frame, "sr": self.sr,
                    "ext": "0x%02X" % b[7] if len(b) > 7 else "-"}
            self.emit("caps", **info)
            self.log("CAPS_RESP v%s codecs=%s 交互模型=%s 帧长=%d 采样率=%d"
                     % (info["version"], info["codecs"], info["model"], frame, self.sr))

        elif op == EV_AUDIO_START and len(b) >= 4:
            reason, codec_id, sid = b[1], b[2], b[3]
            self._begin_segment(sid, reason, codec_id)
            self.log("▶ 新语音段 #%d sid=%d codec=0x%02X %dHz"
                     % (self.seg_id, sid, codec_id, self.sr), "ok")

        elif op == EV_AUDIO_STOP and len(b) >= 2:
            self._end_segment(STOP_REASON.get(b[1], "0x%02X" % b[1]))
            self.log("■ 本段结束: %s" % STOP_REASON.get(b[1], "0x%02X" % b[1]))

        elif op == EV_AUDIO_SYNC and len(b) >= 7:
            fno = struct.unpack(">H", b[2:4])[0]
            self.log("AUDIO_SYNC codec=0x%02X frame_no=%d index=%d" % (b[1], fno, b[6]), "mut")

        elif op == EV_MIC_OPEN_ERROR and len(b) >= 3:
            code = struct.unpack(">H", b[1:3])[0]
            self.log("MIC_OPEN_ERROR 0x%04X (%s)"
                     % (code, OPEN_ERR.get(code, "未知")), "warn")

    def on_audio(self, _sender, data):
        b = bytes(data)
        if not self.listening:
            return
        now = time.time()
        if self.cur is None:
            self._begin_segment(-1, -1, 0x02)
            self.log("收到音频流但无 AUDIO_START，自动建段", "warn")

        self.cur["raw"] += b
        self.cur["frames"] += 1
        self.frames_total += 1

        pcm = self.dec.feed(b)
        rms = 0.0
        if pcm:
            rms = math.sqrt(sum(v * v for v in pcm) / len(pcm))

        # 帧率统计
        self.burst += 1
        if now - self.burst_t0 >= 1.0:
            rate = self.burst / (now - self.burst_t0)
            if now - self.last_rate >= 1.0:
                self.emit("rate", fps=rate, frames=self.frames_total,
                          kb=self.frames_total * FRAME / 1000.0)
                self.last_rate = now
            self.burst = 0
            self.burst_t0 = now

        self.emit("audio", rms=rms, seg=self.cur["id"],
                  frames=self.cur["frames"],
                  dur=self.cur["frames"] * FRAME * 2 / self.sr)

        # 单段最长保护
        limit = self.cfg.get("max_segment_s", 30)
        if limit and now - self.max_since > limit:
            self.log("单段超过 %ds，自动 MIC_CLOSE" % limit, "warn")
            asyncio.ensure_future(self.mic_close(self.cur["sid"]))
            self.max_since = now + 3600

    # ---- 线程主体
    async def _amain(self):
        try:
            await self._connect()
        except Exception as e:
            self.emit("state", value="error", text="连接失败")
            self.log("连接失败: %s: %s" % (type(e).__name__, e), "err")
            return

        self.listening = True
        self.emit("listening", value=True)
        self.log("进入监听：按遥控器语音键说话即可，自动开麦", "ok")

        while not self.stop_flag.is_set():
            await asyncio.sleep(0.15)
            if self.client and not self.client.is_connected:
                self.log("连接已断开", "warn")
                break

        self.listening = False
        self._end_segment("上位机停止")
        try:
            await self.client.disconnect()
        except Exception:
            pass
        self.emit("state", value="idle", text="未连接")
        self.emit("listening", value=False)

    def _start_stt(self):
        if not self.cfg.get("voice_input") or not HAVE_STT:
            return
        self.stt_thread = threading.Thread(target=self._stt_loop, daemon=True)
        self.stt_thread.start()
        self.log("语音输入已启用（Windows 内置识别），按住语音键说完松开即可", "ok")

    def _stop_stt(self):
        self.stt_thread = None
        self.stt_q.put(None)

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._start_stt()
        try:
            self.loop.run_until_complete(self._amain())
        finally:
            self._stop_stt()
            try:
                self.loop.close()
            except Exception:
                pass

    def stop(self):
        self.stop_flag.set()

    def call(self, coro_fn, *a):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro_fn(*a), self.loop)


# ---------------------------------------------------------------- HID 按键监听

# ---- Raw Input 结构体（模块级：类体内互相引用只有在类创建后才可见）
class _RI_DEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class _RI_HEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class _RI_BTN(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _RI_BTNU(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("s", _RI_BTN)]


class _RI_MOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("u", _RI_BTNU),
                ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class _RI_KEYBD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT),
                ("Reserved", wintypes.USHORT), ("VKey", wintypes.USHORT),
                ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class _RI_HID(ctypes.Structure):
    _fields_ = [("dwSizeHid", wintypes.DWORD), ("dwCount", wintypes.DWORD),
                ("bRawData", ctypes.c_ubyte * 1)]


class _RI_DATA(ctypes.Union):
    _fields_ = [("mouse", _RI_MOUSE), ("keyboard", _RI_KEYBD), ("hid", _RI_HID)]


class _RI_RAWINPUT(ctypes.Structure):
    _fields_ = [("header", _RI_HEADER), ("data", _RI_DATA)]


class _RI_WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class RawInputWatcher(threading.Thread):
    """Raw Input 监听（覆盖全部 HID 入口）

    Windows 上 Mouse(COL04) / Keyboard(COL01) 集合被 hidclass 独占，hidapi 打不开，
    只有 Raw Input 的 INPUTSINK 能旁路拿到。方向键就走在键盘集合里：
      上 make=0x48 vk=0x26 / 下 0x50 0x28 / 左 0x4B 0x25 / 右 0x4D 0x27
      OK 0x1C 0x0D / 返回经 Consumer 0x0224 -> VK_BROWSER_BACK 0xA6
    注意：目标窗口必须 ShowWindow，隐藏窗口收不到 INPUTSINK 事件。
    """
    WM_INPUT = 0x00FF
    RID_INPUT = 0x10000003
    RIDI_DEVICENAME = 0x20000007
    RIDEV_INPUTSINK = 0x00000100
    RIM_MOUSE, RIM_KEYBOARD, RIM_HID = 0, 1, 2

    USAGE_CLASSES = [(0x01, 0x02), (0x01, 0x06), (0x01, 0x80), (0x0C, 0x01),
                     (0x0D, 0x01), (0xFF00, 0x01), (0x01, 0x04), (0x01, 0x05),
                     (0x01, 0x07), (0x01, 0x08), (0x0B, 0x01), (0x0E, 0x01)]

    def __init__(self, out_q, mac="AA:BB:CC:DD:EE:FF"):
        super().__init__(daemon=True)
        self.q = out_q
        self.mac_frag = mac.replace(":", "").lower()[:8]
        self.stop_flag = threading.Event()
        self.hwnd = None
        self.names = {}
        self._proc_ref = None
        self.last_key = None
        self.last_key_t = 0.0
        self.error = None

    # ---- 工具
    def emit(self, kind, **kw):
        self.q.put((kind, kw))

    def is_ours(self, name):
        low = (name or "").lower()
        if not low or low == "?":
            return False
        # VID/PID 命中即认为是遥控器
        if "620a" in low and "0407" in low:
            return True
        # MAC 片段只作辅助：留空时不能变成“匹配一切”
        return bool(self.mac_frag) and self.mac_frag in low

    def dev_name(self, h):
        if h in self.names:
            return self.names[h]
        name = "?"
        if not h:
            self.names[h] = "<injected>"
            return "<injected>"
        try:
            size = wintypes.UINT(0)
            r = user32.GetRawInputDeviceInfoW(h, self.RIDI_DEVICENAME, None,
                                              ctypes.byref(size))
            if r != 0xFFFFFFFF and 0 < size.value < 4096:
                buf = ctypes.create_unicode_buffer(size.value + 2)
                n = user32.GetRawInputDeviceInfoW(h, self.RIDI_DEVICENAME, buf,
                                                  ctypes.byref(size))
                if n not in (0, 0xFFFFFFFF):
                    name = buf.value
            else:
                cap = wintypes.UINT(1024)
                buf = ctypes.create_unicode_buffer(1024)
                n = user32.GetRawInputDeviceInfoW(h, self.RIDI_DEVICENAME, buf,
                                                  ctypes.byref(cap))
                if n not in (0, 0xFFFFFFFF) and buf.value:
                    name = buf.value
        except Exception:
            name = "?"
        self.names[h] = name
        return name

    # ---- 解析
    def on_input(self, lparam):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hdr_len = ctypes.sizeof(_RI_HEADER)
        size = wintypes.UINT(0)
        user32.GetRawInputData(lparam, self.RID_INPUT, None, ctypes.byref(size), hdr_len)
        if size.value == 0:
            return
        buf = ctypes.create_string_buffer(size.value)
        n = user32.GetRawInputData(lparam, self.RID_INPUT, buf, ctypes.byref(size), hdr_len)
        if n in (0, 0xFFFFFFFF):
            return
        ri = ctypes.cast(buf, ctypes.POINTER(_RI_RAWINPUT)).contents
        dtype = ri.header.dwType

        # 丢弃本程序自己合成的事件（带 MAGIC 标记），否则会「按键→合成→再抓→再合成」死循环
        if HAVE_KEYSYNTH:
            if dtype == self.RIM_KEYBOARD:
                if (ri.data.keyboard.ExtraInformation & 0xFFFFFFFF) == keysynth.MAGIC:
                    return
            elif dtype == self.RIM_MOUSE:
                if (ri.data.mouse.ulExtraInformation & 0xFFFFFFFF) == keysynth.MAGIC:
                    return

        name = self.dev_name(ri.header.hDevice)
        if not self.is_ours(name):
            return

        if dtype == self.RIM_KEYBOARD:
            k = ri.data.keyboard
            if k.VKey in (0, 0xFF) or (k.Flags & 0x01):
                return  # 只报按下
            self.push("key", k.VKey, k.MakeCode, "%02X" % k.MakeCode)

        elif dtype == self.RIM_HID:
            hd = ri.data.hid
            avail = max(0, size.value - hdr_len - 8)
            total = min(hd.dwCount * hd.dwSizeHid, avail)
            if total <= 0:
                return
            raw = ctypes.string_at(ctypes.addressof(ri.data) + 8, total)
            rid = raw[0]
            if rid == 0x02 and len(raw) >= 3:
                usage = raw[1] | (raw[2] << 8)
                if usage:
                    self.push("consumer", usage, rid, hx(raw[:3]))
            elif rid == 0x01 and len(raw) >= 4:
                # 键盘集合的 HID 报告（备用路径）
                vk = raw[2] if len(raw) > 2 else 0
                if vk:
                    self.push("key", vk, raw[3] if len(raw) > 3 else 0,
                              hx(raw[:8]))
            elif rid == 0x5A:
                self.emit("log", text="厂商报告 0x5A: %s" % hx(raw[:16]), level="mut")

        elif dtype == self.RIM_MOUSE:
            m = ri.data.mouse
            if m.lLastX or m.lLastY:
                self.emit("rawmove", dx=m.lLastX, dy=m.lLastY)

    def push(self, src, code, extra, raw_hex):
        # 同一键 300ms 内去重（HID 报告可能重复上报）
        now = time.time()
        sig = (src, code)
        if sig == self.last_key and now - self.last_key_t < 0.25:
            return
        self.last_key, self.last_key_t = sig, now
        key_id = ("K:0x%02X" % code) if src == "key" else ("C:0x%04X" % code)
        self.emit("rawkey", key_id=key_id, src=src, code=code,
                  hex=raw_hex, extra=extra)

    # ---- 窗口
    def _wndproc(self, hwnd, msg, wp, lp):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if msg == self.WM_INPUT:
            try:
                self.on_input(lp)
            except Exception as e:
                self.emit("log", text="RawInput 解析异常: %s" % e, level="err")
            return 0
        if msg == 0x0002:  # WM_DESTROY
            user32.PostQuitMessage(0)
            return 0
        try:
            return user32.DefWindowProcW(ctypes.c_void_p(hwnd), ctypes.c_uint(msg),
                                         ctypes.c_size_t(wp & 0xFFFFFFFFFFFFFFFF),
                                         ctypes.c_ssize_t(lp))
        except (ctypes.ArgumentError, OverflowError, TypeError):
            return 0

    def run(self):
        try:
            self._setup()
        except Exception as e:
            self.error = str(e)
            self.emit("hidfail", reason=str(e))
            return
        self.emit("hidok", msg="Raw Input 监听已启动（键盘/Consumer/厂商）")
        msg = wintypes.MSG()
        while not self.stop_flag.is_set():
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _setup(self):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.restype = ctypes.c_longlong
        user32.GetRawInputData.restype = wintypes.UINT
        user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                           ctypes.c_void_p,
                                           ctypes.POINTER(wintypes.UINT), wintypes.UINT]
        user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
        user32.GetRawInputDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                                  ctypes.c_void_p,
                                                  ctypes.POINTER(wintypes.UINT)]

        hinst = kernel32.GetModuleHandleW(None)
        cls = "T1GuiRawInput"
        self._proc_ref = WNDPROC(self._wndproc)
        wc = _RI_WNDCLASS()
        wc.style = 0x0001 | 0x0002
        wc.lpfnWndProc = ctypes.cast(self._proc_ref, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls
        if not user32.RegisterClassW(ctypes.byref(wc)):
            if ctypes.get_last_error() not in (0, 1410):
                raise OSError("RegisterClassW 失败 %d" % ctypes.get_last_error())
        self.hwnd = user32.CreateWindowExW(0, cls, "t1guiraw", 0x80000000,
                                           -3200, -3200, 8, 8, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW 失败 %d" % ctypes.get_last_error())
        # 关键：窗口必须 ShowWindow，隐藏窗口收不到 INPUTSINK 事件
        user32.ShowWindow(self.hwnd, 4)
        user32.UpdateWindow(self.hwnd)

        ok = 0
        for pg, us in self.USAGE_CLASSES:
            one = (_RI_DEVICE * 1)()
            one[0].usUsagePage = pg
            one[0].usUsage = us
            one[0].dwFlags = self.RIDEV_INPUTSINK
            one[0].hwndTarget = self.hwnd
            if user32.RegisterRawInputDevices(one, 1, ctypes.sizeof(_RI_DEVICE)):
                ok += 1
        if ok == 0:
            raise OSError("RegisterRawInputDevices 全部失败")

    def stop(self):
        self.stop_flag.set()
        try:
            user32.PostQuitMessage(0) if self.hwnd else None
            if self.hwnd:
                user32.PostMessageW(self.hwnd, 0x0010, 0, 0)
        except Exception:
            pass


class HidWatcher(threading.Thread):
    """hidapi 直读 Consumer 集合（Raw Input 不可用时的回退方案）"""

    def __init__(self, out_q, vid=0x620A, pid=0x0407):
        super().__init__(daemon=True)
        self.q = out_q
        self.vid, self.pid = vid, pid
        self.stop_flag = threading.Event()
        self.last_usage = -1

    def run(self):
        if not HAVE_HID:
            self.q.put(("hidfail", {"reason": "hidapi 未安装"}))
            return
        dev = None
        try:
            cands = [d for d in hid.enumerate(self.vid, self.pid) if d["usage_page"] == 0x0C]
            if not cands:
                self.q.put(("hidfail", {"reason": "未找到 Consumer 集合"}))
                return
            dev = hid.device()
            dev.open_path(cands[0]["path"])
            dev.set_nonblocking(True)
            self.q.put(("hidok", {"msg": "hidapi Consumer 监听已启动（回退模式）"}))
        except Exception as e:
            self.q.put(("hidfail", {"reason": "hidapi 打开失败: %s" % e}))
            return

        while not self.stop_flag.is_set():
            try:
                r = dev.read(64)
            except Exception:
                break
            if r:
                rid = r[0]
                if len(r) >= 3:
                    usage = (r[2] << 8) | r[1]
                elif len(r) >= 2:
                    usage = r[1]
                else:
                    usage = 0
                if usage == 0:
                    self.last_usage = -1
                elif usage != self.last_usage:
                    self.last_usage = usage
                    self.q.put(("rawkey", {"key_id": "C:0x%04X" % usage,
                                           "src": "consumer", "code": usage,
                                           "hex": hx(r[:3]), "extra": rid}))
            else:
                time.sleep(0.015)
        try:
            dev.close()
        except Exception:
            pass

    def stop(self):
        self.stop_flag.set()


# ---------------------------------------------------------------- 界面

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = self.load_cfg()
        self.q = queue.Queue()
        self.worker = None
        self.hid = None
        self.segments = {}
        self.wave = deque(maxlen=int(self.cfg.get("wave_frames", 320)))
        self.seg_bars = deque(maxlen=int(self.cfg.get("wave_frames", 320)))
        self.cur_seg = None
        self.connected = False
        self.played_path = None

        self.title(APP_TITLE)
        self.geometry("1120x740")
        self.minsize(980, 640)
        self.configure(bg=C_BG)
        self._init_style()
        self._build()
        self.after(60, self._pump)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.say("就绪。填 MAC 直连，或只填名称按名称查找后点「连接」。", "mut")
        if self.cfg.get("hid_listen", True):
            self.after(400, self.start_hid)

    # ---- 样式
    def _init_style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".", background=C_BG, foreground=C_TEXT, font=("Microsoft YaHei UI", 9))
        s.configure("TFrame", background=C_BG)
        s.configure("Card.TFrame", background=C_CARD, relief="flat")
        s.configure("TLabel", background=C_BG, foreground=C_TEXT)
        s.configure("Card.TLabel", background=C_CARD, foreground=C_TEXT)
        s.configure("Mut.TLabel", background=C_CARD, foreground=C_MUT)
        s.configure("H.TLabel", background=C_BG, foreground=C_TEXT,
                    font=("Microsoft YaHei UI", 13, "bold"))
        s.configure("Big.TLabel", background=C_CARD, foreground=C_ACC,
                    font=("Consolas", 20, "bold"))
        s.configure("TButton", padding=(10, 5))
        s.configure("Acc.TButton", padding=(14, 6))
        s.map("Acc.TButton", background=[("!disabled", C_ACC), ("active", C_ACC_D)],
              foreground=[("!disabled", "#ffffff")])
        s.configure("TNotebook", background=C_BG, borderwidth=0)
        s.configure("TNotebook.Tab", padding=(16, 7), font=("Microsoft YaHei UI", 9))
        s.map("TNotebook.Tab", background=[("selected", C_CARD)],
              foreground=[("selected", C_ACC)])
        s.configure("Treeview", rowheight=24, fieldbackground=C_CARD, background=C_CARD)
        s.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        s.configure("TCheckbutton", background=C_CARD)
        s.configure("TLabelframe", background=C_CARD, borderwidth=1, relief="solid")
        s.configure("TLabelframe.Label", background=C_CARD, foreground=C_MUT,
                    font=("Microsoft YaHei UI", 9, "bold"))

    # ---- 顶栏
    def _build(self):
        head = tk.Frame(self, bg=C_CARD, highlightthickness=1,
                        highlightbackground=C_EDGE)
        head.pack(fill="x", side="top")
        inner = tk.Frame(head, bg=C_CARD)
        inner.pack(fill="x", padx=16, pady=10)

        tk.Label(inner, text="T1-Remote 上位机", bg=C_CARD, fg=C_TEXT,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        tk.Label(inner, text="ATVV over BLE · 语音录制 / 按键映射", bg=C_CARD, fg=C_MUT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(10, 24))

        self.btn_conn = ttk.Button(inner, text="连接", style="Acc.TButton",
                                   command=self.toggle_connect, width=8)
        self.btn_conn.pack(side="right", padx=(8, 0))

        self.mac_var = tk.StringVar(value=self.cfg["mac"])
        e = tk.Entry(inner, textvariable=self.mac_var, width=20, font=("Consolas", 10),
                     relief="solid", bd=1, highlightthickness=0)
        e.pack(side="right", padx=(8, 0), ipady=3)
        tk.Label(inner, text="MAC", bg=C_CARD, fg=C_MUT).pack(side="right")

        self.dot = tk.Canvas(inner, width=12, height=12, bg=C_CARD, highlightthickness=0)
        self.dot.pack(side="right", padx=(14, 6))
        self._dot_item = self.dot.create_oval(2, 2, 10, 10, fill="#c3cad4", outline="")
        self.state_lbl = tk.Label(inner, text="未连接", bg=C_CARD, fg=C_MUT,
                                  font=("Microsoft YaHei UI", 9))
        self.state_lbl.pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(10, 6))
        self.tab_rec = ttk.Frame(self.nb, style="Card.TFrame")
        self.tab_key = ttk.Frame(self.nb, style="Card.TFrame")
        self.tab_cfg = ttk.Frame(self.nb, style="Card.TFrame")
        self.tab_log = ttk.Frame(self.nb, style="Card.TFrame")
        self.nb.add(self.tab_rec, text="  语音录制  ")
        self.nb.add(self.tab_key, text="  按键映射  ")
        self.nb.add(self.tab_cfg, text="  配置  ")
        self.nb.add(self.tab_log, text="  日志  ")

        self._build_rec()
        self._build_key()
        self._build_cfg()
        self._build_log()

        bar = tk.Frame(self, bg=C_BG)
        bar.pack(fill="x", side="bottom", padx=14, pady=(0, 8))
        self.stat = tk.Label(bar, text="", bg=C_BG, fg=C_MUT, anchor="w",
                             font=("Consolas", 9))
        self.stat.pack(side="left")

    # ---- 录音页
    def _build_rec(self):
        f = self.tab_rec
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

        top = tk.Frame(f, bg=C_CARD)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        self.btn_listen = ttk.Button(top, text="开始监听", command=self.toggle_listen,
                                     state="disabled")
        self.btn_listen.pack(side="left")

        self.btn_caps = ttk.Button(top, text="查询能力", command=self.ask_caps,
                                   state="disabled")
        self.btn_caps.pack(side="left", padx=6)

        self.lbl_big = tk.Label(top, text="0.00 s", bg=C_CARD, fg=C_ACC,
                                font=("Consolas", 22, "bold"))
        self.lbl_big.pack(side="left", padx=(22, 4))
        tk.Label(top, text="本段时长", bg=C_CARD, fg=C_MUT).pack(side="left", padx=(0, 22))

        self.lbl_meta = tk.Label(top, text="等待连接", bg=C_CARD, fg=C_MUT,
                                 font=("Consolas", 9), justify="left")
        self.lbl_meta.pack(side="left")

        # 设备信息
        self.info_lbl = tk.Label(top, text="", bg=C_CARD, fg=C_MUT,
                                 font=("Consolas", 9), justify="right")
        self.info_lbl.pack(side="right")

        # 波形
        wave_box = tk.Frame(f, bg=C_CARD)
        wave_box.grid(row=1, column=0, sticky="ew", padx=14)
        self.cv = tk.Canvas(wave_box, height=132, bg=C_WAVE_BG, highlightthickness=0)
        self.cv.pack(fill="x")
        self.cv.bind("<Configure>", lambda e: self.draw_wave())
        self.cv.create_text(14, 12, text="实时波形（未开始）", fill="#3d4d63",
                            anchor="nw", font=("Consolas", 9), tags="hint")

        # 段列表
        mid = tk.Frame(f, bg=C_CARD)
        mid.grid(row=2, column=0, sticky="nsew", padx=14, pady=(10, 4))
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)

        cols = ("id", "time", "dur", "frames", "bytes", "rms", "reason", "file")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=8)
        heads = {"id": ("#", 44), "time": ("开始时间", 96), "dur": ("时长", 78),
                 "frames": ("帧", 60), "bytes": ("字节", 76), "rms": ("RMS", 66),
                 "reason": ("结束原因", 110), "file": ("文件", 320)}
        for c in cols:
            t = heads[c]
            self.tree.heading(c, text=t[0])
            self.tree.column(c, width=t[1], anchor="w" if c in ("reason", "file") else "center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        vs.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.bind("<Double-1>", lambda e: self.play_selected())

        bar = tk.Frame(f, bg=C_CARD)
        bar.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))
        ttk.Button(bar, text="播放", command=self.play_selected).pack(side="left")
        ttk.Button(bar, text="导出 WAV", command=self.export_wav).pack(side="left", padx=6)
        ttk.Button(bar, text="导出 ADPCM", command=self.export_raw).pack(side="left")
        ttk.Button(bar, text="打开目录", command=self.open_outdir).pack(side="left", padx=6)
        ttk.Button(bar, text="删除", command=self.del_selected).pack(side="left")
        self.lbl_sel = tk.Label(bar, text="", bg=C_CARD, fg=C_MUT, font=("Consolas", 9))
        self.lbl_sel.pack(side="right")

    # ---- 按键页
    def _build_key(self):
        f = self.tab_key
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)

        top = tk.Frame(f, bg=C_CARD)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        ttk.Button(top, text="开始学习", command=self.toggle_hid).pack(side="left")
        ttk.Button(top, text="改动作", command=self.edit_action).pack(side="left", padx=6)
        ttk.Button(top, text="删除条目", command=self.del_key).pack(side="left")
        ttk.Button(top, text="保存映射", command=self.save_cfg).pack(side="left", padx=6)
        self.lbl_key = tk.Label(top, text="点「开始学习」后按遥控器按键，自动记录",
                                bg=C_CARD, fg=C_MUT, font=("Microsoft YaHei UI", 9))
        self.lbl_key.pack(side="right")

        mid = tk.Frame(f, bg=C_CARD)
        mid.grid(row=1, column=0, sticky="nsew", padx=14)
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        cols = ("key", "src", "label", "action", "count")
        self.ktree = ttk.Treeview(mid, columns=cols, show="headings", height=12)
        for c, t, w in (("key", "键标识", 130), ("src", "来源通道", 150),
                        ("label", "名称", 190), ("action", "绑定动作", 330),
                        ("count", "次数", 70)):
            self.ktree.heading(c, text=t)
            self.ktree.column(c, width=w, anchor="w" if c in ("label", "action") else "center")
        self.ktree.grid(row=0, column=0, sticky="nsew")
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.ktree.yview)
        vs.grid(row=0, column=1, sticky="ns")
        self.ktree.configure(yscrollcommand=vs.set)
        self.ktree.bind("<Double-1>", lambda e: self.edit_action())

        tip = tk.Label(f, text="键标识：K:0xNN = 键盘集合 COL01（方向键 上/下/左/右 = 0x26/0x28/0x25/0x27，OK = 0x0D）；"
                               "C:0xNNNN = Consumer 集合 COL02（语音 0x0221、主页 0x0223、返回 0x0224、音量 0xE9/0xEA、静音 0xE2）。"
                               "动作四类：key:ctrl+alt+p（合成按键）、text:要键入的字、open:recordings（打开）、cmd:notepad（执行）；"
                               "双击行打开编辑器，可选预设、可试运行，留空仅记录。",
                       bg=C_CARD, fg=C_MUT, justify="left", anchor="w", wraplength=1040,
                       font=("Microsoft YaHei UI", 9))
        tip.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 10))

        self.key_counts = {}
        self.refresh_keytree()

    # ---- 配置页
    def _build_cfg(self):
        f = self.tab_cfg
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        # 左：设备与音频
        g1 = ttk.Labelframe(f, text=" 设备与音频参数 ")
        g1.grid(row=0, column=0, sticky="nsew", padx=(14, 8), pady=(14, 8))
        g1.columnconfigure(1, weight=1)

        self.v = {}
        rows = [
            ("mac", "设备 MAC", "entry"),
            ("outdir", "输出目录", "dir"),
            ("skip_bytes", "段首跳过字节", "spin", 0, 16),
            ("hpf_hz", "高通截止 (Hz)", "spin", 0, 400),
            ("mic_extend_ms", "MIC_EXTEND 周期 (ms)", "spin", 500, 5000),
            ("max_segment_s", "单段最长 (s, 0=不限)", "spin", 0, 300),
            ("wave_frames", "波形显示帧数", "spin", 60, 1200),
        ]
        for i, row in enumerate(rows):
            key, label = row[0], row[1]
            tk.Label(g1, text=label, bg=C_CARD, fg=C_MUT).grid(
                row=i, column=0, sticky="w", padx=(12, 8), pady=5)
            var = tk.StringVar(value=str(self.cfg.get(key, "")))
            self.v[key] = var
            if row[2] == "entry":
                w = tk.Entry(g1, textvariable=var, font=("Consolas", 10), relief="solid", bd=1)
                w.grid(row=i, column=1, sticky="ew", padx=(0, 6), pady=5, ipady=2)
            elif row[2] == "dir":
                box = tk.Frame(g1, bg=C_CARD)
                box.grid(row=i, column=1, sticky="ew", padx=(0, 6), pady=5)
                box.columnconfigure(0, weight=1)
                tk.Entry(box, textvariable=var, font=("Consolas", 9), relief="solid",
                         bd=1).grid(row=0, column=0, sticky="ew", ipady=2)
                ttk.Button(box, text="浏览", width=5,
                           command=self.browse_dir).grid(row=0, column=1, padx=(4, 0))
            else:
                tk.Spinbox(g1, textvariable=var, from_=row[3], to=row[4], width=10,
                           font=("Consolas", 10), relief="solid", bd=1,
                           justify="right").grid(row=i, column=1, sticky="w", pady=5, ipady=2)

        y = len(rows)
        checks = [("auto_mic_open", "收到语音键立即 MIC_OPEN"),
                  ("voice_input", "松开语音键后识别并键入到当前对话框"),
                  ("auto_save", "录音结束自动保存 WAV"),
                  ("normalize", "导出时归一化峰值"),
                  ("hid_listen", "连接后自动开始按键学习"),
                  ("run_key", "允许按键动作合成按键/键字（key: / text:）"),
                  ("run_cmd", "允许按 action 执行命令（cmd: / open:）")]
        for j, (key, label) in enumerate(checks):
            var = tk.BooleanVar(value=bool(self.cfg.get(key, False)))
            self.v[key] = var
            ttk.Checkbutton(g1, text=label, variable=var).grid(
                row=y + j, column=0, columnspan=2, sticky="w", padx=12, pady=2)

        # 右：状态 + 操作
        g2 = ttk.Labelframe(f, text=" 运行时状态 ")
        g2.grid(row=0, column=1, sticky="nsew", padx=(8, 14), pady=(14, 8))
        g2.columnconfigure(0, weight=1)
        self.caps_txt = tk.Text(g2, height=13, bg="#fafbfc", fg=C_TEXT, relief="flat",
                                font=("Consolas", 9), wrap="word", state="disabled")
        self.caps_txt.grid(row=0, column=0, sticky="nsew", padx=10, pady=8)

        g3 = ttk.Labelframe(f, text=" 配置读写 ")
        g3.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 14))
        for txt, fn in (("保存配置", self.save_cfg), ("恢复默认", self.reset_cfg),
                        ("导出 JSON", self.export_cfg), ("导入 JSON", self.import_cfg),
                        ("打开配置文件", self.open_cfg_file)):
            ttk.Button(g3, text=txt, command=fn).pack(side="left", padx=(10, 0), pady=10)
        tk.Label(g3, text=CFG_PATH, bg=C_CARD, fg=C_MUT,
                 font=("Consolas", 8)).pack(side="right", padx=10)

    # ---- 日志页
    def _build_log(self):
        f = self.tab_log
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        bar = tk.Frame(f, bg=C_CARD)
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ttk.Button(bar, text="清空", command=self.clear_log).pack(side="left")
        ttk.Button(bar, text="保存日志", command=self.save_log).pack(side="left", padx=6)
        ttk.Button(bar, text="复制全部", command=self.copy_log).pack(side="left")

        self.log_txt = tk.Text(f, bg="#101722", fg="#c9d6e4", relief="flat",
                               font=("Consolas", 9), wrap="none", insertbackground="#c9d6e4")
        self.log_txt.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
        vs = ttk.Scrollbar(f, orient="vertical", command=self.log_txt.yview)
        vs.grid(row=1, column=1, sticky="ns", padx=(0, 14), pady=(0, 12))
        self.log_txt.configure(yscrollcommand=vs.set)
        self.log_txt.tag_configure("info", foreground="#c9d6e4")
        self.log_txt.tag_configure("ok", foreground="#5fd68a")
        self.log_txt.tag_configure("warn", foreground="#e8b23a")
        self.log_txt.tag_configure("err", foreground="#ff6b5e")
        self.log_txt.tag_configure("mut", foreground="#6b7c91")

    # ------------------------------------------------------------ 配置 IO
    def load_cfg(self):
        cfg = json.loads(json.dumps(CFG_DEFAULT))
        if os.path.exists(CFG_PATH):
            try:
                with open(CFG_PATH, "r", encoding="utf-8") as f:
                    cfg.update(json.load(f))
            except Exception:
                pass
        if not os.path.isabs(cfg.get("outdir", "")):
            cfg["outdir"] = os.path.join(BASE_DIR, cfg.get("outdir", "recordings"))
        # 旧配置迁移：纯 "0x0221" 视为 Consumer 通道
        km = {}
        for k, v in (cfg.get("keymap") or {}).items():
            kk = k if ":" in str(k) else "C:" + str(k)
            km[kk] = v
        cfg["keymap"] = km
        return cfg

    def collect_cfg(self):
        c = dict(self.cfg)
        for k, var in self.v.items():
            try:
                val = var.get()
            except Exception:
                continue
            if isinstance(var, tk.BooleanVar):
                c[k] = bool(val)
            elif k in ("skip_bytes", "mic_extend_ms", "max_segment_s", "wave_frames"):
                try:
                    c[k] = int(float(val))
                except Exception:
                    pass
            elif k == "hpf_hz":
                try:
                    c[k] = float(val)
                except Exception:
                    pass
            else:
                c[k] = str(val).strip()
        c["keymap"] = self.cfg.get("keymap", {})
        return c

    def save_cfg(self):
        self.cfg = self.collect_cfg()
        try:
            os.makedirs(self.cfg["outdir"], exist_ok=True)
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            self.say("配置已保存 → %s" % CFG_PATH, "ok")
            self.wave = deque(maxlen=int(self.cfg.get("wave_frames", 320)))
        except Exception as e:
            self.say("保存配置失败: %s" % e, "err")

    def reset_cfg(self):
        if not messagebox.askyesno("恢复默认", "将丢弃当前配置并恢复默认值，继续？"):
            return
        self.cfg = json.loads(json.dumps(CFG_DEFAULT))
        for k, var in self.v.items():
            if isinstance(var, tk.BooleanVar):
                var.set(bool(self.cfg.get(k, False)))
            else:
                var.set(str(self.cfg.get(k, "")))
        self.refresh_keytree()
        self.say("已恢复默认配置（尚未写入文件）", "warn")

    def export_cfg(self):
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         initialfile="t1_config.json",
                                         filetypes=[("JSON", "*.json")])
        if not p:
            return
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.collect_cfg(), f, ensure_ascii=False, indent=2)
        self.say("配置已导出 → %s" % p, "ok")

    def import_cfg(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.cfg.update(data)
            for k, var in self.v.items():
                if k in data:
                    var.set(bool(data[k]) if isinstance(var, tk.BooleanVar) else str(data[k]))
            self.refresh_keytree()
            self.say("已导入 %s" % p, "ok")
        except Exception as e:
            self.say("导入失败: %s" % e, "err")

    def open_cfg_file(self):
        if not os.path.exists(CFG_PATH):
            self.save_cfg()
        try:
            os.startfile(CFG_PATH)
        except Exception:
            self.say("无法打开配置文件", "warn")

    def browse_dir(self):
        p = filedialog.askdirectory(initialdir=self.v["outdir"].get() or BASE_DIR)
        if p:
            self.v["outdir"].set(p)

    # ------------------------------------------------------------ 连接
    def toggle_connect(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.btn_conn.configure(text="连接中…", state="disabled")
            self.after(900, self._force_idle)
            return
        self.cfg["mac"] = self.mac_var.get().strip().upper()
        self.worker = BleWorker(self.q, self.cfg)
        self.worker.start()
        self.btn_conn.configure(text="断开", state="normal")
        if self.cfg.get("hid_listen"):
            self.start_hid()

    def _force_idle(self):
        if self.worker and not self.worker.is_alive():
            self.btn_conn.configure(text="连接", state="normal")
            self.set_state("idle", "未连接")

    def ask_caps(self):
        if self.worker:
            self.worker.call(self.worker.cmd_get_caps)

    def toggle_listen(self):
        if not self.worker:
            return
        self.worker.listening = not self.worker.listening
        self.btn_listen.configure(
            text="停止监听" if self.worker.listening else "开始监听")
        self.say("监听" + ("开启" if self.worker.listening else "暂停"),
                 "ok" if self.worker.listening else "warn")

    # ------------------------------------------------------------ HID
    def toggle_hid(self):
        if self.hid and self.hid.is_alive():
            self.hid.stop()
            self.hid = None
            self.lbl_key.configure(text="按键学习已停止", fg=C_MUT)
        else:
            self.start_hid()

    def start_hid(self):
        if self.hid and self.hid.is_alive():
            return
        self.hid = RawInputWatcher(self.q, self.cfg.get("mac", "AA:BB:CC:DD:EE:FF"))
        self.hid.start()
        self.lbl_key.configure(text="学习中… 按遥控器任意键", fg=C_OK)

    def refresh_keytree(self):
        for i in self.ktree.get_children():
            self.ktree.delete(i)
        km = self.cfg.get("keymap", {})

        def sort_key(k):
            src, code = k.split(":", 1)
            try:
                return (0 if src == "K" else 1, int(code, 16))
            except Exception:
                return (2, 0)

        for k in sorted(km, key=sort_key):
            item = km[k]
            src = ("键盘集合 COL01" if k.startswith("K:") else
                   "Consumer COL02" if k.startswith("C:") else "其他")
            self.ktree.insert("", "end", iid=k, values=(
                k, src, item.get("label", ""), item.get("action", ""),
                self.key_counts.get(k, 0)))

    def on_rawkey(self, key_id, src, code, hxstr):
        """键盘集合与 Consumer 集合统一的按键处理入口"""
        self.key_counts[key_id] = self.key_counts.get(key_id, 0) + 1
        km = self.cfg.setdefault("keymap", {})
        if src == "key":
            name = VK_NAME.get(code, "VK 0x%02X" % code)
        else:
            name = USAGE_NAME.get(code, "Usage 0x%04X" % code)

        if key_id not in km:
            km[key_id] = {"label": name, "action": ""}
            self.say("新按键 %s  %s  [%s]" % (key_id, name, hxstr), "ok")
        else:
            self.say("按键 %s  %s  [%s]" % (key_id, km[key_id].get("label") or name, hxstr))
        self.lbl_key.configure(text="最近: %s  %s" % (key_id, km[key_id].get("label") or name),
                               fg=C_OK)
        self.refresh_keytree()

        act = (km[key_id].get("action") or "").strip()
        if act:
            threading.Thread(target=self.exec_action, args=(act, key_id),
                             daemon=True).start()

    # ------------------------------------------------------------ 动作执行
    def exec_action(self, act, key_id=""):
        """动作分发：key: / text: / open: / cmd:

        合成按键放在后台线程，避免 SendInput 的短 sleep 卡住界面重绘。
        """
        act = (act or "").strip()
        if not act:
            return
        kind, _, arg = act.partition(":")
        kind = kind.strip().lower()
        arg = arg.strip()
        tag = " [%s]" % key_id if key_id else ""

        if kind == "key":
            if not self.cfg.get("run_key", True):
                self.q.put(("log", {"text": "已拦截%s：配置页未开启「允许合成按键」%s"
                                    % (tag, act), "level": "warn"}))
                return
            if not HAVE_KEYSYNTH:
                self.q.put(("log", {"text": "keysynth 不可用，跳过 %s" % act, "level": "err"}))
                return
            try:
                names = keysynth.send_hotkey(arg)
                self.q.put(("log", {"text": "合成按键%s → %s" % (tag, "+".join(names)), "level": "ok"}))
            except Exception as e:
                self.q.put(("log", {"text": "合成失败%s: %s" % (tag, e), "level": "err"}))

        elif kind == "text":
            if not self.cfg.get("run_key", True):
                self.q.put(("log", {"text": "已拦截%s：未开启「允许合成按键」" % tag, "level": "warn"}))
                return
            if not HAVE_KEYSYNTH:
                return
            try:
                keysynth.send_text(arg)
                self.q.put(("log", {"text": "键入%s: %s" % (tag, arg[:40]), "level": "ok"}))
            except Exception as e:
                self.q.put(("log", {"text": "键入失败%s: %s" % (tag, e), "level": "err"}))

        elif kind == "open":
            if not self.cfg.get("run_cmd", False):
                self.q.put(("log", {"text": "已拦截%s：未开启「允许执行命令」" % tag, "level": "warn"}))
                return
            target = arg
            # 相对路径按输出目录解析，方便 open:recordings
            if target and not os.path.isabs(target) and "://" not in target:
                cand = os.path.join(self.cfg.get("outdir") or BASE_DIR, target)
                if os.path.exists(cand):
                    target = cand
            try:
                os.startfile(target)
                self.q.put(("log", {"text": "打开%s: %s" % (tag, target), "level": "ok"}))
            except Exception as e:
                self.q.put(("log", {"text": "打开失败%s: %s" % (tag, e), "level": "err"}))

        elif kind == "cmd":
            if not self.cfg.get("run_cmd", False):
                self.q.put(("log", {"text": "已拦截%s：未开启「允许执行命令」" % tag, "level": "warn"}))
                return
            try:
                subprocess.Popen(arg, shell=True)
                self.q.put(("log", {"text": "执行%s: %s" % (tag, arg), "level": "ok"}))
            except Exception as e:
                self.q.put(("log", {"text": "执行失败%s: %s" % (tag, e), "level": "err"}))

        else:
            self.q.put(("log", {"text": "未知动作类型%s: %s" % (tag, act), "level": "warn"}))

    def edit_action(self):
        sel = self.ktree.selection()
        if not sel:
            messagebox.showinfo("提示", "先选中一行")
            return
        key_id = sel[0]
        vals = self.ktree.item(sel[0], "values")
        label, action = vals[2], vals[3]
        win = tk.Toplevel(self)
        win.title("编辑按键 %s" % key_id)
        win.configure(bg=C_CARD)
        win.geometry("600x318")
        win.transient(self)

        tk.Label(win, text="键标识", bg=C_CARD, fg=C_MUT).grid(
            row=0, column=0, sticky="w", padx=14, pady=8)
        tk.Label(win, text="%s   (%s)" % (key_id, vals[1]), bg=C_CARD, fg=C_TEXT,
                 font=("Consolas", 10)).grid(row=0, column=1, sticky="w")
        tk.Label(win, text="名称", bg=C_CARD, fg=C_MUT).grid(
            row=1, column=0, sticky="w", padx=14, pady=6)
        lv = tk.StringVar(value=label)
        tk.Entry(win, textvariable=lv, width=44, relief="solid", bd=1).grid(
            row=1, column=1, sticky="w", ipady=3)

        tk.Label(win, text="动作", bg=C_CARD, fg=C_MUT).grid(
            row=2, column=0, sticky="w", padx=14, pady=6)
        av = tk.StringVar(value=action)
        tk.Entry(win, textvariable=av, width=44, relief="solid", bd=1,
                 font=("Consolas", 9)).grid(row=2, column=1, sticky="w", ipady=3)

        tk.Label(win, text="预设", bg=C_CARD, fg=C_MUT).grid(
            row=3, column=0, sticky="w", padx=14, pady=6)
        pv = tk.StringVar(value="（选择预设动作自动填入）")
        presets = keysynth.PRESETS if HAVE_KEYSYNTH else [("（keysynth 未加载）", "")]
        cb = ttk.Combobox(win, textvariable=pv, width=41, state="readonly",
                          values=[p[0] for p in presets])
        cb.grid(row=3, column=1, sticky="w")

        def on_preset(_e=None):
            idx = cb.current()
            if 0 <= idx < len(presets):
                av.set(presets[idx][1])

        cb.bind("<<ComboboxSelected>>", on_preset)

        hint = tk.Label(win, text="key:ctrl+alt+p   text:要键入的文字   open:recordings   cmd:notepad\n"
                                  "留空 = 仅记录不执行。key:/text: 需开启「允许合成按键」，"
                                  "open:/cmd: 需开启「允许执行命令」。",
                        bg=C_CARD, fg=C_MUT, justify="left", anchor="w",
                        wraplength=560, font=("Microsoft YaHei UI", 8))
        hint.grid(row=4, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 0))

        tstat = tk.Label(win, text="", bg=C_CARD, fg=C_MUT, anchor="w",
                         font=("Microsoft YaHei UI", 8))
        tstat.grid(row=5, column=0, columnspan=2, sticky="ew", padx=14)

        def preview(*_):
            txt = av.get().strip()
            desc = keysynth.describe(txt) if (HAVE_KEYSYNTH and txt) else (
                "无动作（仅记录）" if not txt else txt)
            tstat.configure(text="解析：" + desc)

        av.trace_add("write", preview)
        preview()

        def test_run():
            txt = av.get().strip()
            if not txt:
                messagebox.showinfo("提示", "动作是空的")
                return
            self.exec_action(txt, key_id)
            tstat.configure(text="已试运行：" + keysynth.describe(txt)
                            if HAVE_KEYSYNTH else "已试运行")

        def ok():
            self.cfg.setdefault("keymap", {})[key_id] = {
                "label": lv.get().strip() or "未命名", "action": av.get().strip()}
            self.refresh_keytree()
            win.destroy()
            self.say("已更新 %s" % key_id, "ok")

        bf = tk.Frame(win, bg=C_CARD)
        bf.grid(row=6, column=0, columnspan=2, pady=14)
        ttk.Button(bf, text="保存", command=ok).pack(side="left", padx=6)
        ttk.Button(bf, text="试运行", command=test_run).pack(side="left", padx=6)
        ttk.Button(bf, text="取消", command=win.destroy).pack(side="left")

    def del_key(self):
        sel = self.ktree.selection()
        if not sel:
            return
        for key_id in sel:
            self.cfg.get("keymap", {}).pop(key_id, None)
        self.refresh_keytree()
        self.say("已删除选中条目", "warn")

    # ------------------------------------------------------------ 段操作
    def on_seg_end(self, ev):
        sid = ev["id"]
        self.segments[sid] = ev
        stamp = datetime.datetime.fromtimestamp(ev["t0"]).strftime("%H:%M:%S")
        self.tree.insert("", "end", iid=str(sid), values=(
            sid, stamp, "%.2fs" % ev["dur"], ev["frames"], ev["nbytes"],
            "…", ev["reason"], ""))
        self.tree.see(str(sid))
        # 解码 + 落盘放到后台线程，避免长段卡界面
        threading.Thread(target=self._finalize_segment, args=(sid,), daemon=True).start()

    def _finalize_segment(self, sid):
        ev = self.segments.get(sid)
        if not ev:
            return
        try:
            pcm, sr = self.seg_pcm(sid, use_cache=False)
            rms = math.sqrt(sum(v * v for v in pcm) / len(pcm)) if pcm else 0.0
            ev["pcm"] = pcm
            ev["rms"] = rms
            path = ""
            if self.cfg.get("auto_save") and ev["frames"] >= 4:
                path = self.write_files(sid)
            self.q.put(("seg_ready", {"id": sid, "rms": rms, "path": path,
                                      "dur": ev["dur"]}))
        except Exception as e:
            self.q.put(("log", {"text": "段解码失败: %s" % e, "level": "err"}))

    def seg_pcm(self, sid, use_cache=True):
        ev = self.segments.get(sid)
        if not ev:
            return None, DEFAULT_SR
        if use_cache and ev.get("pcm"):
            return ev["pcm"], ev["sr"]
        pcm = codec.decode_stream(ev["raw"], skip=int(self.cfg.get("skip_bytes", 4)),
                                  hp=float(self.cfg.get("hpf_hz", 120.0)),
                                  sr=ev["sr"], norm=bool(self.cfg.get("normalize", True)))
        return pcm, ev["sr"]

    def write_files(self, sid):
        """把一段写成 .adpcm + .wav，返回 wav 路径"""
        ev = self.segments[sid]
        outdir = self.cfg.get("outdir") or os.path.join(BASE_DIR, "recordings")
        os.makedirs(outdir, exist_ok=True)
        stamp = datetime.datetime.fromtimestamp(ev["t0"]).strftime("%Y%m%d_%H%M%S")
        base = "voice_%s_%02d" % (stamp, sid)
        with open(os.path.join(outdir, base + ".adpcm"), "wb") as f:
            f.write(ev["raw"])
        pcm, sr = self.seg_pcm(sid)
        wav = os.path.join(outdir, base + ".wav")
        codec.write_wav(wav, pcm, sr)
        ev["file"] = wav
        # 控制内存：保留最近 20 段的 PCM 缓存
        if len([s for s in self.segments.values() if s.get("pcm")]) > 20:
            for k in sorted(self.segments)[:-20]:
                self.segments[k].pop("pcm", None)
        return wav

    def save_segment(self, sid):
        return self.write_files(sid)

    def _selected_sid(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一段录音")
            return None
        return int(sel[0])

    def play_selected(self):
        sid = self._selected_sid()
        if sid is None:
            return
        try:
            path = self.segments[sid].get("file") or self.save_segment(sid)
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            self.say("播放 #%d  %s" % (sid, os.path.basename(path)), "ok")
        except Exception as e:
            self.say("播放失败: %s" % e, "err")

    def export_wav(self):
        sid = self._selected_sid()
        if sid is None:
            return
        p = filedialog.asksaveasfilename(defaultextension=".wav",
                                         initialfile="voice_%02d.wav" % sid,
                                         filetypes=[("WAV", "*.wav")])
        if not p:
            return
        pcm, sr = self.seg_pcm(sid)
        codec.write_wav(p, pcm, sr)
        self.say("已导出 WAV → %s" % p, "ok")

    def export_raw(self):
        sid = self._selected_sid()
        if sid is None:
            return
        p = filedialog.asksaveasfilename(defaultextension=".adpcm",
                                         initialfile="voice_%02d.adpcm" % sid,
                                         filetypes=[("ADPCM", "*.adpcm"), ("BIN", "*.bin")])
        if not p:
            return
        with open(p, "wb") as f:
            f.write(self.segments[sid]["raw"])
        self.say("已导出 ADPCM → %s" % p, "ok")

    def del_selected(self):
        sid = self._selected_sid()
        if sid is None:
            return
        self.segments.pop(sid, None)
        self.tree.delete(str(sid))
        self.say("已从列表移除 #%d（磁盘文件未删除）" % sid, "warn")

    def open_outdir(self):
        outdir = self.cfg.get("outdir") or os.path.join(BASE_DIR, "recordings")
        os.makedirs(outdir, exist_ok=True)
        try:
            os.startfile(outdir)
        except Exception as e:
            self.say("无法打开目录: %s" % e, "warn")

    # ------------------------------------------------------------ 波形
    def push_bar(self, rms):
        self.seg_bars.append(rms)

    def draw_wave(self):
        cv = self.cv
        cv.delete("bar")
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 20:
            return
        n = int(self.cfg.get("wave_frames", 320))
        bars = list(self.seg_bars)[-n:]
        if not bars:
            return
        cv.delete("hint")
        step = max(1.0, w / float(n))
        peak = max(max(bars), 60.0)
        base = h - 6
        span = h - 16
        x = w
        for v in reversed(bars):
            hh = min(1.0, v / peak) * span
            x -= step
            if x < -step:
                break
            if hh < 1.2:
                cv.create_line(x, base - 1, x + step * 0.7, base - 1,
                               fill=C_WAVE_PEAK, tags="bar")
            else:
                cv.create_rectangle(x, base - hh, x + step * 0.72, base,
                                    fill=C_WAVE_BAR, outline="", tags="bar")
        cv.create_text(12, 10, text="%d 帧/窗口  峰值 RMS %.0f" % (len(bars), peak),
                       fill="#4a5d75", anchor="nw", font=("Consolas", 8), tags="bar")

    # ------------------------------------------------------------ 状态/日志
    def set_state(self, value, text):
        color = {"connected": C_OK, "connecting": C_WARN,
                 "error": C_ERR}.get(value, "#c3cad4")
        self.dot.itemconfigure(self._dot_item, fill=color)
        self.state_lbl.configure(text=text)
        self.connected = value == "connected"
        st = "normal" if self.connected else "disabled"
        self.btn_listen.configure(state=st)
        self.btn_caps.configure(state=st)
        if self.connected:
            self.btn_listen.configure(text="停止监听")

    def set_caps(self, ev):
        lines = ["版本        : %s" % ev.get("version", "-"),
                 "编解码      : %s  (0x02 = IMA ADPCM)" % ev.get("codecs", "-"),
                 "交互模型    : %s  (0x01=按需 0x02=PTT 0x03=HTT)" % ev.get("model", "-"),
                 "帧长        : %s 字节" % ev.get("frame", "-"),
                 "采样率      : %d Hz" % ev.get("sr", 0),
                 "流内扩展    : %s" % ev.get("ext", "-"),
                 "",
                 "比特率      : %.1f kbps  (帧长 × 62.5 帧/秒 × 8)"
                 % (float(ev.get("frame") or 0) * 62.5 * 8 / 1000.0)]
        self.caps_txt.configure(state="normal")
        self.caps_txt.delete("1.0", "end")
        self.caps_txt.insert("1.0", "\n".join(lines))
        self.caps_txt.configure(state="disabled")

    def set_info(self, key, value):
        cur = getattr(self, "_infos", {})
        cur[key] = value
        self._infos = cur
        self.info_lbl.configure(text="   ".join("%s %s" % (k, v) for k, v in cur.items()))

    def say(self, text, level="info"):
        line = "[%s] %s\n" % (now_str(), text)
        self.log_txt.insert("end", line, level)
        self.log_txt.see("end")
        if level in ("warn", "err", "ok"):
            self.stat.configure(text=text, fg={"warn": C_WARN, "err": C_ERR,
                                               "ok": C_OK}.get(level, C_MUT))

    def clear_log(self):
        self.log_txt.delete("1.0", "end")

    def save_log(self):
        p = filedialog.asksaveasfilename(defaultextension=".log",
                                         initialfile="t1_ui.log",
                                         filetypes=[("LOG", "*.log"), ("TXT", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.log_txt.get("1.0", "end"))
            self.say("日志已保存 → %s" % p, "ok")

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log_txt.get("1.0", "end"))
        self.say("日志已复制到剪贴板", "ok")

    # ------------------------------------------------------------ 事件泵
    def _pump(self):
        dirty = False
        for _ in range(400):
            try:
                kind, ev = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.say(ev["text"], ev.get("level", "info"))
            elif kind == "state":
                self.set_state(ev["value"], ev["text"])
                if ev["value"] in ("idle", "error"):
                    self.btn_conn.configure(text="连接", state="normal")
            elif kind == "listening":
                self.btn_listen.configure(
                    text="停止监听" if ev["value"] else "开始监听")
            elif kind == "audio":
                self.push_bar(ev["rms"])
                dirty = True
                self.lbl_big.configure(text="%.2f s" % ev["dur"])
                self.lbl_meta.configure(
                    text="段 #%d   帧 %d   %d Hz" % (ev["seg"], ev["frames"],
                                                     self.worker.sr if self.worker else 0))
            elif kind == "rate":
                self.stat.configure(
                    text="帧率 %.1f fps   累计 %d 帧   %.1f KB"
                         % (ev["fps"], ev["frames"], ev["kb"]))
            elif kind == "seg_begin":
                self.seg_bars.clear()
                self.tree.selection_remove(self.tree.selection())
                self.say("段 #%d 开始（sid=%d, %d Hz）" % (ev["id"], ev["sid"], ev["sr"]), "ok")
            elif kind == "seg_end":
                self.on_seg_end(ev)
            elif kind == "seg_ready":
                sid = ev["id"]
                if self.tree.exists(str(sid)):
                    vals = list(self.tree.item(str(sid), "values"))
                    vals[5] = "%.0f" % ev["rms"]
                    vals[7] = os.path.basename(ev["path"]) if ev["path"] else ""
                    self.tree.item(str(sid), values=vals)
                self.say("段 #%d 完成 %.2fs  RMS %.0f  %s"
                         % (sid, ev["dur"], ev["rms"],
                            os.path.basename(ev["path"]) if ev["path"] else "(未保存)"), "ok")
            elif kind == "caps":
                self.set_caps(ev)
            elif kind == "info":
                self.set_info(ev["key"], ev["value"])
            elif kind == "ctl":
                self.say("CTL %s  [%s]" % (ev["name"], ev["hex"]), "mut")
            elif kind == "rawkey":
                self.on_rawkey(ev["key_id"], ev["src"], ev["code"], ev["hex"])
            elif kind == "hidok":
                self.say(ev["msg"], "ok")
                self.lbl_key.configure(text="学习中… 按遥控器任意键", fg=C_OK)
            elif kind == "hidfail":
                self.say("按键监听启动失败（回退 hidapi）: %s" % ev.get("reason", ""), "warn")
                if self.hid and isinstance(self.hid, RawInputWatcher):
                    self.hid = HidWatcher(self.q)
                    self.hid.start()
                    self.lbl_key.configure(text="hidapi 回退模式（仅 Consumer 集合）", fg=C_WARN)
            elif kind == "rawmove":
                self.stat.configure(text="指针位移 dx=%+d dy=%+d" % (ev["dx"], ev["dy"]))
        if dirty:
            self.draw_wave()
        self.after(60, self._pump)

    # ------------------------------------------------------------ 关闭
    def on_close(self):
        try:
            self.cfg = self.collect_cfg()
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        if self.hid:
            self.hid.stop()
        if self.worker:
            self.worker.stop()
        self.after(300, self.destroy)


def main():
    if not HAVE_BLE:
        print("缺少 bleak: python -m pip install bleak")
    app = App()
    if "--tab" in sys.argv:
        try:
            app.nb.select(int(sys.argv[sys.argv.index("--tab") + 1]))
        except Exception:
            pass
    app.mainloop()


if __name__ == "__main__":
    main()
