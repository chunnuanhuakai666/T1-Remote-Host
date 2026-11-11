#!/usr/bin/env python3
"""ATVV 音频编解码工具链

  IMA ADPCM 4-bit -> 16-bit PCM，含去漂移高通 / 归一化
  python codec.py in.adpcm out.wav [--skip 4] [--hp 120] [--no-norm] [--stats]
"""
import argparse
import math
import struct
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


def ima_decode(data):
    """标准 IMA ADPCM 4-bit 解码，返回 int 列表（含漂移）"""
    pred, index = 0, 0
    out = []
    ap = out.append
    for byte in data:
        for nib in ((byte >> 4) & 0x0F, byte & 0x0F):
            step = IMA_STEP[index]
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
            index += IMA_INDEX[nib]
            if index < 0:
                index = 0
            elif index > 88:
                index = 88
            ap(pred)
    return out


def hpf_biquad(x, fc, fs, order2=True):
    """RBJ 2nd-order Butterworth 高通（去 120Hz 以下的漂移）"""
    w0 = 2.0 * math.pi * fc / fs
    cs = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * 0.7071067811865476)
    a0 = 1.0 + alpha
    b0 = (1.0 + cs) / 2.0 / a0
    b1 = -(1.0 + cs) / a0
    b2 = (1.0 + cs) / 2.0 / a0
    a1 = -2.0 * cs / a0
    a2 = (1.0 - alpha) / a0
    y = [0.0] * len(x)
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x):
        o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, v
        y2, y1 = y1, o
        y[i] = o
    if order2:
        # 再串一级，得到 4 阶，裙边更陡
        x1 = x2 = y1 = y2 = 0.0
        for i, v in enumerate(y):
            o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, v
            y2, y1 = y1, o
            y[i] = o
    return y


def normalize(x, peak_target=0.85):
    peak = max(abs(v) for v in x) if x else 0
    if peak <= 0:
        return x
    k = (peak_target * 32767.0) / peak
    return [v * k for v in x]


def write_wav(path, samples, sr=16000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        buf = struct.pack("<%dh" % len(samples),
                          *[max(-32768, min(32767, int(v))) for v in samples])
        w.writeframes(buf)


def decode_stream(raw, skip=4, hp=120.0, sr=16000, norm=True):
    body = raw[skip:] if skip and len(raw) > skip * 2 else raw
    d = ima_decode(bytes(body))
    d = hpf_biquad(d, hp, sr) if hp else d
    if norm:
        d = normalize(d)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--skip", type=int, default=4)
    ap.add_argument("--hp", type=float, default=120.0)
    ap.add_argument("--no-norm", action="store_true")
    args = ap.parse_args()

    raw = open(args.src, "rb").read()
    d = decode_stream(raw, args.skip, args.hp, norm=not args.no_norm)
    write_wav(args.dst, d)
    print(f"{args.src} -> {args.dst}  {len(d)/16000:.2f}s  {len(d)} 采样")


if __name__ == "__main__":
    main()
