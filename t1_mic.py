#!/usr/bin/env python3
"""T1-Remote 语音录制（Google ATVV / Voice-over-BLE v1.0）

协议（Google Voice over BLE spec 1.0）:
  AB5E0002 TX    主机 -> 遥控器  GET_CAPS 0x0A / MIC_OPEN 0x0C / MIC_CLOSE 0x0D / MIC_EXTEND 0x0E
  AB5E0003 AUDIO 遥控器 -> 主机  IMA ADPCM 4-bit 16kHz 音频帧
  AB5E0004 CTL   遥控器 -> 主机  AUDIO_STOP 0x00 / AUDIO_START 0x04 / START_SEARCH 0x08
                                 AUDIO_SYNC 0x0A / CAPS_RESP 0x0B / MIC_OPEN_ERROR 0x0C

音频: 128 字节/帧, 62.5 帧/秒 -> 256 采样/帧 -> 16kHz, 64kbps
每段流开头有 4 字节 0x77 填充，解码前需跳过；解码后需高通去 DC 漂移。

用法:
  python t1_mic.py --seconds 60       # 按一次语音键 = 一段，自动存 wav
  python t1_mic.py --probe            # 只握手看能力
"""
import argparse
import asyncio
import datetime
import os
import struct
import sys
import time

try:
    from bleak import BleakClient
except ImportError:
    print("bleak 未安装: python -m pip install bleak")
    sys.exit(1)

import codec

ADDR = "AA:BB:CC:DD:EE:FF"
TX = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
AUDIO = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
CTL = "ab5e0004-5a21-4f05-bc7d-af01f617b664"

EV_AUDIO_STOP = 0x00
EV_AUDIO_START = 0x04
EV_START_SEARCH = 0x08
EV_AUDIO_SYNC = 0x0A
EV_CAPS_RESP = 0x0B
EV_MIC_OPEN_ERROR = 0x0C

FRAME = 128
SR = 16000


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg):
    print(f"[{ts()}] {msg}", flush=True)


class Recorder:
    def __init__(self):
        self.client = None
        self.streams = []       # [{sid, raw: bytearray, frames, t0}]
        self.cur = None
        self.sample_rate = SR
        self.audio_started = False
        self.burst = 0
        self.burst_t0 = time.time()
        self.last_rate_log = 0.0
        self.total_frames = 0

    async def send(self, name, payload):
        try:
            await self.client.write_gatt_char(TX, bytes(payload), response=False)
            log(f"TX {name} -> {' '.join('%02X' % b for b in payload)}")
        except Exception as e:
            log(f"TX {name} 失败: {type(e).__name__}: {e}")

    async def get_caps(self):
        await self.send("GET_CAPS", [0x0A, 0x01, 0x00, 0x00, 0x03, 0x03])

    async def mic_open(self, capture=True):
        await self.send("MIC_OPEN", [0x0C, 0x01 if capture else 0x00])

    async def mic_close(self, sid=0xFF):
        await self.send("MIC_CLOSE", [0x0D, sid])

    def new_stream(self, sid, reason):
        s = dict(sid=sid, raw=bytearray(), frames=0, t0=time.time(), reason=reason)
        self.streams.append(s)
        self.cur = s
        return s

    def on_audio(self, _sender, data):
        b = bytes(data)
        if self.cur is None:
            self.new_stream(-1, "implicit")
        self.cur["raw"] += b
        self.cur["frames"] += 1
        self.total_frames += 1
        self.burst += 1
        now = time.time()
        if now - self.burst_t0 >= 1.0:
            rate = self.burst / (now - self.burst_t0)
            if now - self.last_rate_log >= 3.0:
                log(f"AUDIO {rate:.1f} 帧/秒  累计 {self.total_frames} 帧 "
                    f"({self.total_frames*FRAME/1000:.1f} KB)")
                self.last_rate_log = now
            self.burst = 0
            self.burst_t0 = now

    def on_ctl(self, _sender, data):
        b = bytes(data)
        if not b:
            return
        op = b[0]
        names = {EV_AUDIO_STOP: "AUDIO_STOP", EV_AUDIO_START: "AUDIO_START",
                 EV_START_SEARCH: "START_SEARCH", EV_AUDIO_SYNC: "AUDIO_SYNC",
                 EV_CAPS_RESP: "CAPS_RESP", EV_MIC_OPEN_ERROR: "MIC_OPEN_ERROR"}
        log(f"CTL {names.get(op, 'UNK_0x%02X' % op)} [{b.hex(' ').upper()}]")

        if op == EV_START_SEARCH:
            log(">> 语音键按下 -> MIC_OPEN(capture)")
            asyncio.ensure_future(self.mic_open(capture=True))

        elif op == EV_CAPS_RESP and len(b) >= 8:
            ver = struct.unpack(">H", b[1:3])[0]
            codecs, model = b[3], b[4]
            frame = struct.unpack(">H", b[5:7])[0]
            self.sample_rate = 16000 if (codecs & 0x02) else 8000
            log(f"   版本=0x{ver:04X} codecs=0x{codecs:02X} 交互模型=0x{model:02X} "
                f"帧长={frame} 采样率={self.sample_rate}")

        elif op == EV_AUDIO_START and len(b) >= 4:
            reason, codec_id, sid = b[1], b[2], b[3]
            self.sample_rate = 16000 if codec_id == 0x02 else 8000
            self.audio_started = True
            self.new_stream(sid, reason)
            log(f"   >>> 新语音段 stream_id={sid} "
                f"(理由 0x{reason:02X}, codec 0x{codec_id:02X}, {self.sample_rate}Hz)")

        elif op == EV_AUDIO_STOP and len(b) >= 2:
            rmap = {0x00: "MIC_CLOSE", 0x02: "松开按键", 0x04: "将开始新流",
                    0x08: "音频传输超时", 0x10: "通知被关闭", 0x80: "其他"}
            dur = ""
            if self.cur:
                dur = f"  本段 {self.cur['frames']*FRAME*2/self.sample_rate:.2f}s"
            log(f"   <<< 结束: {rmap.get(b[1], hex(b[1]))}{dur}")
            self.audio_started = False
            self.cur = None

        elif op == EV_AUDIO_SYNC and len(b) >= 7:
            log(f"   codec=0x{b[1]:02X} frame_no={struct.unpack('>H', b[2:4])[0]} "
                f"pred={struct.unpack('>H', b[4:6])[0]} index={b[6]}")

        elif op == EV_MIC_OPEN_ERROR and len(b) >= 3:
            code = struct.unpack(">H", b[1:3])[0]
            emap = {0x0F01: "保留/未就绪", 0x0F02: "遥控器未激活",
                    0x0F03: "音频通知未开启", 0x0F80: "PTT/HTT 进行中", 0x0FFF: "内部错误"}
            log(f"   错误码=0x{code:04X} ({emap.get(code, '未知')})")


def export(rec, outdir):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    good = [s for s in rec.streams if s["frames"] >= 8]
    if not good:
        log("本次没有可用的语音段。")
        return []

    produced = []
    for idx, s in enumerate(good, 1):
        raw_path = os.path.join(outdir, f"seg_{stamp}_{idx:02d}_sid{s['sid']}.adpcm")
        with open(raw_path, "wb") as f:
            f.write(bytes(s["raw"]))
        pcm = codec.decode_stream(bytes(s["raw"]), skip=4, hp=120.0, sr=rec.sample_rate)
        wav_path = os.path.join(outdir, f"voice_{stamp}_{idx:02d}.wav")
        codec.write_wav(wav_path, pcm, rec.sample_rate)
        dur = s["frames"] * FRAME * 2 / rec.sample_rate
        rms = (sum(v * v for v in pcm) / len(pcm)) ** 0.5 if pcm else 0
        log(f"段 {idx}: sid={s['sid']} {dur:.2f}s  {len(s['raw'])} 字节  "
            f"RMS {rms:.0f}  -> {os.path.basename(wav_path)}")
        produced.append(wav_path)

    # 合并成一条（段间插 300ms 静音）
    merged = []
    gap = [0] * int(0.3 * rec.sample_rate)
    for i, s in enumerate(good):
        if i:
            merged.extend(gap)
        merged.extend(codec.decode_stream(bytes(s["raw"]), skip=4, hp=120.0,
                                          sr=rec.sample_rate))
    all_path = os.path.join(outdir, f"voice_all_{stamp}.wav")
    codec.write_wav(all_path, merged, rec.sample_rate)
    log(f"合并: {all_path}  共 {len(merged)/rec.sample_rate:.2f}s")
    produced.append(all_path)
    return produced


async def run(args):
    rec = Recorder()
    client = BleakClient(ADDR, timeout=20.0)
    client._backend._device_info = int(ADDR.replace(":", ""), 16)
    await client.connect()
    rec.client = client
    log(f"BLE connected, MTU={getattr(client, 'mtu_size', '?')}")
    await client.start_notify(CTL, rec.on_ctl)
    await client.start_notify(AUDIO, rec.on_audio)
    log("subscribe CTL + AUDIO OK")
    await rec.get_caps()
    if args.micopen_first:
        await asyncio.sleep(0.3)
        await rec.mic_open(capture=True)

    log(f"=== 按住语音键说话，窗口 {args.seconds}s（每次按键 = 一段）===")
    t0 = time.time()
    try:
        while time.time() - t0 < args.seconds:
            await asyncio.sleep(0.2)
    except KeyboardInterrupt:
        pass

    try:
        await client.disconnect()
    except Exception:
        pass

    log(f"=== 结束: {len(rec.streams)} 段 / {rec.total_frames} 帧 "
        f"({rec.total_frames*FRAME*2/rec.sample_rate:.2f}s 音频) ===")
    outdir = os.path.dirname(os.path.abspath(__file__))
    files = export(rec, outdir)
    if files:
        print("\nFILES=" + "|".join(files))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--micopen-first", action="store_true")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    if args.probe:
        args.seconds = 6
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
