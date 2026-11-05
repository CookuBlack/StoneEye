# -*- coding: utf-8 -*-
"""全局快捷键注册。

keyboard 的回调运行在它自己的监听线程里，这里只发 Qt 信号；
连接端使用 QueuedConnection，保证 UI 操作回到 GUI 主线程。
"""
from PySide6.QtCore import QObject, Signal


class HotkeyManager(QObject):
    triggered = Signal()
    status_changed = Signal(str)

    def __init__(self, hotkey: str = "ctrl+alt+q", parent=None):
        super().__init__(parent)
        self._hotkey = hotkey
        self._handle = None
        self._keyboard = None

    def start(self) -> bool:
        try:
            import keyboard
        except ImportError:
            self.status_changed.emit("未安装 keyboard，全局热键不可用（可用托盘菜单框选）")
            return False
        self._keyboard = keyboard
        return self.reload(self._hotkey)

    def reload(self, hotkey: str) -> bool:
        if self._keyboard is None:
            return False
        self._remove()
        try:
            self._handle = self._keyboard.add_hotkey(
                hotkey, self._fire, suppress=False
            )
        except Exception as exc:
            self._handle = None
            self.status_changed.emit(f"热键 {hotkey} 注册失败：{exc}（可能被占用或需管理员权限）")
            return False
        self._hotkey = hotkey
        self.status_changed.emit(f"热键已注册：{hotkey}")
        return True

    def _fire(self):
        # 注意：非 GUI 线程，禁止在此直接创建/操作任何 QWidget
        self.triggered.emit()

    def _remove(self):
        if self._handle is not None and self._keyboard is not None:
            try:
                self._keyboard.remove_hotkey(self._handle)
            except Exception:
                pass
            self._handle = None

    def stop(self):
        self._remove()
        if self._keyboard is not None:
            try:
                self._keyboard.unhook_all()
            except Exception:
                pass
            self._keyboard = None
