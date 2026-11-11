# -*- coding: utf-8 -*-
"""keysynth.py —— Windows 键鼠合成（SendInput）

给遥控器上位机用的动作执行器。支持三种写法：

    key:ctrl+alt+p            组合键
    key:volup                 单个多媒体/功能键（见 ALIAS）
    text:你好 hello           以 Unicode 方式逐字符键入（不受输入法布局限制）
    open:https://www.bing.com 用系统默认程序打开链接 / 文件
    cmd:notepad               交给 shell 执行

设计要点：
1. 所有合成事件都带 MAGIC 标记（dwExtraInfo）。RawInputWatcher 看到这个标记
   会直接丢弃，避免「遥控器按键 -> 合成按键 -> 又被自己抓回来 -> 再合成」的死循环。
2. 组合键按「先全按下、再逆序松开」执行，这是 Windows 上最稳的顺序。
3. 方向键 / Home / End / PgUp / PgDn / Insert / Delete 必须带 EXTENDEDKEY 标志，
   否则会被识别成小键盘的 2/8 等数字键。

单独运行可直接测试：

    python keysynth.py "ctrl+alt+p"
    python keysynth.py --text "hello"
    python keysynth.py volup
"""
import ctypes
import sys
import time
from ctypes import wintypes

# 合成事件标记：RawInputWatcher 用它识别「自己发出的」事件
MAGIC = 0x54314B59  # 'T1KY'

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

user32 = ctypes.WinDLL("user32", use_last_error=True)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


# ---------------------------------------------------------------- 键名表

# 名字 -> (VK, 是否需要 EXTENDEDKEY)
ALIAS = {
    # 多媒体 / 系统（遥控器最常用的映射目标）
    "volup": (0xAF, True), "volumeup": (0xAF, True),
    "voldown": (0xAE, True), "volumedown": (0xAE, True),
    "mute": (0xAD, True),
    "playpause": (0xB3, True), "mediaplaypause": (0xB3, True),
    "stopmedia": (0xB2, True), "stop": (0xB2, True),
    "nexttrack": (0xB0, True), "next": (0xB0, True),
    "prevtrack": (0xB1, True), "prev": (0xB1, True),
    "mediaselect": (0xB5, True), "browserhome": (0xAC, True),
    "browserback": (0xA6, True), "browserforward": (0xA7, True),
    "browserrefresh": (0xA8, True), "browsersearch": (0xAA, True),
    "launchmail": (0xB4, True), "launchapp1": (0xB6, True),
    "launchapp2": (0xB7, True),
    # 编辑 / 导航
    "enter": (0x0D, False), "return": (0x0D, False),
    "esc": (0x1B, False), "escape": (0x1B, False),
    "tab": (0x09, False), "space": (0x20, False),
    "backspace": (0x08, False), "bksp": (0x08, False),
    "delete": (0x2E, True), "del": (0x2E, True),
    "insert": (0x2D, True), "ins": (0x2D, True),
    "home": (0x24, True), "end": (0x23, True),
    "pgup": (0x21, True), "pageup": (0x21, True), "prior": (0x21, True),
    "pgdn": (0x22, True), "pagedown": (0x22, True), "next_": (0x22, True),
    "up": (0x26, True), "down": (0x28, True),
    "left": (0x25, True), "right": (0x27, True),
    # 修饰键
    "ctrl": (0x11, False), "control": (0x11, False),
    "shift": (0x10, False), "alt": (0x12, False), "menu": (0x12, False),
    "win": (0x5B, True), "lwin": (0x5B, True), "rwin": (0x5C, True),
    "caps": (0x14, False), "capslock": (0x14, False),
    "numlock": (0x90, True), "scrolllock": (0x91, False),
    "printscreen": (0x2C, True), "prtsc": (0x2C, True),
    "pause": (0x13, False), "apps": (0x5D, True),
    # 功能键
    **{"f%d" % i: (0x6F + i, False) for i in range(1, 25)},
}

PRESETS = [
    ("（留空，仅记录）", ""),
    ("音量 +", "key:volup"),
    ("音量 −", "key:voldown"),
    ("静音", "key:mute"),
    ("播放 / 暂停", "key:playpause"),
    ("上一曲", "key:prevtrack"),
    ("下一曲", "key:nexttrack"),
    ("确认 / 回车", "key:enter"),
    ("Esc 退出", "key:esc"),
    ("PageUp 上翻页", "key:pgup"),
    ("PageDown 下翻页", "key:pgdn"),
    ("Alt+Tab 切换窗口", "key:alt+tab"),
    ("Win+D 显示桌面", "key:win+d"),
    ("Win+R 运行", "key:win+r"),
    ("Ctrl+Shift+Esc 任务管理器", "key:ctrl+shift+esc"),
    ("Alt+F4 关闭窗口", "key:alt+f4"),
    ("Ctrl+W 关闭标签页", "key:ctrl+w"),
    ("Ctrl+T 新标签页", "key:ctrl+t"),
    ("Ctrl+Z 撤销", "key:ctrl+z"),
    ("Ctrl+C 复制", "key:ctrl+c"),
    ("Ctrl+V 粘贴", "key:ctrl+v"),
    ("Ctrl+S 保存", "key:ctrl+s"),
    ("F11 全屏", "key:f11"),
    ("打开记事本", "cmd:notepad"),
    ("打开资源管理器", "cmd:explorer"),
    ("打开录音目录", "open:recordings"),
]


def _resolve(name):
    """名字 -> (vk, extended)"""
    n = name.strip().lower()
    if not n:
        return None
    if n in ALIAS:
        return ALIAS[n]
    # 单字符：用 VkKeyScanW 得到带修饰位的虚拟键
    if len(n) == 1:
        r = user32.VkKeyScanW(ctypes.c_wchar(n))
        if r != -1:
            return (r & 0xFF, False)
    # 十六进制：0x7B
    if n.startswith("0x"):
        try:
            return (int(n, 16), False)
        except ValueError:
            pass
    raise ValueError("未知按键名: %s" % name)


def _make_key(vk, up, extended=False):
    flags = KEYEVENTF_KEYUP if up else 0
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=MAGIC)
    return inp


def _send(inputs):
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    n = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if n != len(inputs):
        raise ctypes.WinError(ctypes.get_last_error())
    return n


def send_hotkey(spec, hold_ms=30):
    """'ctrl+alt+p' / 'volup' / 'f11' -> 合成按键并返回实际按下的键名"""
    parts = [p for p in spec.replace("-", "+").split("+") if p.strip()]
    if not parts:
        return []
    keys = []
    for p in parts:
        r = _resolve(p)
        if r is None:
            continue
        keys.append((p.strip(), r[0], r[1]))

    down = [_make_key(vk, False, ext) for _, vk, ext in keys]
    up = [_make_key(vk, True, ext) for _, vk, ext in reversed(keys)]
    _send(down)
    time.sleep(max(hold_ms, 1) / 1000.0)
    _send(up)
    return [n for n, _, _ in keys]


def send_text(text, per_char_ms=2):
    """以 Unicode 方式键入，不受当前输入法/键盘布局影响"""
    if not text:
        return 0
    inputs = []
    for ch in text:
        code = ord(ch)
        # 代理对（emoji 等）拆成两个 UTF-16 单元
        if code > 0xFFFF:
            code -= 0x10000
            units = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
        else:
            units = [code]
        for u in units:
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.ki = KEYBDINPUT(wVk=0, wScan=u,
                                dwFlags=KEYEVENTF_UNICODE, time=0,
                                dwExtraInfo=MAGIC)
            inputs.append(inp)
            inp2 = INPUT(type=INPUT_KEYBOARD)
            inp2.ki = KEYBDINPUT(wVk=0, wScan=u,
                                 dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                                 time=0, dwExtraInfo=MAGIC)
            inputs.append(inp2)
    # 分块发送，避免单次数组过大
    for i in range(0, len(inputs), 64):
        _send(inputs[i:i + 64])
        if per_char_ms:
            time.sleep(per_char_ms / 1000.0)
    return len(text)


def describe(spec):
    """把动作字符串翻译成人话，用在界面上"""
    if not spec:
        return "无动作"
    kind, _, arg = spec.partition(":")
    arg = arg.strip()
    if kind == "key":
        try:
            parts = [p for p in arg.replace("-", "+").split("+") if p.strip()]
            names = []
            for p in parts:
                r = _resolve(p)
                names.append(p.upper() if r else p)
            return "合成按键 " + "+".join(names)
        except Exception:
            return "合成按键 " + arg
    if kind == "text":
        return "键入文本「%s」" % (arg[:24] + ("…" if len(arg) > 24 else ""))
    if kind == "open":
        return "打开 " + arg
    if kind == "cmd":
        return "执行 " + arg
    return spec


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        print("用法: python keysynth.py \"ctrl+alt+p\" | --text 文本 | volup")
        print("预设动作:")
        for label, spec in PRESETS:
            if spec:
                print("  %-24s %s" % (label, spec))
        sys.exit(0)
    if argv[0] in ("--text", "-t"):
        n = send_text(" ".join(argv[1:]))
        print("typed %d chars" % n)
    else:
        spec = " ".join(argv)
        names = send_hotkey(spec)
        print("sent:", "+".join(names))
