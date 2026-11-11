#!/usr/bin/env python3
"""T1-Remote GATT 观察 / 下行工具"""
import argparse
import asyncio
import datetime
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("bleak 未安装: python -m pip install bleak")
    sys.exit(1)

ADDR = "AA:BB:CC:DD:EE:FF"

SHORT = {
    "AE41": "0000ae41-0000-1000-8000-00805f9b34fb",
    "AE42": "0000ae42-0000-1000-8000-00805f9b34fb",
    "AE01": "0000ae01-0000-1000-8000-00805f9b34fb",
    "AE02": "0000ae02-0000-1000-8000-00805f9b34fb",
    "AB5E0002": "ab5e0002-5a21-4f05-bc7d-af01f617b664",
    "AB5E0003": "ab5e0003-5a21-4f05-bc7d-af01f617b664",
    "AB5E0004": "ab5e0004-5a21-4f05-bc7d-af01f617b664",
    "BATT": "00002a19-0000-1000-8000-00805f9b34fb",
}
REV = {v: k for k, v in SHORT.items()}


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def name_of(uuid):
    return REV.get(str(uuid).lower(), str(uuid))


def log(msg, fp=None):
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    if fp:
        fp.write(line + "\n")
        fp.flush()


async def get_client(addr):
    c = BleakClient(addr, timeout=20.0)
    c._backend._device_info = int(addr.replace(":", ""), 16)
    return c


async def cmd_readall(args):
    async with await get_client(args.addr) as c:
        log(f"connected={c.is_connected}")
        for svc in c.services:
            for ch in svc.characteristics:
                if "read" not in ch.properties:
                    continue
                try:
                    v = bytes(await c.read_gatt_char(ch))
                    asc = "".join(chr(b) if 32 <= b < 127 else "." for b in v)
                    log(f"READ {name_of(ch.uuid):10s} len={len(v):<3} [{v.hex(' ')}]  {asc}")
                except Exception as e:
                    log(f"READ {name_of(ch.uuid):10s} FAIL {e}")


async def cmd_dump(args):
    async with await get_client(args.addr) as c:
        log(f"connected={c.is_connected} addr={args.addr}")
        for svc in c.services:
            log(f"SERVICE {name_of(svc.uuid)}  [{svc.uuid}]")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                log(f"    CHAR {name_of(ch.uuid):10s} [{ch.uuid}]  handle=0x{ch.handle:04X}  {props}")


async def cmd_listen(args):
    fp = open(args.log, "a", encoding="utf-8") if args.log else None
    async with await get_client(args.addr) as c:
        subs = []
        for svc in c.services:
            for ch in svc.characteristics:
                if "notify" in ch.properties or "indicate" in ch.properties:
                    subs.append(ch)

        def mk(ch):
            def cb(_sender, data: bytearray):
                hx = " ".join(f"{b:02X}" for b in data)
                log(f"NOTIFY {name_of(ch.uuid):10s} len={len(data):<3} [{hx}]", fp)
            return cb

        for ch in subs:
            try:
                await c.start_notify(ch, mk(ch))
                log(f"subscribed {name_of(ch.uuid)}", fp)
            except Exception as e:
                log(f"subscribe FAIL {name_of(ch.uuid)}: {e}", fp)

        log(f"listening {args.seconds}s -- 按键 / 观察私有通道是否上报", fp)
        await asyncio.sleep(args.seconds)
        log("listen done", fp)
    if fp:
        fp.close()


async def cmd_write(args):
    uuid = SHORT.get(args.char.upper(), args.char.lower())
    payload = bytes.fromhex(args.hex.replace(" ", "").replace(",", ""))
    resp = not args.no_response
    async with await get_client(args.addr) as c:
        log(f"write -> {name_of(uuid)} len={len(payload)} [{payload.hex(' ')}] response={resp}")
        await c.write_gatt_char(uuid, payload, response=resp)
        log("write OK")
        if args.readback:
            v = await c.read_gatt_char(uuid)
            log(f"readback [{v.hex(' ')}]")


async def cmd_read(args):
    uuid = SHORT.get(args.char.upper(), args.char.lower())
    async with await get_client(args.addr) as c:
        try:
            v = await c.read_gatt_char(uuid)
            log(f"READ {name_of(uuid)} len={len(v)} [{v.hex(' ')}]")
        except Exception as e:
            log(f"read FAIL {name_of(uuid)}: {e}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--addr", default=ADDR)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dump")
    d.set_defaults(func=cmd_dump)

    ra = sub.add_parser("readall")
    ra.set_defaults(func=cmd_readall)

    l = sub.add_parser("listen")
    l.add_argument("--seconds", type=int, default=60)
    l.add_argument("--log", default="t1_notify.log")
    l.set_defaults(func=cmd_listen)

    w = sub.add_parser("write")
    w.add_argument("--char", required=True)
    w.add_argument("--hex", required=True)
    w.add_argument("--no-response", action="store_true")
    w.add_argument("--readback", action="store_true")
    w.set_defaults(func=cmd_write)

    r = sub.add_parser("read")
    r.add_argument("--char", required=True)
    r.set_defaults(func=cmd_read)

    args = p.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
