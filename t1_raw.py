#!/usr/bin/env python3
"""Raw Input 探测：抓取指定 VID/PID 设备的全部原始输入报告

按 VID_620A&PID_0407 过滤，覆盖 9 个 HID 用途类，解出:
  RAWMOUSE  -> 按键位、X/Y 相对位移、滚轮
  RAWKEYBOARD -> MakeCode / VKey / 按下抬起
  RAWHID    -> 原始报告字节（含 Report ID）

用法:
  python t1_raw.py --seconds 90 --log t1_raw.log
"""
import argparse
import ctypes
import datetime
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD, RIM_TYPEHID = 0, 1, 2

WS_POPUP = 0x80000000
CS_VREDRAW, CS_HREDRAW = 0x0001, 0x0002


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT),
                ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD),
                ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD),
                ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE),
                ("wParam", wintypes.WPARAM)]


class _BtnUnion(ctypes.Union):
    class _S(ctypes.Structure):
        _fields_ = [("usButtonFlags", wintypes.USHORT),
                    ("usButtonData", wintypes.USHORT)]
    _fields_ = [("ulButtons", wintypes.ULONG), ("s", _S)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT),
                ("u", _BtnUnion),
                ("ulRawButtons", wintypes.ULONG),
                ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG),
                ("ulExtraInformation", wintypes.ULONG)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT),
                ("Flags", wintypes.USHORT),
                ("Reserved", wintypes.USHORT),
                ("VKey", wintypes.USHORT),
                ("Message", wintypes.UINT),
                ("ExtraInformation", wintypes.ULONG)]


class RAWHID(ctypes.Structure):
    _fields_ = [("dwSizeHid", wintypes.DWORD),
                ("dwCount", wintypes.DWORD),
                ("bRawData", ctypes.c_ubyte * 1)]


class _DataUnion(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD), ("hid", RAWHID)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", _DataUnion)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
user32.GetRawInputDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                          ctypes.c_void_p, ctypes.POINTER(wintypes.UINT)]

BTN = {0x0001: "左键", 0x0002: "右键", 0x0004: "中键",
       0x0040: "侧键1", 0x0080: "侧键2", 0x0100: "前进", 0x0200: "后退"}

VK_NAME = {0x0D: "Enter", 0x1B: "Esc", 0x08: "Backspace", 0x09: "Tab",
           0x20: "Space", 0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓",
           0x2E: "Delete", 0x24: "Home", 0x23: "End",
           0x21: "PageUp", 0x22: "PageDown", 0x0B: "F1", 0x0C: "F2"}

USAGE_CLASSES = [
    (0x01, 0x02), (0x01, 0x04), (0x01, 0x05), (0x01, 0x06),
    (0x01, 0x07), (0x01, 0x08), (0x01, 0x80), (0x0C, 0x01),
    (0x0D, 0x01), (0x0E, 0x01), (0x0B, 0x01), (0xFF00, 0x00),
    (0xFF00, 0x01), (0xFF60, 0x61),
]


def now():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


class RawCapture:
    def __init__(self, vidpid="VID_620A&PID_0407", log_path=None, verbose_all=False):
        self.vidpid = vidpid.upper()
        self.log_path = log_path
        self.verbose_all = verbose_all
        self.names = {}
        self.hwnd = None
        self.thread_id = None
        self.count = 0
        self.other = 0
        self.stdout_lock = threading.Lock()
        self.alive = True

    @staticmethod
    def is_ours(name):
        """Windows 的 HID 接口路径形如
        \\?\HID#{00001812-...}_Dev_VID&01620a_PID&0407_REV&0000_aabbccdd...
        注意是 VID&01620a 而不是 VID_620A，不能按旧格式匹配"""
        low = (name or "").lower()
        if not low or low == "?":
            return False
        return ("620a" in low and "0407" in low) or "aabbccdd" in low

    def out(self, line):
        with self.stdout_lock:
            print(line, flush=True)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

    # ---- 设备名
    def dev_name(self, h):
        if h in self.names:
            return self.names[h]
        if not h:
            self.names[h] = "<injected>"
            return "<injected>"
        name = "?"
        try:
            size = wintypes.UINT(0)
            r = user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, None,
                                              ctypes.byref(size))
            if r != 0xFFFFFFFF and 0 < size.value < 4096:
                buf = ctypes.create_unicode_buffer(size.value + 2)
                n = user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf,
                                                  ctypes.byref(size))
                if n not in (0, 0xFFFFFFFF):
                    name = buf.value
            else:
                # 回退：固定容量再试一次
                cap = wintypes.UINT(1024)
                buf = ctypes.create_unicode_buffer(1024)
                n = user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf,
                                                  ctypes.byref(cap))
                if n not in (0, 0xFFFFFFFF) and buf.value:
                    name = buf.value
        except Exception:
            name = "?"
        self.names[h] = name
        return name

    def short(self, name):
        # \\?\HID#VID_620A&PID_0407&MI_05&Col02#...  ->  MI_05&Col02
        parts = name.split("#")
        if len(parts) >= 2:
            seg = parts[1]
            for tag in ("MI_", "Col"):
                pass
            return seg.replace("VID_620A&PID_0407", "").strip("&") or seg
        return name[-28:]

    # ---- 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            try:
                self.on_input(lparam)
            except Exception as e:
                self.out("!! 解析异常 %s: %s" % (type(e).__name__, e))
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def on_input(self, lparam):
        size = wintypes.UINT(0)
        hdr_size = ctypes.sizeof(RAWINPUTHEADER)
        user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), hdr_size)
        if size.value == 0:
            return
        buf = ctypes.create_string_buffer(size.value)
        n = user32.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), hdr_size)
        if n == 0xFFFFFFFF or n == 0:
            return
        ri = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        name = self.dev_name(ri.header.hDevice)
        mine = self.is_ours(name)
        unknown = name in ("?", "<injected>")
        if not mine and not unknown and not self.verbose_all:
            self.other += 1
            if self.other % 40 == 1:
                self.out("· %s (其他设备事件累计 %d, 示例 %s)"
                         % (now(), self.other, name[-30:]))
            return

        hdev = ri.header.hDevice or 0
        tag = "★" if mine else ("?" if unknown else "·")
        if mine:
            where = "h%06X" % (hdev & 0xFFFFFF)
            if name not in self.names.get("_shown", set()):
                self.names.setdefault("_shown", set()).add(name)
                self.out("  [设备] h%06X  %s" % (hdev & 0xFFFFFF, name))
        elif unknown:
            where = "h%06X" % (hdev & 0xFFFFFF)
        else:
            where = name[-24:]

        if ri.header.dwType == RIM_TYPEMOUSE:
            m = ri.data.mouse
            flags = m.u.s.usButtonFlags
            btns = " ".join(v for k, v in BTN.items() if flags & k)
            if flags & 0x0400:
                btns += " 滚轮%+d" % ctypes.c_short(m.u.s.usButtonData).value
            if flags & 0x0800:
                btns += " 横向%+d" % ctypes.c_short(m.u.s.usButtonData).value
            if not (m.lLastX or m.lLastY or flags):
                return
            self.out("%s %s MOUSE %-14s dx=%+d dy=%+d  %s"
                     % (tag, now(), where, m.lLastX, m.lLastY, btns))

        elif ri.header.dwType == RIM_TYPEKEYBOARD:
            k = ri.data.keyboard
            if k.VKey in (0, 0xFF):
                return
            down = (k.Flags & 0x01) == 0
            vn = VK_NAME.get(k.VKey, "0x%02X" % k.VKey)
            self.out("%s %s KEY   %-14s vk=%-6s make=0x%02X %s"
                     % (tag, now(), where, vn, k.MakeCode, "↓按下" if down else "↑抬起"))

        elif ri.header.dwType == RIM_TYPEHID:
            hd = ri.data.hid
            cnt = hd.dwCount
            sz = hd.dwSizeHid
            # 可用载荷 = 总大小 - RAWINPUTHEADER - RAWHID 头(8)
            avail = max(0, size.value - ctypes.sizeof(RAWINPUTHEADER) - 8)
            total = min(cnt * sz, avail)
            raw = ctypes.string_at(ctypes.addressof(ri.data) + 8, total) if total else b""
            shown = min(total, 48)
            self.out("%s %s HID   %-14s sizeHid=%d count=%d  [%s]%s"
                     % (tag, now(), where, sz, cnt,
                        " ".join("%02X" % b for b in raw[:shown]),
                        " …" if total > shown else ""))

        self.count += 1

    # ---- 启动
    def start(self):
        hinst = kernel32.GetModuleHandleW(None)
        cls = "T1RawCapWnd"
        self._proc_ref = WNDPROC(self._wndproc)
        wc = WNDCLASSW()
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = ctypes.cast(self._proc_ref, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):  # 1410 = 类已存在
                raise OSError("RegisterClassW 失败 %d" % err)
        # 必须是真实窗口（HWND_MESSAGE 不被 INPUTSINK 接受）
        self.hwnd = user32.CreateWindowExW(
            0, cls, "t1raw", WS_POPUP, -3000, -3000, 8, 8,
            None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW 失败 %d" % ctypes.get_last_error())
        user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        user32.UpdateWindow(self.hwnd)

        devs = (RAWINPUTDEVICE * len(USAGE_CLASSES))()
        ok = 0
        for i, (pg, us) in enumerate(USAGE_CLASSES):
            devs[i].usUsagePage = pg
            devs[i].usUsage = us
            devs[i].dwFlags = RIDEV_INPUTSINK
            devs[i].hwndTarget = self.hwnd
        # 逐个注册，避免一个失败拖垮全部
        for i, (pg, us) in enumerate(USAGE_CLASSES):
            one = (RAWINPUTDEVICE * 1)()
            one[0].usUsagePage = pg
            one[0].usUsage = us
            one[0].dwFlags = RIDEV_INPUTSINK
            one[0].hwndTarget = self.hwnd
            if user32.RegisterRawInputDevices(one, 1, ctypes.sizeof(RAWINPUTDEVICE)):
                ok += 1
                self.out("+ %s armed usage 0x%02X/0x%02X" % (now(), pg, us))
            else:
                self.out("+ %s FAILED usage 0x%02X/0x%02X err=%d"
                         % (now(), pg, us, ctypes.get_last_error()))
        self.out("+ %s ARMED %d/%d usage classes" % (now(), ok, len(USAGE_CLASSES)))

    def loop(self):
        msg = wintypes.MSG()
        while self.alive:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.out("+ %s 消息循环结束" % now())

    def stop(self):
        self.alive = False
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_DESTROY, 0, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--log", default="t1_raw.log")
    ap.add_argument("--all", action="store_true", help="也输出其他设备的报文")
    args = ap.parse_args()

    open(args.log, "w", encoding="utf-8").close()
    cap = RawCapture(log_path=args.log, verbose_all=args.all)
    cap.start()
    cap.thread_id = kernel32.GetCurrentThreadId()

    def killer():
        time.sleep(args.seconds)
        cap.stop()
        time.sleep(0.5)
        user32.PostQuitMessage(0)

    threading.Thread(target=killer, daemon=True).start()
    try:
        cap.loop()
    except KeyboardInterrupt:
        pass
    print("\n==== 共捕获 %d 条本设备报文，忽略其他设备 %d 条 ====" % (cap.count, cap.other))


if __name__ == "__main__":
    main()
