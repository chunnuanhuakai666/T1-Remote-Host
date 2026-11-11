#!/usr/bin/env python3
"""T1-Remote 全量 GATT 属性表（含 descriptor / user description）"""
import asyncio
import sys

try:
    from bleak import BleakClient
except ImportError:
    print("bleak 未安装")
    sys.exit(1)

ADDR = "AA:BB:CC:DD:EE:FF"


async def main():
    c = BleakClient(ADDR, timeout=20.0)
    c._backend._device_info = int(ADDR.replace(":", ""), 16)
    await c.connect()
    print("connected =", c.is_connected)

    for svc in c.services:
        print(f"\n[SERVICE] {svc.uuid}  handle=0x{svc.handle:04X}")
        for ch in svc.characteristics:
            props = ",".join(ch.properties)
            print(f"  [CHAR] {ch.uuid}  h=0x{ch.handle:04X}  {props}")
            try:
                v = bytes(await c.read_gatt_char(ch))
                if v:
                    print(f"         value ({len(v)}) = {' '.join('%02X' % b for b in v)}")
                else:
                    print("         value = (empty)")
            except Exception as e:
                print(f"         read -> {type(e).__name__}: {e}")
            for d in getattr(ch, "descriptors", []) or []:
                try:
                    dv = bytes(await c.read_gatt_descriptor(d.handle))
                    txt = ""
                    try:
                        txt = dv.decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    print(f"         [DESC] {d.uuid} h=0x{d.handle:04X} = "
                          f"{' '.join('%02X' % b for b in dv)}  {txt!r}")
                except Exception as e:
                    print(f"         [DESC] {d.uuid} h=0x{d.handle:04X} -> {type(e).__name__}")

    await c.disconnect()


asyncio.run(main())
