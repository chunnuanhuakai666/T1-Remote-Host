#!/usr/bin/env python3
"""第二级：更大假设空间 + 逐帧头探测，用客观指标挑出正确解码。"""
import math
import struct
import sys
import wave

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


def strip_frames(data, frame_size, skip):
    if frame_size <= skip:
        return data
    out = bytearray()
    for i in range(0, len(data), frame_size):
        out += data[i + skip:i + frame_size]
    return bytes(out)


def ima(data, wrap=False, low_first=False):
    pred, index = 0, 0
    out = []
    ap = out.append
    for byte in data:
        nibs = ((byte & 0x0F, byte >> 4) if low_first else (byte >> 4, byte & 0x0F))
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
            if wrap:
                pred = ((pred + 32768) & 0xFFFF) - 32768
            else:
                pred = -32768 if pred < -32768 else (32767 if pred > 32767 else pred)
            index += IMA_INDEX[nib]
            index = 0 if index < 0 else (88 if index > 88 else index)
            ap(pred)
    return out


def dpcm(data, k, low_first=False):
    acc = 0
    out = []
    ap = out.append
    for byte in data:
        nibs = ((byte & 0x0F, byte >> 4) if low_first else (byte >> 4, byte & 0x0F))
        for nib in nibs:
            v = nib - 16 if nib & 8 else nib
            acc += v * k
            acc = -32768 if acc < -32768 else (32767 if acc > 32767 else acc)
            ap(acc)
    return out


def stats(d):
    n = len(d)
    mean = sum(d) / n
    rms = math.sqrt(sum(x * x for x in d) / n)
    rail = sum(1 for x in d if x >= 32000 or x <= -32000) / n * 100
    num = sum((d[i] - mean) * (d[i + 1] - mean) for i in range(n - 1))
    den = sum((x - mean) ** 2 for x in d) or 1
    ac = num / den
    return rms, mean, rail, ac


def env(d, win=1600):
    return [math.sqrt(sum(x * x for x in d[i:i + win]) / win)
            for i in range(0, len(d) - win, win)]


def env_score(d):
    """包络应平稳变化（语音音节 2-8Hz），帧边界处不应有周期性尖刺。"""
    e = env(d, 160)
    if len(e) < 20:
        return 0
    jumps = sum(abs(e[i] - e[i - 1]) / (max(e[i], e[i - 1]) + 1) for i in range(1, len(e)))
    return jumps / len(e)


def main(path):
    raw = open(path, "rb").read()
    print(f"{path}: {len(raw)} 字节, {len(raw)*2/16000:.2f}s @16k\n")
    print(f"{'变体':40s} {'RMS':>8s} {'DC':>8s} {'削顶%':>6s} {'lag1':>6s} {'包络抖动':>8s}")
    cands = {}

    def add(name, d):
        rms, dc, rail, ac = stats(d)
        j = env_score(d)
        cands[name] = (d, rms, dc, rail, ac, j)
        print(f"{name:40s} {rms:8.0f} {dc:8.0f} {rail:6.1f} {ac:+6.3f} {j:8.3f}")

    for skip in (0, 1, 2):
        body = strip_frames(raw, 128, skip)
        add(f"IMA 连续 skip={skip}", ima(body))
        add(f"IMA 回绕 skip={skip}", ima(body, wrap=True))
        add(f"IMA 连续 低先 skip={skip}", ima(body, low_first=True))
    for k in (32, 64, 128, 256, 512, 1024):
        add(f"DPCM k={k} skip=0", dpcm(raw, k))

    print()
    ok = [(n, v) for n, v in cands.items()
          if v[3] < 0.2 and abs(v[2]) < 1200 and 200 < v[1] < 12000]
    print(f"满足 [无削顶 & |DC|<1200 & 200<RMS<12000] 的候选 {len(ok)} 个:")
    for n, v in sorted(ok, key=lambda x: x[1][5]):
        print(f"  {n:40s} RMS={v[1]:7.0f} DC={v[2]:6.0f} 抖动={v[5]:.3f}")

    if ok:
        best = min(ok, key=lambda x: x[1][5])
        name, (d, rms, dc, rail, ac, j) = best
        print(f"\n>>> 选中 {name}")
        print("包络前40段:", " ".join(f"{x:.0f}" for x in env(d)[:40]))
        print("包络后40段:", " ".join(f"{x:.0f}" for x in env(d)[-40:]))
        out = path.replace(".adpcm", "_best.wav")
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<%dh" % len(d), *d))
        print(f"写出 {out}")


if __name__ == "__main__":
    main(sys.argv[1])
