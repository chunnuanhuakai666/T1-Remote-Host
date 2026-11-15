# -*- coding: utf-8 -*-
"""t1_tray.py —— 系统托盘常驻

上位机跑在托盘区（桌面右下角），无窗口、无任务栏按钮。
左键单击 = 打开界面；右键 = 菜单（连接/暂停/退出）。

用法（t1_server.py --tray）：
    tray = Tray(url, state_fn, connect_fn, disconnect_fn, pause_fn, quit_fn)
    tray.run()          # 阻塞主线程，直到用户点「退出」
"""
import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw

_C_OK = (34, 158, 104)      # 已连接
_C_IDLE = (128, 128, 128)   # 未连接
_C_BUSY = (58, 110, 200)    # 连接中 / 断链重连中
_C_PAUSE = (214, 140, 30)   # 暂停映射


def _make_image(color):
    """64x64 圆形底 + 白色麦克风"""
    s = 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, s - 3, s - 3), fill=color + (255,))
    white = (255, 255, 255, 245)
    d.rounded_rectangle((26, 12, 38, 36), radius=6, fill=white)
    d.arc((19, 19, 45, 45), start=0, end=180, fill=white, width=4)
    d.line((32, 45, 32, 51), fill=white, width=4)
    d.line((23, 52, 41, 52), fill=white, width=4)
    return img


class Tray:
    """
    state_fn()      -> {"connected": bool, "paused": bool, "name": str}
    connect_fn()    -> 发起连接
    disconnect_fn() -> 断开
    pause_fn(flag)  -> 设置暂停
    quit_fn()       -> 退出前的清理
    """

    def __init__(self, url, state_fn, connect_fn, disconnect_fn,
                 pause_fn, quit_fn, log=None):
        self.url = url
        self.state_fn = state_fn
        self.connect_fn = connect_fn
        self.disconnect_fn = disconnect_fn
        self.pause_fn = pause_fn
        self.quit_fn = quit_fn
        self.log = log or (lambda *a, **k: None)
        self._last = None
        self._icon = pystray.Icon(
            "t1_remote", _make_image(_C_IDLE), "T1-Remote 上位机",
            menu=pystray.Menu(
                pystray.MenuItem("打开界面", self._open, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(self._status_text, None, enabled=False),
                pystray.MenuItem(self._conn_text, self._toggle_conn),
                pystray.MenuItem("暂停映射", self._toggle_pause,
                                 checked=lambda i: self.state_fn()["paused"]),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._quit),
            ),
        )

    # ---- 菜单文案（pystray 每帧回调，返回实时状态）
    def _status_text(self, item=None):
        st = self.state_fn()
        if st["connected"]:
            return "● 已连接 %s" % (st.get("name") or "T1")
        if st.get("busy"):
            return "◌ 连接中 / 等待唤醒"
        return "○ 未连接"

    def _conn_text(self, item=None):
        st = self.state_fn()
        if st["connected"]:
            return "断开连接"
        return "立即重连" if st.get("busy") else "连接设备"

    # ---- 动作
    def _open(self, icon=None, item=None):
        try:
            webbrowser.open(self.url)
        except Exception as e:
            self.log("打开浏览器失败: %s" % e, "warn")

    def _toggle_conn(self, icon=None, item=None):
        try:
            if self.state_fn()["connected"]:
                self.disconnect_fn()
            else:
                self.connect_fn()
        except Exception as e:
            self.log("托盘操作失败: %s" % e, "err")
        # 状态变化有延迟，交给刷新线程处理

    def _toggle_pause(self, icon=None, item=None):
        try:
            self.pause_fn(not self.state_fn()["paused"])
        except Exception as e:
            self.log("托盘操作失败: %s" % e, "err")

    def _quit(self, icon=None, item=None):
        try:
            self.quit_fn()
        except Exception:
            pass
        self._icon.stop()

    # ---- 图标颜色跟随状态
    def _refresh_loop(self):
        while True:
            threading.Event().wait(1.5)
            try:
                st = self.state_fn()
            except Exception:
                continue
            if st["paused"]:
                color = _C_PAUSE
            elif st["connected"]:
                color = _C_OK
            elif st.get("busy"):
                color = _C_BUSY
            else:
                color = _C_IDLE
            sig = (color, st["connected"], st.get("busy"), st["paused"], st.get("name"))
            if sig != self._last:
                self._last = sig
                try:
                    self._icon.icon = _make_image(color)
                    if st["connected"]:
                        phase = "已连接"
                    elif st.get("busy"):
                        phase = "连接中·等待唤醒"
                    else:
                        phase = "未连接"
                    self._icon.title = "T1-Remote · %s%s" % (
                        phase, "（暂停）" if st["paused"] else "")
                    self._icon.update_menu()
                except Exception:
                    pass

    def run(self):
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        self._icon.run()
