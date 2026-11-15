# -*- coding: utf-8 -*-
"""用 Interception 驱动直读并拦截指定键盘（遥控器）。

背景：遥控器是标准 BLE HID 键盘，Windows 会把按键送给前台窗口；
我们如果只是"旁路偷听"，就会出现"原功能 + 映射功能"双触发。
用户态没有只针对某台设备吞键的 API，所以这里用 Interception 驱动：
按键在进入 Windows 输入队列之前先到我们手里，判断后决定转发还是丢弃。

设备识别：`interception_get_hardware_id` 对蓝牙 HID 键盘可能返回空，
所以先用硬件 ID 匹配，匹配不到再退化到「启动时已存在的那台设备 id」。

注意：驱动要求调用进程以管理员身份运行；演示版每次开机只有 1 小时。
"""

import ctypes
import os
import threading
import time

FILTER_KEY_ALL = 0xFFFF
KEY_UP = 0x01          # state & 1
KEY_E0 = 0x02          # 扩展键前缀
KEY_E1 = 0x04

MAPVK_VSC_TO_VK = 1

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DLL = os.path.join(HERE, "tools", "interception", "Interception",
                           "library", "x64", "interception.dll")


class _KeyStroke(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort),
                ("state", ctypes.c_ushort),
                ("information", ctypes.c_uint)]


class _MouseStroke(ctypes.Structure):
    _fields_ = [("unit", ctypes.c_ushort),
                ("flags", ctypes.c_ushort),
                ("rolling", ctypes.c_short),
                ("x", ctypes.c_int),
                ("y", ctypes.c_int),
                ("information", ctypes.c_uint)]


class _Stroke(ctypes.Union):
    _fields_ = [("key", _KeyStroke), ("mouse", _MouseStroke)]


def _load(dll_path=None):
    path = dll_path or DEFAULT_DLL
    if not os.path.isfile(path):
        return None
    lib = ctypes.WinDLL(path)
    lib.interception_create_context.restype = ctypes.c_void_p
    lib.interception_destroy_context.argtypes = [ctypes.c_void_p]
    lib.interception_is_keyboard.argtypes = [ctypes.c_int]
    lib.interception_is_keyboard.restype = ctypes.c_bool
    lib.interception_set_filter.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_ushort]
    lib.interception_set_filter.restype = ctypes.c_bool
    lib.interception_get_filter.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.interception_get_filter.restype = ctypes.c_ushort
    lib.interception_wait_with_timeout.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.interception_wait_with_timeout.restype = ctypes.c_int
    lib.interception_receive.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                         ctypes.POINTER(_Stroke), ctypes.c_uint]
    lib.interception_receive.restype = ctypes.c_int
    lib.interception_send.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                      ctypes.POINTER(_Stroke), ctypes.c_uint]
    lib.interception_send.restype = ctypes.c_int
    lib.interception_get_hardware_id.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                 ctypes.c_void_p, ctypes.c_uint]
    lib.interception_get_hardware_id.restype = ctypes.c_uint
    return lib


def _hwid(lib, ctx, dev):
    buf = ctypes.create_unicode_buffer(512)
    n = lib.interception_get_hardware_id(ctx, dev, buf, ctypes.sizeof(buf) - 2)
    return buf.value if n > 2 else ""


def scancode_to_vk(code, state):
    """扫描码 -> 虚拟键码（扩展键要带上 0xE000 前缀）。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    sc = code | (0xE000 if state & KEY_E0 else 0)
    vk = user32.MapVirtualKeyW(sc, MAPVK_VSC_TO_VK)
    if vk:
        return int(vk)
    return 0


class InterceptionKeyboard(threading.Thread):
    """把遥控器的按键事件交给回调，并可按回调结果丢键。

    on_key(vk, down, dev) -> True 表示"拦下，不转发"，False 表示放行。
    ready 事件在初始化完成后 set，可用 ready.wait(3) 等它启动。
    """

    def __init__(self, on_key, match="", log=None, dll_path=None, on_dead=None):
        super().__init__(daemon=True)
        self.on_key = on_key
        self.match = (match or "").upper()
        self.log = log or (lambda *a, **k: None)
        self.dll_path = dll_path
        self.on_dead = on_dead
        self.ready = threading.Event()
        self.ok = False
        self.dev = 0
        self.stop_flag = threading.Event()
        self.count = 0
        self.blocked = 0
        self.dead = False        # 演示期到期 / 驱动异常，已停止投递
        self.started_at = 0.0    # 上下文创建时刻，用来推算演示期剩余
        self._budget_warned = False

    def _pick_device(self, lib, ctx):
        found = []
        for dev in range(1, 11):
            hwid = _hwid(lib, ctx, dev)
            if hwid:
                found.append((dev, hwid))
        self.log("Interception 键盘设备: " +
                 ("; ".join("id=%d %s" % (d, h) for d, h in found) or "无"), "mut")
        if self.match:
            for dev, hwid in found:
                if self.match in hwid.upper():
                    return dev
        # 蓝牙 HID 键盘的 hardware id 可能是空的：退化到「没有硬件 ID 的那一台」
        for dev in range(1, 11):
            if lib.interception_is_keyboard(dev) and not _hwid(lib, ctx, dev):
                return dev
        if len(found) == 1:
            return found[0][0]
        return 0

    def run(self):
        lib = _load(self.dll_path)
        if lib is None:
            self.log("没找到 interception.dll，按键拦截不可用", "warn")
            self.ready.set()
            return
        ctx = lib.interception_create_context()
        if not ctx:
            self.log("Interception 上下文创建失败（需要管理员权限/演示期未激活）", "warn")
            self.ready.set()
            return
        try:
            self.dev = self._pick_device(lib, ctx)
            if not self.dev:
                self.log("没认出遥控器对应的键盘设备，按键拦截不启用", "warn")
                self.ready.set()
                return
            self.log("按键拦截绑定到设备 id=%d（%s）"
                     % (self.dev, _hwid(lib, ctx, self.dev) or "无 hardware id"), "ok")
            lib.interception_set_filter(ctx, ctypes.cast(lib.interception_is_keyboard,
                                                         ctypes.c_void_p),
                                        FILTER_KEY_ALL)
            self.ok = True
            self.ready.set()

            st = _Stroke()
            self.started_at = time.time()
            starved = 0          # 被唤醒却收不到事件的连续次数
            while not self.stop_flag.is_set():
                # 演示版上限是「每次 Windows 启动后 1 小时，从首次成功创建立上下文算起」，
                # 到点不会报错，只是不再投递。提前给个提示，免得用着用着双触发突然回来。
                if not self._budget_warned and time.time() - self.started_at > 55 * 60:
                    self._budget_warned = True
                    self.log("Interception 演示期约剩 5 分钟：到期后映射照常，但原功能会一起触发",
                             "warn")
                dev = lib.interception_wait_with_timeout(ctx, 300)
                if not dev:
                    starved = 0
                    continue
                got = 0
                # 别的键盘、鼠标：原样放行
                if dev != self.dev:
                    while lib.interception_receive(ctx, dev, ctypes.byref(st), 1) == 1:
                        lib.interception_send(ctx, dev, ctypes.byref(st), 1)
                        got += 1
                else:
                    while lib.interception_receive(ctx, dev, ctypes.byref(st), 1) == 1:
                        got += 1
                        k = st.key
                        down = not (k.state & KEY_UP)
                        vk = scancode_to_vk(k.code, k.state)
                        self.count += 1
                        try:
                            drop = bool(vk) and bool(self.on_key(vk, down, dev))
                        except Exception as e:
                            self.log("按键回调异常: %s" % e, "err")
                            drop = False
                        if drop:
                            self.blocked += 1
                        else:
                            lib.interception_send(ctx, dev, ctypes.byref(st), 1)
                if got:
                    starved = 0
                    continue
                # 被唤醒却没拿到任何事件：驱动已停止投递（演示期到期或驱动异常）。
                # 不处理的话这里会立刻再 wait、再空转，把 CPU 吃满。
                if starved == 0:
                    self.log("Interception 唤醒后收不到按键，可能在退出（演示期到期或驱动异常）", "mut")
                starved += 1
                if starved == 40:
                    self.log("Interception 已停止投递：按键退回透传，映射仍生效但原功能会同时触发",
                             "warn")
                if starved > 40:
                    self.dead = True
                    break
                time.sleep(0.02)
        finally:
            lib.interception_destroy_context(ctx)
            self.ready.set()
            if self.dead and self.on_dead:
                try:
                    self.on_dead()
                except Exception:
                    pass

    def stop(self):
        self.stop_flag.set()


if __name__ == "__main__":
    # 独立自测：拦下遥控器的键（打印但不转发），其他键盘照常放行
    import sys
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0

    def on_key(vk, down, dev):
        print("  vk=0x%02X %s" % (vk, "down" if down else "up"), flush=True)
        return True          # 全拦，用于验证

    kb = InterceptionKeyboard(on_key, log=lambda t, l="info": print("[%s] %s" % (l, t)))
    kb.start()
    kb.ready.wait(5)
    print("ok=%s dev=%s；抓 %.0fs（遥控器按键会被拦下）" % (kb.ok, kb.dev, secs))
    t0 = time.time()
    while time.time() - t0 < secs:
        time.sleep(0.5)
    kb.stop()
    time.sleep(0.6)
    print("事件 %d 个，拦截 %d 个" % (kb.count, kb.blocked))
