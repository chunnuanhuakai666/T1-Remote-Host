#!/usr/bin/env python3
"""调试 GetRawInputDeviceInfoW 的设备名获取"""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000b
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
WM_INPUT = 0x00FF

user32.GetRawInputData.restype = wintypes.UINT
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]

# 不设 argtypes，手动指定每种调用的签名，便于观察返回值
_DevInfoW = ctypes.WinDLL("user32").GetRawInputDeviceInfoW
_DevInfoW.restype = ctypes.c_uint
_DevInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                      ctypes.POINTER(wintypes.UINT)]

seen = []


def probe(h):
    print("\n--- hDevice = 0x%X ---" % (h or 0))

    # 方式一：两段式，宽字符
    size = wintypes.UINT(0)
    ctypes.set_last_error(0)
    r = _DevInfoW(h, RIDI_DEVICENAME, None, ctypes.byref(size))
    print("  [name] 1st call -> ret=%d size=%d err=%d" % (r, size.value,
                                                          ctypes.get_last_error()))
    if 0 < size.value < 4096:
        buf = ctypes.create_unicode_buffer(size.value + 2)
        ctypes.set_last_error(0)
        r2 = _DevInfoW(h, RIDI_DEVICENAME, buf, ctypes.byref(size))
        print("  [name] 2nd call -> ret=%d err=%d  value=%r"
              % (r2, ctypes.get_last_error(), buf.value))

    # 方式二：直接给一个足够大的缓冲区，pcbSize 传入容量
    cap = wintypes.UINT(2048)
    buf2 = ctypes.create_unicode_buffer(2048)
    ctypes.set_last_error(0)
    r3 = _DevInfoW(h, RIDI_DEVICENAME, buf2, ctypes.byref(cap))
    print("  [name] direct -> ret=%d size=%d err=%d  value=%r"
          % (r3, cap.value, ctypes.get_last_error(), buf2.value[:90]))

    # 设备信息（含类型）
    class RID_DEVICE_INFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("dwType", wintypes.DWORD),
                    ("pad", ctypes.c_byte * 32)]
    di = RID_DEVICE_INFO()
    di.cbSize = ctypes.sizeof(di)
    cap2 = wintypes.UINT(ctypes.sizeof(di))
    ctypes.set_last_error(0)
    r4 = _DevInfoW(h, RIDI_DEVICEINFO, ctypes.byref(di), ctypes.byref(cap2))
    print("  [info] ret=%d type=%d err=%d raw=%s"
          % (r4, di.dwType, ctypes.get_last_error(),
             bytes(di.pad[:16]).hex(" ")))


def wndproc(hwnd, msg, wp, lp):
    if msg == WM_INPUT:
        size = wintypes.UINT(0)
        user32.GetRawInputData(lp, RID_INPUT, None, ctypes.byref(size),
                               ctypes.sizeof(ctypes.c_void_p) * 3)
        buf = ctypes.create_string_buffer(size.value or 64)
        n = user32.GetRawInputData(lp, RID_INPUT, buf, ctypes.byref(size),
                                   ctypes.sizeof(ctypes.c_void_p) * 3)
        if n not in (0, 0xFFFFFFFF):
            dwType, dwSize = ctypes.c_uint32.from_buffer_copy(buf, 0).value, \
                ctypes.c_uint32.from_buffer_copy(buf, 4).value
            hDev = ctypes.c_uint64.from_buffer_copy(buf, 8).value
            key = (dwType, hDev)
            if key not in seen:
                seen.append(key)
                print("WM_INPUT type=%d dwSize=%d" % (dwType, dwSize))
                probe(hDev)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


proc_ref = WNDPROC(wndproc)
wc = WNDCLASSW()
wc.style = 0x0002 | 0x0001
wc.lpfnWndProc = ctypes.cast(proc_ref, ctypes.c_void_p)
wc.hInstance = kernel32.GetModuleHandleW(None)
wc.lpszClassName = "T1RawDbg"
user32.RegisterClassW(ctypes.byref(wc))
hwnd = user32.CreateWindowExW(0, "T1RawDbg", "dbg", 0x80000000,
                              -3000, -3000, 8, 8, None, None,
                              kernel32.GetModuleHandleW(None), None)
print("hwnd =", hwnd)
user32.ShowWindow(hwnd, 4)
user32.UpdateWindow(hwnd)

devs = (RAWINPUTDEVICE * 2)()
devs[0].usUsagePage = 0x01; devs[0].usUsage = 0x02
devs[0].dwFlags = RIDEV_INPUTSINK; devs[0].hwndTarget = hwnd
devs[1].usUsagePage = 0x01; devs[1].usUsage = 0x06
devs[1].dwFlags = RIDEV_INPUTSINK; devs[1].hwndTarget = hwnd
print("register:", user32.RegisterRawInputDevices(devs, 2, ctypes.sizeof(RAWINPUTDEVICE)))
print("请动一下鼠标/按几个键，8 秒后结束…")

import threading
import time
threading.Thread(target=lambda: (time.sleep(8), user32.PostQuitMessage(0)),
                 daemon=True).start()
msg = wintypes.MSG()
while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    user32.TranslateMessage(ctypes.byref(msg))
    user32.DispatchMessageW(ctypes.byref(msg))
print("done, devices seen:", len(seen))
