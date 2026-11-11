#!/usr/bin/env python3
"""对 t1_mic.py 抓到的 .adpcm 做多假设解码对比，判定真实编码。"""
import math
import struct
import sys
import wave
from collections import Counter

IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]
IMA_STEP = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707,
    1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635,
    13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
]


def ima(data, low_first=False, reset_each=0):
    pred, index = 0, 0
    out = []
    for i, byte in enumerate(data):
        if reset_each and i % reset_each == 0:
            pred, index = 0, 0
        nibs = ((byte & 0x0F, (byte >> 4) & 0x0F) if low_first
                else ((byte >> 4) & 0x0F, byte & 0x0F))
        for nib in nibs:
            step = IMA_STEP[index]
            diff = step >> 3
            if nib & 4:
                diff += step
            if nib & 2:
                diff += step >> 1
            if nib & 1:
                diff += step >> 2
            pred += -diff if (nib & 8) else diff
            pred = max(-32768, min(32767, pred))
            index = max(0, min(88, index + IMA_INDEX[nib]))
            out.append(pred)
    return out


def pcm4(data, center, scale, low_first=False):
    out = []
    for byte in data:
        nibs = ((byte & 0x0F, (byte >> 4) & 0x0F) if low_first
                else ((byte >> 4) & 0x0F, byte & 0x0F))
        for nib in nibs:
            out.append(max(-32768, min(32767, (nib - center) * scale)))
    return out


def stats(name, d):
    n = len(d)
    mean = sum(d) / n
    rms = math.sqrt(sum(x * x for x in d) / n)
    clip = sum(1 for x in d if x >= 32000 or x <= -32000) / n * 100
    # lag-1 自相关
    num = sum((d[i] - mean) * (d[i + 1] - mean) for i in range(n - 1))
    den = sum((x - mean) ** 2 for x in d)
    ac = num / den if den else 0
    print(f"{name:34s} RMS={rms:9.0f} DC={mean:9.0f} 削顶={clip:5.1f}% lag1自相关={ac:+.3f}")
    return rms, ac, clip


def env(d, win=1600):
    return [int(math.sqrt(sum(x * x for x in d[i:i + win]) / win))
            for i in range(0, len(d) - win, win)]


def main(path):
    data = open(path, "rb").read()
    print(f"文件 {path}: {len(data)} 字节 = {len(data)*2} nibble = "
          f"{len(data)*2/16000:.2f}s @16k\n")

    hi = Counter((b >> 4) & 0xF for b in data)
    lo = Counter(b & 0xF for b in data)
    print("高 nibble 分布:", {hex(k): hi[k] for k in sorted(hi)})
    print("低 nibble 分布:", {hex(k): lo[k] for k in sorted(lo)})
    tot = len(data) * 2
    small = sum(hi[k] + lo[k] for k in (0, 8, 1, 9)) / tot * 100
    print(f"|增量|<=1 的 nibble 占比: {small:.1f}%\n")

    variants = {
        "A IMA clamps/高先": ima(data),
        "B IMA 低先": ima(data, low_first=True),
        "C IMA 每帧重置(128B)": ima(data, reset_each=128),
        "D PCM4 无符号 中心8 缩放4096": pcm4(data, 8, 4096),
        "E PCM4 无符号 中心8 缩放2048": pcm4(data, 8, 2048),
        "F PCM4 有符号 缩放4096": pcm4(data, 0, 4096),
    }
    res = {}
    for k, v in variants.items():
        res[k] = stats(k, v)

    # 最优判定：削顶少 + lag1 自相关高
    best = max(res, key=lambda k: (res[k][1] - res[k][2] / 50))
    print(f"\n>>> 最可能: {best}")

    d = variants[best]
    e = env(d)
    print("\n包络(每100ms RMS) 前 60 段:")
    print(" ".join(str(x) for x in e[:60]))
    print("\n包络 后 60 段:")
    print(" ".join(str(x) for x in e[-60:]))

    out = path.replace(".adpcm", "_dec.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<%dh" % len(d), *d))
    print(f"\n写出 {out}")


if __name__ == "__main__":
    main(sys.argv[1])
