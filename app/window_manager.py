# -*- coding: utf-8 -*-
"""贴图窗口管理：创建、摆放、隐藏/恢复、识别结果回派、统一关闭。

V3.2：默认 snapshot 模式把窗口贴在源区域原位（画面已冻结，不存在自截图），
live 模式才错开摆放。框选或重截前会临时隐藏全部贴图，
否则置顶的旧贴图会被 mss 拍进新截图里。
"""
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .coords import ui_scale
from .live_window import LiveWindow, normalize_mode


def pick_position(rect, win_w, win_h, offset):
    """选一个不遮挡源区域、且尽量落在虚拟桌面内的位置（Qt 逻辑坐标）。

    live 窗口若盖在源区域上，持续截图会拍到窗口自己，形成无限镜像。
    """
    s = ui_scale()
    x, y = rect.x / s, rect.y / s
    w, h = rect.w / s, rect.h / s

    screens = list(QGuiApplication.screens())
    if screens:
        left = min(sc.geometry().left() for sc in screens)
        top = min(sc.geometry().top() for sc in screens)
        right = max(sc.geometry().right() for sc in screens)
        bottom = max(sc.geometry().bottom() for sc in screens)
    else:
        left, top, right, bottom = 0, 0, 3840, 2160

    candidates = [
        (x + w + offset, y),            # 右侧
        (x - win_w - offset, y),         # 左侧
        (x, y + h + offset),             # 下方
        (x, y - win_h - offset),         # 上方
    ]
    for cx, cy in candidates:
        if (cx >= left and cy >= top
                and cx + win_w <= right and cy + win_h <= bottom):
            return (round(cx), round(cy))

    cx = max(left, min(x + w + offset, max(left, right - win_w)))
    cy = max(top, min(y, max(top, bottom - win_h)))
    return (round(cx), round(cy))


class WindowManager(QObject):
    count_changed = Signal(int)

    def __init__(self, service, cfg, parent=None):
        super().__init__(parent)
        self._service = service
        self.cfg = cfg
        self._windows = {}
        self._restore = []
        self._next_id = 1
        self._last_text = ""
        service.result_ready.connect(self._dispatch_result, Qt.QueuedConnection)
        service.progress_changed.connect(self._dispatch_progress, Qt.QueuedConnection)

    def create(self, rect, initial_frame):
        wid = self._next_id
        self._next_id += 1

        frame = dict(initial_frame)
        frame["window_id"] = wid
        frame["frame_id"] = 1

        st = self.cfg["sticker"]
        mode = normalize_mode(self.cfg["live"].get("capture_mode"))
        s = ui_scale()
        win_w = max(1, round(frame["width"] * float(st["default_zoom"]) / s))
        win_h = max(1, round(frame["height"] * float(st["default_zoom"]) / s))

        if mode == "snapshot" and bool(st.get("place_at_source", True)):
            # 画面已定格，直接贴在源区域原位，像把屏幕「揭」下来一样
            pos = (round(rect.x / s), round(rect.y / s))
        else:
            pos = pick_position(rect, win_w, win_h, int(st.get("source_offset", 24)))

        window = LiveWindow(wid, frame, self.cfg, pos, rect, self._service)
        window.before_capture = self.hide_all
        window.after_capture = self.show_all
        window.closed.connect(self._on_closed)
        self._windows[wid] = window

        window.show()
        window.raise_()
        # show 之后才有有效 HWND；live 模式此时启动采集线程并设置防自截图
        window.set_mode(mode, quiet=True, force=True)
        self._service.enqueue(frame)       # 首帧立刻进识别队列
        self.count_changed.emit(len(self._windows))
        return window

    def hide_all(self):
        """框选 / 重截前临时隐藏所有贴图，避免把旧贴图拍进新截图。"""
        if self._restore:
            return
        self._restore = [w for w in list(self._windows.values()) if w.isVisible()]
        for window in self._restore:
            window.hide()
        QApplication.processEvents()

    def show_all(self):
        for window in self._restore:
            # 隐藏期间被关闭的窗口不能复活
            if not window._closed_emitted and not window.isVisible():
                window.show()
        self._restore = []

    def _dispatch_result(self, wid, highlights, text, frame_id, line_count):
        window = self._windows.get(wid)
        if text:
            # 留给控制面板「复制识别文本」：漏标时先确认是 OCR 错字还是词表缺词
            self._last_text = str(text)
        if window is not None:
            window.apply_ocr(highlights, text, frame_id, line_count)

    def latest_text(self) -> str:
        return self._last_text

    def _dispatch_progress(self, wid, done, total, stage):
        window = self._windows.get(wid)
        if window is not None:
            window.apply_progress(done, total, stage)

    def apply_config_all(self, cfg=None):
        """只把新配置套到窗口上（配色 / 状态条 / 采集参数），不重跑 OCR。

        控制面板拖滑块调性能参数时走这条路径：没必要为一次采样间隔
        调整把所有窗口的 OCR 全部重跑一遍。
        """
        cfg = cfg or self.cfg
        self.cfg = cfg
        for window in list(self._windows.values()):
            window.apply_config(cfg)

    def set_mode_all(self, mode, quiet=True):
        """统一切换快照 / 实时模式，不强制重新识别。"""
        for window in list(self._windows.values()):
            window.set_mode(mode, quiet=quiet)

    def request_recognize_all(self, cfg=None):
        """配置热加载：新词表 / 配色 / 状态条位置 / 采集模式立刻对全部窗口生效。"""
        cfg = cfg or self.cfg
        self.cfg = cfg
        mode = normalize_mode(cfg["live"].get("capture_mode"))
        for window in list(self._windows.values()):
            window.apply_config(cfg)
            window.set_mode(mode, quiet=True)
            window.request_recognize(quiet=True)

    def _on_closed(self, wid):
        self._windows.pop(wid, None)
        self._restore = [w for w in self._restore if w.window_id != wid]
        self._service.stop_window(wid)
        self.count_changed.emit(len(self._windows))

    def close_all(self):
        self.show_all()      # 若正处于隐藏态，先恢复再逐个关闭
        for window in list(self._windows.values()):
            window.close()
        self._windows.clear()
        self.count_changed.emit(0)

    def count(self) -> int:
        return len(self._windows)
