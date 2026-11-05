# -*- coding: utf-8 -*-
"""全屏框选层（V3.8：纯手动拖拽）。

交互只有一种：**按住左键拖动**，松开确认。Esc 或右键取消。
不再做任何窗口自动识别 / 预测框（V3.7 的智能预测实测不可用，已移除）。

覆盖整个虚拟桌面（多显示器并集），选区做「挖空」处理以便看清原内容。
select() 用局部 QEventLoop 阻塞等待，返回物理像素 Rect 或 None。

为什么之前「手动框选总是失败」，这次的做法：
  - 不调用 grabMouse/grabKeyboard。Qt 在按下时会自动建立隐式捕获，
    再显式 grab 反而会让 release 落到别的窗口，表现为「松手后毫无反应」；
  - 状态机只有 idle / drag 两态，按下即进入拖拽，没有「长按等待」这种
    容易被手抖破坏的中间态；
  - 所有事件处理都做了空值防护，不会因为一次异常把状态卡死；
  - 先 hide 再等 120ms，确保调用方截图时遮罩已经彻底从屏幕上撤掉。
"""
import time

from PySide6.QtCore import Qt, QRect, QRectF, QEventLoop
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QFontMetrics, QGuiApplication,
)
from PySide6.QtWidgets import QWidget, QApplication

from .coords import to_physical
from .screen_capture import Rect

_MASK_ALPHA = 112
_CAPTURE_DELAY_MS = 120
_MIN_SELECT = 6               # 小于这个尺寸视为误点，直接取消
_TIP_IDLE = "按住鼠标左键拖动选择区域   ·   Esc / 右键取消"
_TIP_DRAG = "松开确认选区   ·   Esc 取消"


def _global_pos(event):
    """兼容 PySide6 新旧版本的鼠标全局坐标获取。"""
    try:
        return event.globalPosition().toPoint()
    except AttributeError:
        return event.globalPos()


def _virtual_geometry() -> QRect:
    screens = list(QGuiApplication.screens())
    if not screens:
        return QRect(0, 0, 1920, 1080)
    left = min(s.geometry().left() for s in screens)
    top = min(s.geometry().top() for s in screens)
    right = max(s.geometry().right() for s in screens)
    bottom = max(s.geometry().bottom() for s in screens)
    return QRect(left, top, right - left + 1, bottom - top + 1)


class RegionSelector(QWidget):
    """一次性使用的框选窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._dragging = False
        self._origin = None       # QPoint，全局逻辑坐标
        self._current = None
        self._result = None       # Rect | None
        self._loop = None
        self._finished = False

        geom = _virtual_geometry()
        self._offset = geom.topLeft()   # 窗口局部坐标 -> 全局坐标的偏移
        self.setGeometry(geom)

    # ---------- 绘制 ----------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(0, 0, 0, _MASK_ALPHA))

        rect = self._active_rect()
        if rect is not None:
            local = rect.translated(-self._offset.x(), -self._offset.y())
            local = local.intersected(self.rect())
            if local.width() > 0 and local.height() > 0:
                # 选区挖空：Clear 把该区域 alpha 置 0，透出下层真实画面
                p.setCompositionMode(QPainter.CompositionMode_Clear)
                p.fillRect(local, QColor(0, 0, 0, 0))
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)

                p.setPen(QPen(QColor(0, 229, 255), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(local.adjusted(0, 0, -1, -1))
                self._paint_size(p, rect, local)

        self._paint_tip(p)

    def _active_rect(self):
        if self._origin is None or self._current is None:
            return None
        # 自己算宽高，不用 QRect(QPoint, QPoint)：那个构造把右下角算作「包含」，
        # 同样拖 300px 会得到 301，截出来的图总比框多 1px。
        x1, y1 = self._origin.x(), self._origin.y()
        x2, y2 = self._current.x(), self._current.y()
        w, h = abs(x2 - x1), abs(y2 - y1)
        if w <= 0 or h <= 0:
            return None
        return QRect(min(x1, x2), min(y1, y2), w, h)

    def _paint_tip(self, p: QPainter):
        tip = _TIP_DRAG if self._dragging else _TIP_IDLE
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        p.setFont(font)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(tip) + 26
        h = fm.height() + 12
        x = (self.width() - w) // 2
        y = 24
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(QColor(18, 20, 26, 215))
        p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
        p.setPen(QColor(235, 245, 255))
        p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, tip)

    @staticmethod
    def _paint_size(p: QPainter, r: QRect, local: QRect):
        label = f"{r.width()} x {r.height()}"
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(label) + 16
        h = fm.height() + 8
        x = max(0, min(local.left(), p.window().width() - w))
        y = local.top() - h - 6
        if y < 0:
            y = min(local.top() + 6, p.window().height() - h - 2)
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(QColor(18, 20, 26, 220))
        p.drawRoundedRect(QRectF(x, y, w, h), 5, 5)
        p.setPen(QColor(235, 245, 255))
        p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, label)

    # ---------- 鼠标 / 键盘 ----------
    def mousePressEvent(self, event):
        if self._finished:
            return
        if event.button() == Qt.RightButton:
            self._cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        if self._dragging:
            return
        pos = _global_pos(event)
        self._origin = pos
        self._current = pos
        self._dragging = True
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging or self._finished:
            return
        self._current = _global_pos(event)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._finished:
            return
        if not self._dragging:
            # 没收到按下事件（极少见：按下被别的窗口抢走了）。
            # 这里不给任何隐含选区，宁可什么都不做，也不要截错地方。
            return
        self._current = _global_pos(event)
        self._dragging = False
        rect = self._active_rect()
        if rect is None or rect.width() < _MIN_SELECT or rect.height() < _MIN_SELECT:
            self._cancel()          # 误点/手抖，不产生选区
            return
        self._finish_with(rect)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(event)

    # ---------- 生命周期 ----------
    def _finish_with(self, rect: QRect):
        # 鼠标事件是 Qt 逻辑坐标；截图与 OCR 需要物理像素
        self._result = Rect(*to_physical(rect.x(), rect.y(),
                                         rect.width(), rect.height()))
        self._finish()

    def _cancel(self):
        self._result = None
        self._finish()

    def _finish(self):
        if self._finished:
            return
        self._finished = True
        self._dragging = False
        try:
            self.releaseMouse()
            self.releaseKeyboard()
        except Exception:
            pass
        self.hide()                     # 关键：截图前隐藏遮罩
        self._quit_loop()

    def _quit_loop(self):
        if self._loop is not None and self._loop.isRunning():
            self._loop.quit()

    @staticmethod
    def _settle(ms=_CAPTURE_DELAY_MS):
        """退循环后再等一小会儿：让 DWM 真正把遮罩从屏幕上撤掉，
        否则调用方截图会把半透明蒙层拍进去（画面发灰）。"""
        deadline = time.monotonic() + max(0, ms) / 1000.0
        while True:
            try:
                QApplication.processEvents()
            except Exception:
                pass
            if time.monotonic() >= deadline:
                return
            time.sleep(0.006)

    def select(self):
        """显示遮罩并阻塞等待用户拖出一个选区，返回 Rect 或 None。"""
        self._dragging = False
        self._origin = None
        self._current = None
        self._result = None
        self._finished = False     # 同一实例被复用时不至于卡在「已结束」状态

        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)
        self.update()

        self._loop = QEventLoop()
        if not self._finished:
            self._loop.exec()

        try:
            self.releaseMouse()
            self.releaseKeyboard()
        except Exception:
            pass
        self.hide()
        self._settle()
        return self._result
