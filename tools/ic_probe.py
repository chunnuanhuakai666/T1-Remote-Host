# -*- coding: utf-8 -*-
"""Interception 探测：列出键盘设备 + 抓几秒钟按键（全部原样转发，不拦截）。

用法：python tools/ic_probe.py [秒数]
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

DLL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "interception", "Interception", "library", "x64", "interception.dll")

FILTER_KEY_ALL = 0xFFFF
KEY_DOWN, KEY_UP = 0x00, 0x01


class KeyStroke(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort),
                ("state", ctypes.c_ushort),
                ("information", ctypes.c_uint)]


class MouseStroke(ctypes.Structure):
    _fields_ = [("unit", ctypes.c_ushort),
                ("flags", ctypes.c_ushort),
                ("rolling", ctypes.c_short),
                ("x", ctypes.c_int),
                ("y", ctypes.c_int),
                ("information", ctypes.c_uint)]


class Stroke(ctypes.Union):
    _fields_ = [("key", KeyStroke), ("mouse", MouseStroke)]


lib = ctypes.WinDLL(DLL)
lib.interception_create_context.restype = ctypes.c_void_p
lib.interception_destroy_context.argtypes = [ctypes.c_void_p]
lib.interception_is_keyboard.argtypes = [ctypes.c_int]
lib.interception_is_keyboard.restype = ctypes.c_bool
lib.interception_set_filter.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort]
lib.interception_set_filter.restype = ctypes.c_bool
lib.interception_wait_with_timeout.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
lib.interception_wait_with_timeout.restype = ctypes.c_int
lib.interception_receive.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.POINTER(Stroke), ctypes.c_uint]
lib.interception_receive.restype = ctypes.c_int
lib.interception_send.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  ctypes.POINTER(Stroke), ctypes.c_uint]
lib.interception_send.restype = ctypes.c_int
lib.interception_get_hardware_id.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_void_p, ctypes.c_uint]
lib.interception_get_hardware_id.restype = ctypes.c_uint

KB_PRED = ctypes.cast(lib.interception_is_keyboard, ctypes.c_void_p)


def hwid(ctx, dev):
    buf = ctypes.create_unicode_buffer(512)
    n = lib.interception_get_hardware_id(ctx, dev, buf, ctypes.sizeof(buf) - 2)
    return buf.value if n else ""


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    ctx = lib.interception_create_context()
    if not ctx:
        print("create_context 失败：驱动没装好或没以管理员运行")
        return 1

    print("键盘设备：")
    for dev in range(1, 11):
        h = hwid(ctx, dev)
        if h:
            print("  id=%d  %s" % (dev, h))

    ok = lib.interception_set_filter(ctx, KB_PRED, FILTER_KEY_ALL)
    print("set_filter =", ok, " 抓 %.1fs，全部原样转发" % secs)

    st = Stroke()
    t0 = time.time()
    n = 0
    try:
        while time.time() - t0 < secs:
            dev = lib.interception_wait_with_timeout(ctx, 300)
            if not dev:
                continue
            if not lib.interception_is_keyboard(dev):
                lib.interception_send(ctx, dev, ctypes.byref(st), 1)
                continue
            while lib.interception_receive(ctx, dev, ctypes.byref(st), 1) == 1:
                k = st.key
                n += 1
                print("  dev=%d code=0x%02X state=%s info=0x%X  (hwid=%s)"
                      % (dev, k.code, "down" if k.state & 1 == 0 else "up",
                         k.information, hwid(ctx, dev)[-14:]))
                lib.interception_send(ctx, dev, ctypes.byref(st), 1)
    finally:
        lib.interception_destroy_context(ctx)
    print("共 %d 个按键事件" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
