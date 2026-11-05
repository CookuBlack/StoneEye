# -*- coding: utf-8 -*-
"""★ 截图贴图窗口：把框选区域定格成一张贴图，并在画面上逐句标注。

两种模式（右键可随时切换，配置 live.capture_mode 决定默认值）：
  - snapshot（默认）：框选瞬间抓一帧后永久冻结，切屏 / 切换窗口都不会改变
    贴图内容。不再启动采集线程，窗口可直接贴在源区域原位。
  - live：每 capture_interval_ms 重新抓同一物理区域，画面持续刷新。
    此时窗口必须错开源区域，并尝试 WDA_EXCLUDEFROMCAPTURE 防止无限镜像。

高亮由中心识别服务回派，与画面刷新解耦，OCR 慢也不会卡住贴图。
"""
import os
import sys
import time
from datetime import datetime

from PySide6.QtCore import (
    Qt, QRectF, QPoint, QLineF, QTimer, Signal, Slot, QStandardPaths,
)
from PySide6.QtGui import (
    QImage, QPainter, QPen, QColor, QPixmap, QFont, QFontMetrics,
)
from PySide6.QtWidgets import (
    QWidget, QMenu, QFileDialog, QApplication, QMessageBox, QToolTip,
)

from .coords import ui_scale
from .rule_engine import cached_qcolor, summarize

HINT_TEXT = "红=景别 蓝=运镜 · 按标点整句标注 · 拖动移动 · 滚轮缩放 · 双击关闭"

VALID_MODES = ("snapshot", "live")
VALID_CORNERS = ("bottom_right", "bottom_left", "top_right", "top_left")


def normalize_mode(value) -> str:
    """配置值 / 菜单值统一收敛成 snapshot | live。"""
    mode = str(value or "snapshot").strip().lower()
    return "live" if mode in ("live", "realtime", "real_time", "实时") else "snapshot"

MENU_QSS = """
QMenu { background:#202329; color:#ECEFF4; border:1px solid #3A404B; padding:4px; }
QMenu::item { padding:6px 22px; border-radius:4px; }
QMenu::item:selected { background:#3A6FF0; color:#FFFFFF; }
QMenu::separator { height:1px; background:#3A404B; margin:4px 8px; }
"""

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11


def _local_pos(event):
    try:
        return event.position().toPoint()
    except AttributeError:
        return event.pos()


def _global_pos(event):
    try:
        return event.globalPosition().toPoint()
    except AttributeError:
        return event.globalPos()


class LiveWindow(QWidget):
    closed = Signal(int)

    def __init__(self, window_id, initial_frame, cfg, screen_pos,
                 source_rect, service, parent=None):
        super().__init__(parent)
        self.window_id = int(window_id)
        self.source_rect = source_rect
        self.cfg = cfg
        self.sticker_cfg = cfg.get("sticker", {})
        self.live_cfg = cfg.get("live", {})
        self._service = service

        self._last_frame = initial_frame
        self.frozen_frame = initial_frame   # 快照模式的原始帧，永不随屏幕变化
        self.pixmap = self._pixmap_from_frame(initial_frame)
        self.highlights = []
        self.ocr_text = ""
        self.line_count = 0            # 本帧 OCR 识别到的行数，用于判断是否漏检
        self.frame_id = int(initial_frame["frame_id"])
        self.ocr_frame_id = -1
        self.paused = False
        self._status_cache = None      # 图例/命中数缓存，避免每次重绘都重算

        # 识别进度：queued(排队) -> running(分块识别) -> done(完成，短暂停留)
        self._progress = {
            "stage": "idle", "done": 0, "total": 0,
            "started": 0.0, "elapsed": 0.0,
        }
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(60)
        self._progress_timer.timeout.connect(self._tick_progress)
        self._progress_hold = QTimer(self)
        self._progress_hold.setSingleShot(True)
        self._progress_hold.timeout.connect(self._clear_progress)

        # snapshot = 冻结首帧（默认）；live = 持续重新抓屏
        self.mode = normalize_mode(self.live_cfg.get("capture_mode"))
        self._capture_worker = None
        # 由 WindowManager 注入：重截 / 框选前统一隐藏所有贴图，避免把自己截进去
        self.before_capture = None
        self.after_capture = None

        self.zoom = float(self.sticker_cfg.get("default_zoom", 1.0))
        self.min_zoom = float(self.sticker_cfg.get("min_zoom", 0.2))
        self.max_zoom = float(self.sticker_cfg.get("max_zoom", 5.0))
        self.zoom_step = float(self.sticker_cfg.get("zoom_step", 0.1))
        self.opacity = float(self.sticker_cfg.get("default_opacity", 1.0))

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(self.opacity)
        self.setCursor(Qt.OpenHandCursor)
        self.setWindowTitle("OCR 截图贴图")

        self._drag_active = False
        self._drag_start_global = QPoint()
        self._drag_start_pos = QPoint()
        self._closed_emitted = False

        self._hint_alpha = 1.0 if self.sticker_cfg.get("show_hint_on_create", True) else 0.0
        self._hint_elapsed = 0
        self._hint_timer = None
        if self._hint_alpha > 0:
            self._hint_timer = QTimer(self)
            self._hint_timer.setInterval(40)
            self._hint_timer.timeout.connect(self._tick_hint)
            self._hint_timer.start()

        # 出现动效：淡入 + 描边高亮闪一下。
        # 快照模式是原位粘贴，画面和背景长得一模一样，没有这个动效
        # 用户根本分不清「到底截下来了没有」。
        self._flash_on_create = bool(self.sticker_cfg.get("flash_on_create", True))
        self._appear_t = 99.0
        self._appear_timer = None
        if self._flash_on_create:
            self._appear_t = 0.0
            self.setWindowOpacity(0.0)
            self._appear_timer = QTimer(self)
            self._appear_timer.setInterval(30)
            self._appear_timer.timeout.connect(self._tick_appear)
            self._appear_timer.start()

        # 首帧识别为空时自动重试：覆盖「模型还在预热」「det 偶发漏检」
        # 「画面静止导致帧差不再投帧」三种情况，避免标注永远停在 0 命中。
        self._empty_retries = 0
        self._empty_retry_limit = int(self.live_cfg.get("empty_retry_limit", 5))
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(
            max(200, int(self.live_cfg.get("empty_retry_interval_ms", 1200)))
        )
        self._retry_timer.timeout.connect(self._tick_empty_retry)
        self._retry_timer.start()

        self.move(int(screen_pos[0]), int(screen_pos[1]))
        self._apply_size()

    # ---------- 帧更新 ----------
    @staticmethod
    def _pixmap_from_frame(frame) -> QPixmap:
        qimg = QImage(
            frame["rgb"], frame["width"], frame["height"],
            frame["width"] * 3, QImage.Format_RGB888,
        )
        # QPixmap.fromImage 会深拷贝像素，不再需要 qimg.copy() 再拷一次
        pixmap = QPixmap.fromImage(qimg)
        pixmap.setDevicePixelRatio(1.0)
        return pixmap

    @Slot(object)
    def update_frame(self, frame, allow_frozen=False):
        """换画面（live 采集帧 / 手动重截）；高亮沿用最近一次识别结果。

        快照模式下丢弃采集线程可能残留的排队帧，避免切回定格后画面又跳变；
        只有「重新截取源区域」显式传入 allow_frozen=True 才允许替换。
        """
        if self.mode != "live" and not allow_frozen:
            return
        if not frame or frame.get("window_id") != self.window_id:
            return
        self._last_frame = frame
        self.frame_id = int(frame["frame_id"])
        size_changed = (
            frame["width"] != self.pixmap.width()
            or frame["height"] != self.pixmap.height()
        )
        self.pixmap = self._pixmap_from_frame(frame)
        if size_changed:
            self._apply_size()
        self.update()

    @staticmethod
    def _highlight_signature(highlights):
        """标注签名：用于「识别结果没变就别重绘」的判定。"""
        out = []
        for item in highlights or []:
            box = item.get("box") or (0, 0, 0, 0)
            rule = item.get("rule") or {}
            out.append((
                round(float(box[0]), 1), round(float(box[1]), 1),
                round(float(box[2]), 1), round(float(box[3]), 1),
                str(item.get("text", "")),
                str(rule.get("color", "")),
            ))
        return tuple(out)

    def apply_ocr(self, highlights, text, frame_id, line_count=0):
        """识别服务回派：只更新标注，不碰画面。

        live 模式下画面轻微变化（光标闪烁、进度条）会触发重识别，
        但识别结果往往完全一样；签名相同就不必整幅重绘。
        """
        new_items = list(highlights or [])
        new_text = text or ""
        new_lines = int(line_count or 0)
        same = (new_text == self.ocr_text
                and new_lines == self.line_count
                and self._highlight_signature(new_items)
                == self._highlight_signature(self.highlights))

        self.highlights = new_items
        self.ocr_text = new_text
        self.line_count = new_lines
        self.ocr_frame_id = int(frame_id)
        self._status_cache = None
        if self.line_count > 0:
            self._empty_retries = 0
            self._retry_timer.stop()      # 已成功识别到文本，停止空结果重试
        if not same:
            self.update()

    # ---------- 识别进度 ----------
    def apply_progress(self, done=0, total=0, stage="running"):
        """queued：已入队等待；running：正在逐块识别；done/failed：结束。"""
        stage = str(stage or "running").lower()
        done, total = max(0, int(done or 0)), max(0, int(total or 0))
        now = time.monotonic()
        p = self._progress

        if stage == "queued":
            p.update(stage="queued", done=0, total=total,
                     started=now, elapsed=0.0)
        elif stage == "running":
            if p["stage"] in ("idle", "done", "failed") or not p["started"]:
                p["started"] = now       # 从排队转正式识别时不清零总耗时
            p.update(stage="running", done=done, total=total,
                     elapsed=now - p["started"])
        else:                            # done / failed
            if not p["started"]:
                p["started"] = now
            p["stage"] = stage if stage in ("done", "failed") else "done"
            p["elapsed"] = now - p["started"]
            if total:
                p["done"] = p["total"] = total
            self._progress_hold.start(
                max(0, int(self.live_cfg.get("progress_hold_ms", 900)))
            )

        if p["stage"] in ("queued", "running"):
            self._progress_hold.stop()
            if not self._progress_timer.isActive():
                self._progress_timer.start()
        else:
            self._progress_timer.stop()
        self._update_overlay()

    def _update_overlay(self):
        """只重绘顶部进度条 / 状态条所在的窄带。

        进度条 60ms 一跳，若每次都 update() 整个窗口，就要把整幅
        QPixmap 重新光栅化一遍；实际动的只有顶部 3px 和状态条，
        限定脏区后重绘成本降到千分之一量级。
        """
        w, h = max(1, self.width()), max(1, self.height())
        band = 34
        self.update(0, 0, w, band)
        corner = str(self.live_cfg.get("status_corner", "bottom_right")).lower()
        if not corner.startswith("top_"):
            self.update(0, max(0, h - band), w, band)

    def _tick_progress(self):
        p = self._progress
        if p["stage"] in ("queued", "running"):
            p["elapsed"] = time.monotonic() - p["started"]
            self._update_overlay()

    def _clear_progress(self):
        self._progress.update(stage="idle", done=0, total=0,
                              started=0.0, elapsed=0.0)
        self._update_overlay()

    def _progress_label(self) -> str:
        p = self._progress
        stage = p["stage"]
        if stage == "queued":
            return "排队中…"
        if stage == "running":
            if p["total"] > 0:
                pct = int(round(100 * min(1.0, p["done"] / p["total"])))
                return f"识别中 {p['done']}/{p['total']} · {pct}%"
            return "识别中…"
        if stage == "done":
            return f"已识别 {p['elapsed']:.1f}s"
        if stage == "failed":
            return "识别失败"
        return ""

    def _tick_empty_retry(self):
        if self._empty_retries >= self._empty_retry_limit:
            self._retry_timer.stop()
            return
        if self.ocr_frame_id >= 0 and self.line_count > 0:
            self._retry_timer.stop()
            return
        self._empty_retries += 1
        self.request_recognize(quiet=True)

    def _set_display_affinity(self, value) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
                int(self.winId()), int(value)))
        except Exception:
            return False

    def try_exclude_from_capture(self) -> bool:
        """live 模式：让本窗口不出现在屏幕截图里，避免无限镜像。"""
        return self._set_display_affinity(WDA_EXCLUDEFROMCAPTURE)

    def restore_capture_visibility(self) -> bool:
        """切回快照时恢复可被截图，否则系统截图工具会拍不到这张贴图。"""
        return self._set_display_affinity(WDA_NONE)

    def _overlaps_source(self) -> bool:
        s = ui_scale()
        x, y = self.source_rect.x / s, self.source_rect.y / s
        w, h = self.source_rect.w / s, self.source_rect.h / s
        return (self.x() < x + w and self.x() + self.width() > x
                and self.y() < y + h and self.y() + self.height() > y)

    def _move_away_from_source(self):
        try:
            from .window_manager import pick_position
            pos = pick_position(
                self.source_rect, self.width(), self.height(),
                int(self.sticker_cfg.get("source_offset", 24)),
            )
            self.move(int(pos[0]), int(pos[1]))
        except Exception:
            pass

    # ---------- 模式切换 ----------
    def set_mode(self, mode, quiet=False, force=False):
        """snapshot <-> live 热切换；切换本身不会改变当前显示的画面。"""
        mode = normalize_mode(mode)
        if mode == self.mode and not force:
            return
        self.mode = mode
        self.live_cfg = dict(self.live_cfg)
        self.live_cfg["capture_mode"] = mode

        if mode == "snapshot":
            if self._capture_worker is not None:
                try:
                    self._capture_worker.frame_ready.disconnect(self.update_frame)
                except (RuntimeError, TypeError):
                    pass
                self._service.stop_capture(self.window_id)
                self._capture_worker = None
                self.restore_capture_visibility()
            self.paused = False
            # 定格「切换前一刻看到的画面」；默认从 snapshot 启动时就是首帧
            self.frozen_frame = self._last_frame
            label = "已定格为快照 · 切屏不再改变画面"
        else:
            # 快照模式是原位摆放的，切到实时前必须挪开，否则第一帧就拍到自己
            if self._overlaps_source():
                self._move_away_from_source()
            worker = self._service.start_capture(self.window_id, self.source_rect)
            worker.frame_ready.connect(self.update_frame, Qt.QueuedConnection)
            self._capture_worker = worker
            self._service.pause_window(self.window_id, self.paused)
            self.try_exclude_from_capture()
            label = "已切换为实时刷新 · 窗口已移开源区域"

        self.update()
        if not quiet:
            self._toast(label)

    def recapture(self):
        """快照模式下重新截取源区域，替换这一张贴图的内容。

        抓屏前隐藏所有贴图窗口（由 WindowManager 注入回调），
        否则原位摆放的贴图会把自己拍进去。
        """
        # 连续点击时只保留最后一次，避免叠加多次抓屏
        old = getattr(self, "_recapture_timer", None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        if self.before_capture is not None:
            try:
                self.before_capture()
            except Exception:
                pass
        self._recapture_timer = QTimer(self)
        self._recapture_timer.setSingleShot(True)
        self._recapture_timer.timeout.connect(self._do_recapture)
        self._recapture_timer.start(120)

    def _do_recapture(self):
        try:
            from .screen_capture import capture
            from .capture_worker import make_frame
            frame = make_frame(
                self.window_id, self.frame_id + 1, capture(self.source_rect)
            )
            self.frozen_frame = frame
            self.update_frame(frame, True)
            self._service.enqueue(frame)
            msg = "已重新截取源区域"
        except Exception as exc:
            msg = "重新截取失败：" + str(exc)
        finally:
            if self.after_capture is not None:
                try:
                    self.after_capture()
                except Exception:
                    pass
            self.update()
        self._toast(msg)

    # ---------- 尺寸与绘制 ----------
    def _draw_scale(self) -> float:
        return self.zoom / ui_scale()

    def _apply_size(self):
        k = self._draw_scale()
        self.resize(
            max(1, round(self.pixmap.width() * k)),
            max(1, round(self.pixmap.height() * k)),
        )

    def _paint_content(self, painter: QPainter, scale: float,
                       pen_divisor: float = 1.0):
        """画面 + 逐句高亮；scale=1 时用于导出原始分辨率成品图。"""
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(scale, scale)
        painter.drawPixmap(0, 0, self.pixmap)

        for item in self.highlights:
            rule = item["rule"]
            x1, y1, x2, y2 = item["box"]
            rect = QRectF(x1, y1, x2 - x1, y2 - y1)
            is_clause = item.get("scope") == "clause"
            if rule.get("fill"):
                fill = cached_qcolor(
                    rule.get("fill_color"), rule.get("color", "#FF453A")
                )
                if is_clause:
                    # 整句框面积大，填充压到 30%，让正文保持可读
                    try:
                        fill.setAlpha(int(fill.alpha() * 0.3))
                    except Exception:
                        pass
                painter.fillRect(rect, fill)
            line = float(rule.get("line_width", 3))
            # 整句框只是「范围提示」，描边收细；关键词再用实线下划线强调
            width = max(0.3, line * (0.55 if is_clause else 1.0)
                        / max(pen_divisor, 0.01))
            pen = QPen(cached_qcolor(rule.get("color")), width)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

            if is_clause:
                word_pen = QPen(cached_qcolor(rule.get("color")),
                                max(0.8, line * 0.85 / max(pen_divisor, 0.01)))
                word_pen.setCapStyle(Qt.RoundCap)
                painter.setPen(word_pen)
                for wx1, _wy1, wx2, wy2 in item.get("word_boxes") or []:
                    base = wy2 - max(1.0, line * 0.35)
                    painter.drawLine(QLineF(wx1, base, wx2, base))
        painter.restore()

    def paintEvent(self, _event):
        painter = QPainter(self)
        self._paint_content(painter, self._draw_scale(), self.zoom)
        self._paint_border(painter)
        if self._appear_t < 1.4:
            self._paint_capture_badge(painter)
        if self._hint_alpha > 0:
            self._paint_hint(painter)
        if self.live_cfg.get("show_status", True):
            self._paint_status(painter)
        self._paint_progress(painter)

    def _tick_appear(self):
        """出现动效：前 0.24s 淡入，之后让描边光晕衰减到位。"""
        self._appear_t += 0.03
        t = self._appear_t
        if t < 0.24:
            self.setWindowOpacity(max(0.0, self.opacity * (0.3 + 0.7 * (t / 0.24))))
        elif self.windowOpacity() != self.opacity:
            self.setWindowOpacity(self.opacity)
        self.update()
        if t >= 1.4:
            self._appear_timer.stop()
            self.update()

    def _paint_border(self, painter: QPainter):
        """贴图外框：原位粘贴时靠它区分「这是贴图」还是「原屏幕」。"""
        if not self.sticker_cfg.get("show_border", True):
            return
        w, h = max(1, self.width()), max(1, self.height())
        if w < 4 or h < 4:
            return
        color = cached_qcolor(self.sticker_cfg.get("border_color"), "#00E5FF")
        glow = 0.0
        if self._flash_on_create and self._appear_t < 1.2:
            glow = max(0.0, 1.0 - self._appear_t / 1.2)
        width = 1.0 + 1.5 * glow
        painter.save()
        painter.setBrush(Qt.NoBrush)
        if glow > 0.02:
            for i in range(3):
                ring = QColor(color)
                ring.setAlpha(int(90 * glow / (i + 1)))
                painter.setPen(QPen(ring, 1.0))
                inset = 1.0 + i * 2.0
                painter.drawRect(QRectF(inset, inset, w - 2 * inset, h - 2 * inset))
        edge = QColor(color)
        edge.setAlpha(int(190 + 65 * glow))
        painter.setPen(QPen(edge, max(1.0, width)))
        painter.drawRect(QRectF(width / 2.0, width / 2.0,
                                w - width, h - width))
        painter.restore()

    def _paint_capture_badge(self, painter: QPainter):
        """出现时在右下角报一句「已截取 宽 x 高」，确认尺寸没选错。"""
        alpha = 1.0 if self._appear_t < 0.9 else max(0.0, (1.4 - self._appear_t) / 0.5)
        if alpha <= 0.02:
            return
        text = f"已截取 {self.pixmap.width()} x {self.pixmap.height()}"
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.save()
        painter.setFont(font)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text) + 18
        h = fm.height() + 10
        x, y = self._badge_origin(w, h)
        painter.setOpacity(alpha)
        painter.setPen(QPen(Qt.NoPen))
        painter.setBrush(QColor(14, 18, 24, 214))
        painter.drawRoundedRect(QRectF(x, y, w, h), 5, 5)
        painter.setPen(cached_qcolor(self.sticker_cfg.get("border_color"), "#00E5FF"))
        painter.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)
        painter.restore()

    def _badge_origin(self, w, h):
        """尺寸角标固定贴右下角；状态条也占右下角时往上让一格，别叠在一起。"""
        x = max(0.0, self.width() - w - 8.0)
        y = max(0.0, self.height() - h - 8.0)
        corner = str(self.live_cfg.get("status_corner", "bottom_right")).lower()
        if self.live_cfg.get("show_status", True) and corner == "bottom_right":
            f = QFont()
            f.setPointSize(8)
            f.setBold(True)
            y = max(0.0, y - (QFontMetrics(f).height() + 8 + 4))
        return x, y

    def _paint_progress(self, painter: QPainter):
        """顶部 3px 进度条：排队=来回滑动，识别中=按分块填充，完成=满格绿色。"""
        if not self.live_cfg.get("show_progress", True):
            return
        p = self._progress
        stage = p["stage"]
        if stage == "idle":
            return

        w = max(1, self.width())
        bar_h = 3.0
        running = stage in ("queued", "running")
        if running:
            color = QColor(10, 132, 255)
        elif stage == "done":
            color = QColor(52, 199, 89)
        else:
            color = QColor(255, 69, 58)

        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(14, 16, 20, 170))
        painter.drawRect(QRectF(0, 0, w, bar_h))

        painter.setBrush(color)
        if p["total"] > 0:
            ratio = 1.0 if stage == "done" else max(0.0, min(1.0, p["done"] / p["total"]))
            painter.drawRect(QRectF(0, 0, max(2.0, w * ratio), bar_h))
        else:
            # 不确定进度：一段光条来回扫，表示「在排队/正在跑」
            seg = max(36.0, w * 0.25)
            t = (time.monotonic() * 0.8) % 2.0
            phase = t if t <= 1.0 else 2.0 - t
            painter.drawRect(QRectF((w - seg) * phase, 0, seg, bar_h))
        painter.restore()

    def _paint_hint(self, painter: QPainter):
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, self._hint_alpha)))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        fm = QFontMetrics(font)
        available = max(40, self.width() - 12)
        text = fm.elidedText(HINT_TEXT, Qt.ElideRight, available - 16)
        w = min(fm.horizontalAdvance(text) + 20, self.width())
        h = fm.height() + 10
        x = max(0, (self.width() - w) // 2)
        painter.setPen(QPen(Qt.NoPen))
        painter.setBrush(QColor(20, 22, 28, 215))
        painter.drawRoundedRect(QRectF(x, 6, w, h), 6, 6)
        painter.setPen(QColor(240, 242, 248))
        painter.drawText(QRectF(x, 6, w, h), Qt.AlignCenter, text)
        painter.restore()

    def _status_data(self):
        """(show_legend, categories, counts, colors)；结果缓存到下次识别回派。"""
        if self._status_cache is None:
            show_legend = bool(self.live_cfg.get("show_legend", True))
            counts, colors = summarize(self.highlights, self.cfg.get("rules", []))
            categories = []
            for rule in self.cfg.get("rules", []):
                c = str(rule.get("category") or rule.get("name") or "其他")
                if c not in categories:
                    categories.append(c)
            for c in counts:
                if c not in categories:
                    categories.append(c)
            self._status_cache = (show_legend, categories, counts, colors)
        return self._status_cache

    def _paint_status(self, painter: QPainter):
        """状态条：分类配色图例 + 各类命中数 + OCR 行数 + 当前模式。

        默认贴在右下角（live.status_corner 可改成其它三角），
        避免遮挡正文开头的文字；窗口太窄时自动退化成精简文案，保证不越界。
        """
        show_legend, categories, counts, colors = self._status_data()

        head = ("Ⅱ 已暂停" if self.paused else "● LIVE") if self.mode == "live" else "■ 快照"
        prog = self._progress_label()
        parts = [(head, None)]
        if show_legend:
            for c in categories:
                parts.append((f"{c} {counts.get(c, 0)}", colors.get(c, "#FFFFFF")))
        else:
            parts.append((f"{len(self.highlights)} 处命中", None))
        parts.append((f"OCR {self.line_count} 行", None))
        if prog:
            parts.append((prog, None))

        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)
        gap, swatch = 12, 9

        def measure(items):
            total = 12
            for text, color in items:
                total += (fm.horizontalAdvance(text)
                          + (swatch + 5 if color else 0) + gap)
            return total + 4

        w = measure(parts)
        if w > max(40, self.width() - 12):
            parts = [(head, None), (f"{len(self.highlights)} 处命中", None)]
            if prog:
                parts.append((prog, None))
            w = measure(parts)
        w = min(w, max(40, self.width()))
        h = fm.height() + 8

        corner = str(self.live_cfg.get("status_corner", "bottom_right")).lower()
        if corner not in VALID_CORNERS:
            corner = "bottom_right"
        x = (max(0, self.width() - w - 6) if corner.endswith("_right") else 6)
        y = (6 if corner.startswith("top_")
             else max(0, self.height() - h - 6))

        painter.setPen(QPen(Qt.NoPen))
        painter.setBrush(QColor(20, 22, 28, 200))
        painter.drawRoundedRect(QRectF(x, y, w, h), 5, 5)

        cursor = x + 6
        for i, (text, color) in enumerate(parts):
            if color:
                painter.setBrush(cached_qcolor(color))
                painter.drawRoundedRect(
                    QRectF(cursor, y + (h - swatch) / 2, swatch, swatch), 2, 2
                )
                cursor += swatch + 5
            if i == 0:
                if self.mode != "live":
                    pen_color = QColor(150, 200, 255)
                elif self.paused:
                    pen_color = QColor(255, 214, 10)
                else:
                    pen_color = QColor(126, 231, 135)
            elif color:
                pen_color = QColor(236, 239, 244)
            else:
                pen_color = QColor(160, 168, 182)
            text_w = fm.horizontalAdvance(text)
            painter.setPen(pen_color)
            painter.drawText(
                QRectF(cursor, y, text_w + 4, h),
                Qt.AlignLeft | Qt.AlignVCenter, text,
            )
            cursor += text_w + gap

    def _tick_hint(self):
        self._hint_elapsed += 40
        if self._hint_elapsed >= 1500:
            self._hint_alpha -= 0.075
            if self._hint_alpha <= 0:
                self._hint_alpha = 0.0
                self._hint_timer.stop()
        self.update(0, 0, max(1, self.width()), 44)

    def build_image(self) -> QPixmap:
        """当前帧 + 当前标注，原始分辨率（不受缩放影响）。"""
        image = QPixmap(self.pixmap.size())
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        self._paint_content(painter, 1.0, 1.0)
        painter.end()
        return image

    # ---------- 交互 ----------
    def wheelEvent(self, event):
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            return
        if event.modifiers() & Qt.ControlModifier:
            self.opacity = max(
                0.1, min(1.0, self.opacity + (0.05 if delta_y > 0 else -0.05))
            )
            self.setWindowOpacity(self.opacity)
            event.accept()
            return

        old = self.zoom
        new = old + (self.zoom_step if delta_y > 0 else -self.zoom_step)
        new = round(max(self.min_zoom, min(self.max_zoom, new)), 4)
        if abs(new - old) < 1e-9:
            event.accept()
            return
        mouse = _local_pos(event)
        global_mouse = self.mapToGlobal(mouse)
        ratio = new / old
        new_x = global_mouse.x() - (global_mouse.x() - self.x()) * ratio
        new_y = global_mouse.y() - (global_mouse.y() - self.y()) * ratio
        self.zoom = new
        self._apply_size()
        self.move(int(round(new_x)), int(round(new_y)))
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_start_global = _global_pos(event)
            self._drag_start_pos = self.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_active and (event.buttons() & Qt.LeftButton):
            delta = _global_pos(event) - self._drag_start_global
            self.move(self._drag_start_pos + delta)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = False
            self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.close()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_QSS)
        if self.mode == "snapshot":
            menu.addAction("重新截取源区域", lambda: self.recapture())
            menu.addAction("切换为实时刷新", lambda: self.set_mode("live"))
        else:
            menu.addAction("切换为快照（冻结画面）",
                           lambda: self.set_mode("snapshot"))
            menu.addAction("继续刷新" if self.paused else "暂停刷新",
                           lambda: self.toggle_pause())
        menu.addSeparator()
        menu.addAction("立即重新识别", lambda: self.request_recognize())
        menu.addSeparator()
        menu.addAction("复制当前画面（含标注）", lambda: self.copy_image())
        menu.addAction("复制识别文本", lambda: self.copy_text())
        menu.addSeparator()
        menu.addAction("保存为 PNG", lambda: self.save_png())
        menu.addSeparator()
        menu.addAction("关闭", lambda: self.close())
        pos = self.mapToGlobal(_local_pos(event))
        try:
            menu.exec(pos)
        except AttributeError:
            menu.exec_(pos)

    def closeEvent(self, event):
        if self._hint_timer is not None:
            self._hint_timer.stop()
        if self._appear_timer is not None:
            self._appear_timer.stop()
        if self._retry_timer is not None:
            self._retry_timer.stop()
        self._progress_timer.stop()
        self._progress_hold.stop()
        if getattr(self, "_recapture_timer", None) is not None:
            self._recapture_timer.stop()
        if self._capture_worker is not None:
            self._service.stop_capture(self.window_id)
            self._capture_worker = None
        if not self._closed_emitted:
            self._closed_emitted = True
            self.closed.emit(self.window_id)
        super().closeEvent(event)

    # ---------- 菜单动作 ----------
    def _toast(self, text: str):
        QToolTip.showText(
            self.mapToGlobal(QPoint(self.width() // 2, 24)), text, self
        )

    def toggle_pause(self):
        if self.mode != "live":
            self._toast("快照模式已经是定格画面，无需暂停")
            return
        self.paused = not self.paused
        self._service.pause_window(self.window_id, self.paused)
        self._toast("已暂停刷新" if self.paused else "已继续刷新")
        self.update()

    def apply_config(self, cfg):
        """配置热加载：让已存在的窗口也用上新词表配色 / 状态条位置。"""
        self.cfg = cfg
        self.live_cfg = dict(cfg.get("live", {}))
        self.live_cfg["capture_mode"] = self.mode   # 实际模式由 set_mode 单独切换
        self.sticker_cfg = cfg.get("sticker", {})
        self._status_cache = None                   # 词表/图例开关可能已变
        self._empty_retry_limit = int(self.live_cfg.get("empty_retry_limit", 5))
        self._empty_retries = 0
        self._retry_timer.setInterval(
            max(200, int(self.live_cfg.get("empty_retry_interval_ms", 1200)))
        )
        # 已识别到文本就不必再对同一张定格画面反复重试 OCR
        if self.line_count <= 0 and self._empty_retry_limit > 0:
            self._retry_timer.start()
        else:
            self._retry_timer.stop()
        self.update()

    def request_recognize(self, quiet: bool = False):
        if self._last_frame is not None:
            frame = dict(self._last_frame)
            frame["window_id"] = self.window_id
            self._service.enqueue(frame)
            if not quiet:
                self._toast("已提交重新识别")

    def copy_image(self):
        QApplication.clipboard().setPixmap(self.build_image())
        self._toast("已复制当前画面（含标注）")

    def copy_text(self):
        if not self.ocr_text.strip():
            self._toast("当前没有识别到文本")
            return
        QApplication.clipboard().setText(self.ocr_text)
        self._toast("已复制识别文本（每句一行）")

    def save_png(self):
        base = (QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
                or os.path.expanduser("~"))
        name = ("snap_" if self.mode != "live" else "live_") \
            + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存当前帧", os.path.join(base, name), "PNG 图片 (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if self.build_image().save(path, "PNG"):
            self._toast("已保存：" + os.path.basename(path))
        else:
            QMessageBox.warning(self, "保存失败", "无法写入文件：\n" + path)
