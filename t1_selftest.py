#!/usr/bin/env python3
"""上位机后端自检：只跑 BleWorker，不建窗口"""
import json
import queue
import sys
import time

import t1_gui as G

q = queue.Queue()
cfg = json.loads(json.dumps(G.CFG_DEFAULT))
cfg["mic_extend_ms"] = 2000

w = G.BleWorker(q, cfg)
w.start()

t0 = time.time()
seen = {"state": [], "caps": None, "info": {}, "log": 0, "audio": 0, "seg_begin": 0, "seg_end": 0}
while time.time() - t0 < 14:
    try:
        kind, ev = q.get(timeout=0.3)
    except queue.Empty:
        continue
    if kind == "state":
        seen["state"].append(ev["value"])
        print("STATE", ev["value"], ev["text"])
    elif kind == "caps":
        seen["caps"] = ev
        print("CAPS", ev)
    elif kind == "info":
        seen["info"][ev["key"]] = ev["value"]
        print("INFO", ev["key"], "=", ev["value"])
    elif kind == "log":
        seen["log"] += 1
        print("LOG", ev["text"])
    elif kind == "audio":
        seen["audio"] += 1
    elif kind == "seg_begin":
        seen["seg_begin"] += 1
        print("SEG_BEGIN", ev)
    elif kind == "seg_end":
        seen["seg_end"] += 1
        print("SEG_END id=%s frames=%s dur=%.2f" % (ev["id"], ev["frames"], ev["dur"]))

w.stop()
time.sleep(2.0)

print("\n==== 自检汇总 ====")
print("states :", seen["state"])
print("info   :", seen["info"])
print("caps   :", "OK" if seen["caps"] else "缺失")
print("audio  : %d 帧 (按键未按则应为 0)" % seen["audio"])
print("seg    : %d 段" % seen["seg_end"])
ok = ("connected" in seen["state"]) and bool(seen["caps"])
print("RESULT :", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
