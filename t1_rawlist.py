#!/usr/bin/env python3
"""枚举系统全部 Raw Input 设备，打印完整设备接口路径"""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000b

user32.GetRawInputDeviceList.restype = wintypes.UINT
user32.GetRawInputDeviceList.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.UINT),
                                         wintypes.UINT]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
user32.GetRawInputDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                          ctypes.c_void_p, ctypes.POINTER(wintypes.UINT)]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]


TYPE_NAME = {0: "MOUSE", 1: "KEYBOARD", 2: "HID"}


def dev_name(h):
    size = wintypes.UINT(0)
    r = user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, None, ctypes.byref(size))
    if r == 0xFFFFFFFF or size.value == 0 or size.value > 8192:
        return "?"
    buf = ctypes.create_unicode_buffer(size.value + 2)
    n = user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf, ctypes.byref(size))
    return buf.value if n not in (0, 0xFFFFFFFF) else "?"


def dev_info(h):
    class RID_DEVICE_INFO_HID(ctypes.Structure):
        _fields_ = [("dwVendorId", wintypes.DWORD), ("dwProductId", wintypes.DWORD),
                    ("dwVersionNumber", wintypes.DWORD), ("usUsagePage", wintypes.USHORT),
                    ("usUsage", wintypes.USHORT)]

    class RID_DEVICE_INFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("dwType", wintypes.DWORD),
                    ("hid", RID_DEVICE_INFO_HID), ("reserved", ctypes.c_byte * 8)]

    di = RID_DEVICE_INFO()
    di.cbSize = ctypes.sizeof(di)
    cap = wintypes.UINT(ctypes.sizeof(di))
    r = user32.GetRawInputDeviceInfoW(h, RIDI_DEVICEINFO, ctypes.byref(di),
                                      ctypes.byref(cap))
    if r in (0, 0xFFFFFFFF):
        return None
    return di


n = wintypes.UINT(0)
user32.GetRawInputDeviceList(None, ctypes.byref(n), ctypes.sizeof(RAWINPUTDEVICELIST))
arr = (RAWINPUTDEVICELIST * n.value)()
user32.GetRawInputDeviceList(arr, ctypes.byref(n), ctypes.sizeof(RAWINPUTDEVICELIST))

print("共 %d 个 Raw Input 设备\n" % n.value)
print("%-10s %-6s %-9s %-9s %s" % ("hDevice", "类型", "usage", "VID:PID", "设备接口路径"))
print("-" * 130)
for i in range(n.value):
    h = arr[i].hDevice
    t = arr[i].dwType
    name = dev_name(h)
    di = dev_info(h)
    up = us = vid = pid = "-"
    if di and di.dwType == 2:
        up = "0x%04X" % di.hid.usUsagePage
        us = "0x%04X" % di.hid.usUsage
        vid = "%04X" % di.hid.dwVendorId
        pid = "%04X" % di.hid.dwProductId
    mark = "  <<<" if ("620A" in name.upper() or vid == "620A") else ""
    print("%-10s %-6s %-9s %-9s %s%s"
          % ("0x%X" % (h or 0), TYPE_NAME.get(t, str(t)), "%s/%s" % (up, us),
             "%s:%s" % (vid, pid), name[:88], mark))
