#!/usr/bin/env python3
"""T1-Remote 全通道抓包：HID 各集合 + BLE 私有通道 notify

用途：判定“语音键”到底往哪里发数据。
  - HID RID 0x5A (usage page 0xFF00) 255 字节厂商上报
  - HID RID 0x02 Consumer / RID 0x04 Mouse
  - BLE AE42 / AE02 / AB5E0002-0004 notify

用法:
  python t1_voice.py --seconds 90 --log t1_voice.log
"""
import argparse
import asyncio
import datetime
import sys
import threading
import time

try:
    import hid
except ImportError:
    print("hidapi 未安装: python -m pip install hidapi")
    sys.exit(1)

try:
    from bleak import BleakClient
except ImportError:
    print("bleak 未安装: python -m pip install bleak")
    sys.exit(1)

ADDR = "AA:BB:CC:DD:EE:FF"
VID, PID = 0x620A, 0x0407

SHORT = {
    "AE41": "0000ae41-0000-1000-8000-00805f9b34fb",
    "AE42": "0000ae42-0000-1000-8000-00805f9b34fb",
    "AE01": "0000ae01-0000-1000-8000-00805f9b34fb",
    "AE02": "0000ae02-0000-1000-8000-00805f9b34fb",
    "AB5E0002": "ab5e0002-5a21-4f05-bc7d-af01f617b664",
    "AB5E0003": "ab5e0003-5a21-4f05-bc7d-af01f617b664",
    "AB5E0004": "ab5e0004-5a21-4f05-bc7d-af01f617b664",
}

_lock = threading.Lock()
_logfp = None
_start = time.time()


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(msg):
    with _lock:
        line = f"[{ts()}] {msg}"
        print(line, flush=True)
        if _logfp:
            _logfp.write(line + "\n")
            _logfp.flush()


def hexs(b, limit=None):
    if limit and len(b) > limit:
        return " ".join("%02X" % x for x in b[:limit]) + f" ...(+{len(b)-limit})"
    return " ".join("%02X" % x for x in b)


# ---------------- HID 侧 ----------------

class HidReader(threading.Thread):
    def __init__(self, usage_page, usage, label, stop_evt, rate_window=1.0):
        super().__init__(daemon=True)
        self.usage_page = usage_page
        self.usage = usage
        self.label = label
        self.stop_evt = stop_evt
        self.rate_window = rate_window
        self.dev = None
        self.total = 0
        self.printed = 0
        self.bytes = 0

    def open(self):
        for d in hid.enumerate(VID, PID):
            if d["usage_page"] == self.usage_page and d["usage"] == self.usage:
                dev = hid.device()
                dev.open_path(d["path"])
                dev.set_nonblocking(1)
                self.dev = dev
                return True
        return False

    def run(self):
        if not self.open():
            log(f"HID {self.label}: 打开失败（可能被系统独占）")
            return
        log(f"HID {self.label}: OPEN, 开始读")
        last_report_t = 0.0
        win_start = time.time()
        win_count = 0
        gap_max = 0.0
        first_in_win = None
        try:
            while not self.stop_evt.is_set():
                try:
                    data = self.dev.read(300)
                except Exception as e:
                    log(f"HID {self.label}: read err {e}")
                    break
                now = time.time()
                if data:
                    b = bytes(data)
                    self.total += 1
                    self.bytes += len(b)
                    win_count += 1
                    if first_in_win is None:
                        first_in_win = b
                    if last_report_t:
                        gap = (now - last_report_t) * 1000.0
                        if gap > gap_max:
                            gap_max = gap
                    last_report_t = now
                    # 前 6 条完整打印，之后只打摘要
                    if self.printed < 6:
                        self.printed += 1
                        log(f"HID {self.label} #{self.total} len={len(b)} [{hexs(b, 32)}]")
                else:
                    time.sleep(0.001)
                if now - win_start >= self.rate_window:
                    if win_count:
                        log(f"HID {self.label} 速率: {win_count/self.rate_window:.1f} 包/秒, "
                            f"首包 [{hexs(first_in_win or b'', 24)}], 最大间隔 {gap_max:.1f}ms, 累计 {self.total} 包")
                    win_start = now
                    win_count = 0
                    gap_max = 0.0
                    first_in_win = None
        finally:
            try:
                self.dev.close()
            except Exception:
                pass
            log(f"HID {self.label}: 结束, 共 {self.total} 包 / {self.bytes} 字节")


# ---------------- BLE 侧 ----------------

async def ble_listen(stop_evt, seconds):
    client = BleakClient(ADDR, timeout=20.0)
    # 绕过 bleak 的发现流程：设备已被系统连接，不在广播中
    client._backend._device_info = int(ADDR.replace(":", ""), 16)

    def mk(name):
        def cb(_sender, data):
            b = bytes(data)
            log(f"BLE {name} len={len(b)} [{hexs(b, 48)}]")
        return cb

    try:
        await client.connect()
    except Exception as e:
        log(f"BLE 连接失败: {e}")
        return
    log(f"BLE connected={client.is_connected}")

    for name in ("AE42", "AE02", "AB5E0002", "AB5E0003", "AB5E0004"):
        try:
            await client.start_notify(SHORT[name], mk(name))
            log(f"BLE subscribe OK {name}")
        except Exception as e:
            log(f"BLE subscribe FAIL {name}: {e}")

    t0 = time.time()
    while not stop_evt.is_set() and time.time() - t0 < seconds:
        await asyncio.sleep(0.2)

    try:
        await client.disconnect()
    except Exception:
        pass
    log("BLE 断开")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    global _logfp
    if args.log:
        _logfp = open(args.log, "w", encoding="utf-8")

    stop_evt = threading.Event()

    readers = [
        HidReader(0xFF00, 0x00, "VENDOR-5A", stop_evt),
        HidReader(0x0C, 0x01, "CONSUMER", stop_evt),
        HidReader(0x01, 0x02, "MOUSE", stop_evt),
        HidReader(0x01, 0x80, "SYSTEM", stop_evt),
    ]
    for r in readers:
        r.start()

    async def runner():
        await ble_listen(stop_evt, args.seconds)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()

    for r in readers:
        r.join(timeout=2.0)

    log("=== 汇总 ===")
    for r in readers:
        log(f"{r.label}: {r.total} 包 / {r.bytes} 字节")
    if _logfp:
        _logfp.close()


if __name__ == "__main__":
    main()
