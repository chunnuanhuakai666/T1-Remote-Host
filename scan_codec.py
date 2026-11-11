#!/usr/bin/env python3
"""宽扫描：判定 .adpcm 的真实编码形式（含位序反转 / 码位偏移等硬件常见变体）"""
import itertools
import sys
import wave

import numpy as np

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
REV4 = [int(f"{i:04b}"[::-1], 2) for i in range(16)]
REV8 = [int(f"{i:08b}"[::-1], 2) for i in range(256)]
SR = 16000


def codes(data, low_first=False, rev_nib=False, rev_byte=False, xor=0):
    out = []
    for byte in data:
        if rev_byte:
            byte = REV8[byte]
        a, b = (byte & 0x0F, byte >> 4) if low_first else (byte >> 4, byte & 0x0F)
        if rev_nib:
            a, b = REV4[a], REV4[b]
        out.append(a ^ xor)
        out.append(b ^ xor)
    return out


def dec_ima(cs, step_shift=0):
    pred, index = 0, 0
    out = np.empty(len(cs), dtype=np.float64)
    for k, nib in enumerate(cs):
        step = IMA_STEP[index] >> step_shift
        diff = step >> 3
        if nib & 4:
            diff += step
        if nib & 2:
            diff += step >> 1
        if nib & 1:
            diff += step >> 2
        pred += -diff if (nib & 8) else diff
        pred = -32768 if pred < -32768 else (32767 if pred > 32767 else pred)
        index += IMA_INDEX[nib]
        index = 0 if index < 0 else (88 if index > 88 else index)
        out[k] = pred
    return out


def dec_pcm4(cs, scale):
    return np.array([(c - 8) * scale for c in cs], dtype=np.float64)


def band_score(d):
    if d.std() < 1:
        return 0, 0, 0
    x = d - d.mean()
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    E = X ** 2
    tot = E.sum()
    if tot <= 0:
        return 0, 0, 0
    sp = E[(f >= 300) & (f <= 3500)].sum() / tot * 100
    low = E[f < 150].sum() / tot * 100
    rail = ((d >= 32000) | (d <= -32000)).mean() * 100
    return sp, low, rail


def main(path, start_frame=6, n_frames=300):
    raw = open(path, "rb").read()
    data = raw[start_frame * 128:(start_frame + n_frames) * 128]

    # 1) 相邻码相似度：真语音的 ADPCM 码变化缓慢
    cs = codes(data)
    same = sum(1 for i in range(1, len(cs)) if abs(cs[i] - cs[i - 1]) <= 1) / (len(cs) - 1)
    print(f"相邻 nibble |Δ|<=1 占比: {same*100:.1f}%  (随机应 ≈25%, 语音 ADPCM 应 >45%)")
    print(f"nibble 均值 {np.mean(cs):.2f} 标准差 {np.std(cs):.2f}\n")

    rows = []
    for low_first, rev_nib, rev_byte, xor in itertools.product(
            [False, True], [False, True], [False, True], [0, 8]):
        cs = codes(data, low_first, rev_nib, rev_byte, xor)
        d = dec_ima(cs)
        sp, low, rail = band_score(d)
        name = (f"IMA {'低先' if low_first else '高先'}"
                f"{' 反nib' if rev_nib else ''}{' 反byte' if rev_byte else ''}"
                f"{' ^8' if xor else ''}")
        rows.append((sp - rail, name, sp, low, rail, d.std()))

    for scale in (256, 512, 1024, 2048, 4096):
        d = dec_pcm4(codes(data), scale)
        sp, low, rail = band_score(d)
        rows.append((sp - rail, f"PCM4 中心8 缩放{scale}", sp, low, rail, d.std()))

    rows.sort(reverse=True)
    print(f"{'候选':30s} {'语音带%':>8s} {'<150Hz%':>8s} {'削顶%':>7s} {'标准差':>8s}")
    for _, name, sp, low, rail, sd in rows[:8]:
        print(f"{name:30s} {sp:8.1f} {low:8.1f} {rail:7.1f} {sd:8.0f}")

    best = rows[0]
    print(f"\n>>> 最优 {best[1]}")
    cs = codes(data, "低先" in best[1])
    d = dec_ima(cs)
    d = d - d.mean()
    peak = np.abs(d).max() or 1
    d = d / peak * 0.9 * 32767
    out = path.replace(".adpcm", "_scan.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(d.astype("<i2").tobytes())
    print(f"写出 {out}")


if __name__ == "__main__":
    main(sys.argv[1])
