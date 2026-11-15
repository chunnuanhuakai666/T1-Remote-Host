# -*- coding: utf-8 -*-
"""只读探测：HID over GATT 的报告通道能不能被我们读到。

不改任何配置，只做 service discovery + 尝试订阅 2A4D。
"""
import asyncio
from bleak import BleakClient

MAC = "7D:D4:10:68:3E:EA"
HID = "00001812-0000-1000-8000-00805f9b34fb"
REPORT = "00002a4d-0000-1000-8000-00805f9b34fb"


async def main():
    got = []

    def on_report(_c, data):
        got.append(bytes(data))

    async with BleakClient(MAC, timeout=12) as c:
        print("connected =", c.is_connected)
        reports = []
        for s in c.services:
            if str(s.uuid).lower() != HID:
                continue
            for ch in s.characteristics:
                p = ",".join(sorted(ch.properties))
                print("  1812 char %s  %s" % (ch.uuid, p))
                if str(ch.uuid).lower() == REPORT:
                    reports.append(ch)
        if not reports:
            print("!! 没找到 1812/2A4D")
            return
        ok = 0
        for ch in reports:
            try:
                await c.start_notify(ch, on_report)
                ok += 1
                print("subscribe OK:", ch.uuid, "handle", ch.handle)
            except Exception as e:
                print("subscribe FAIL:", ch.uuid, type(e).__name__, e)
        print("subscribed=%d, 等 4 秒看有没有 input report…" % ok)
        await asyncio.sleep(4)
        print("收到 %d 包" % len(got))
        for d in got[:8]:
            print("  ", d[:24].hex(" "))


asyncio.run(main())
