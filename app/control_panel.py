# -*- coding: utf-8 -*-
"""控制面板（V4.5）：左侧栏导航 + 右侧分页，布局参考 WorkBuddy 桌面端。

为什么从「一列竖着排」改成「两栏分页」：

  1. 设置项已经到 13 个，一列排下来必然要滚动，而滚动条出现在设置面板里
     本身就是失败 —— 用户找不到东西。分区之后每页只有 1~3 张卡，一屏到底。
  2. 侧栏把「操作」和「设置」分开：上面是动作（新建框选 / 重新识别），
     下面是去哪（概览 / 显示 / 采集与识别 / 识别规则 / 高级参数）。
  3. 视觉上一整套中性灰，唯一的高亮是白色。贴图上的分类色（运镜蓝、
     景别橙）是画面里唯一有颜色的东西，面板不跟它抢。

V4.5 只做一件事：让它「浮」起来。整窗透明 + 自绘圆角底板 + 双层投影，
四角是真的透出了桌面，不再是深色方块上画了个圆角图形；顶边一道自上而下
渐隐的高光，配上下垂一点的影子，窗口才像被托在桌面上而不是糊在墙上。

数据契约不变：仍然只走 controller.apply_panel_patch 改配置，
仍然用 sync_from_config 回填，托盘与面板不会互相打架。
"""
from datetime import datetime

from PySide6.QtCore import (
    QEasingCurve, QPropertyAnimation, QRect, QRectF, Qt, QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QKeySequence, QLinearGradient,
    QPainter, QPainterPath, QPen, QShortcut,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from .assets import app_icon, app_pixmap
from .config_manager import ConfigManager
from .ui_theme import (
    BG_BOTTOM, BG_ROOT, BG_SIDE, BG_TOP, PANEL_QSS, R_SM, R_WIN, SEL_BG, TEXT,
    TEXT_2, TEXT_3, scaled_qss,
)
from .ui_shell import SHADOW_PAD, ShellWindow
from .ui_widgets import (
    AnimatedButton, CornerPicker, Metric, NavItem, SegmentedControl,
    SilentGrip, SwitchRow, card, caption, chip_label, hline, px,
)

MODE_OPTIONS = (("snapshot", "快照定格"), ("live", "实时刷新"))
GRAIN_OPTIONS = (("clause", "整句标注"), ("word", "词级标注"))

# 「高级参数」里的滑块：key -> (标题, 下限, 上限, 后缀, 显示倍率, 小数位, 提示)
ADVANCED_SLIDERS = (
    ("live", "capture_interval_ms", "采集间隔", 33, 1000, " ms", 1.0, 0,
     "实时模式下多久抓一次屏。快照模式只在框选那一刻抓一次，此值影响很小"),
    ("live", "ocr_min_interval_ms", "识别间隔", 0, 2000, " ms", 1.0, 0,
     "两次 OCR 之间的最小间隔，调大可降低 CPU 占用"),
    ("live", "frame_diff_threshold", "帧差阈值", 0, 200, "", 10.0, 1,
     "画面变化超过这个程度才重新识别；调大更省 CPU，调小更灵敏"),
    ("sticker", "default_zoom", "默认缩放", 20, 300, "%", 100.0, 0,
     "新建贴图的初始缩放比例"),
    ("sticker", "default_opacity", "默认透明", 20, 100, "%", 100.0, 0,
     "新建贴图的初始不透明度，调低可以看清下面的原文"),
    ("ocr", "tile_max", "单帧块数", 0, 24, " 块", 1.0, 0,
     "单帧最多切成几块送识别，0 表示不限制；区域很大时调小可缩短耗时"),
)

STATUS_COLORS = {
    "ready": "#5FBF7A",
    "failed": "#E06C64",
    "loading": "#D2B24C",
}

# 侧栏设置项：(page key, 图标, 标题)
NAV_PAGES = (
    ("home", "grid", "概览"),
    ("display", "display", "显示"),
    ("capture", "tune", "采集与识别"),
    ("advanced", "sparkle", "高级参数"),
)
NAV_RULES = ("rules", "list", "识别规则")

# 下面都是「逻辑像素」，真实尺寸 = 数值 * ui_zoom()，125% 屏上再乘 1.25。
SIDEBAR_W = 214
DEFAULT_W, DEFAULT_H = 1120, 740    # 首次打开的大小（实际受屏幕可用区域约束）
MIN_W, MIN_H = 900, 660             # 再小就挤了，卡片会互相顶

# 窗口四周留出来投影的空白：整窗透明 + 自绘底板，边框之外那圈就是影子。
# 具体数值在 ui_shell.SHADOW_PAD，这里只留别名给几何计算和历史调用。
SHADOW_PAD_BTM = SHADOW_PAD


def _fmt(value: float, decimals: int, suffix: str) -> str:
    if decimals <= 0:
        return f"{int(round(value))}{suffix}"
    return f"{value:.{decimals}f}{suffix}"


class _DragArea(QWidget):
    """空白区域兼作拖动条：无边框窗口必需，按住哪里都能挪窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press = None
        self._origin = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._origin = self.window().pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is None or not (event.buttons() & Qt.LeftButton):
            return super().mouseMoveEvent(event)
        delta = event.globalPosition().toPoint() - self._press
        if delta.manhattanLength() >= 2:
            self.window().move(self._origin + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)


class _Sidebar(QWidget):
    """侧栏底板：画左半边的圆角，并负责那条会滑动的选中胶囊。

    选中底色不再由每个 NavItem 自己填 —— 那样切页是"这边灭、那边亮"，
    生硬。改成侧栏统一维护一块高亮，切页时从上一行补间滑到下一行。
    """

    def __init__(self, scale: float, parent=None):
        super().__init__(parent)
        self._scale = scale
        self.setFixedWidth(px(SIDEBAR_W, scale))

        self._hl = QRectF()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    # -- 高亮块 --
    def set_highlight(self, rect, animate: bool = True):
        target = QRectF(rect)
        if target.isNull() or target.height() <= 0:
            return
        smooth = (animate and not self._hl.isNull() and self._hl.height() > 0
                  and self.isVisible())
        if not smooth:
            self._anim.stop()
            self._hl = target
            self.update()
            return
        if abs(self._hl.y() - target.y()) < 0.5:
            self._hl = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._hl)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim(self, value):
        if isinstance(value, QRectF):
            self._hl = QRectF(value)
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = px(R_WIN, self._scale)
        # 向右多画一个圆角半径，右半边的圆角就落到控件外面去了
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, self.width() + r, self.height()), r, r)
        p.fillPath(path, QColor(BG_SIDE))

        if not self._hl.isNull() and self._hl.height() > 0:
            hr = px(R_SM, self._scale)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(SEL_BG))
            p.drawRoundedRect(self._hl, hr, hr)
        p.end()


class ControlPanel(ShellWindow):
    """常驻小窗；关闭即隐藏，配置改动即时热应用。"""

    def __init__(self, controller, parent=None):
        # 这里千万不能用 Qt.Tool：
        #   Tool 窗口在 Windows 上既不进任务栏，也不进 Alt+Tab，
        #   用户就会问「为什么切不过来、为什么底下没有图标」。
        # 用普通顶层窗口 + 去掉系统边框，任务栏按钮、Alt+Tab、点击还原全都有。
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self.controller = controller
        self._syncing = False
        self._reocr_timer = None
        self._saved_timer = None
        self._geometry_ready = False
        self._sliders = {}          # (section, key) -> (slider, label, scale, suffix, decimals)
        self._switches = {}         # (section, key) -> SwitchRow
        self._navs = {}             # page key -> NavItem
        self._pages = {}            # page key -> QScrollArea
        self._current = "home"

        self.setObjectName("root")
        self.setStyleSheet(scaled_qss(PANEL_QSS, self._scale))
        self.setWindowTitle("控制面板 · 屏幕识别贴图")
        self.setWindowIcon(app_icon())
        # 圆角底板、投影、描边覆盖层都在 ui_shell.ShellWindow 里，这里只管尺寸
        self._spad_b = self._spad
        self.setMinimumSize(self._px(MIN_W) + self._spad * 2,
                            self._px(MIN_H) + self._spad + self._spad_b)
        self.resize(self._px(DEFAULT_W) + self._spad * 2,
                    self._px(DEFAULT_H) + self._spad + self._spad_b)

        self._build()
        self._bind_shortcuts()

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

        self._goto("home")
        self.sync_from_config()
        self._restore_geometry()

    # ---------- 尺寸 / 定位 ----------
    def _px(self, value: float) -> int:
        return px(value, self._scale)

    def _restore_geometry(self):
        """恢复上次的位置与大小；配置里没有、太小或已不在屏幕上则居中。"""
        # 先把自己的几何当作「有效」——否则第一次运行时 _save_geometry
        # 会因为 _geometry_ready 永远是 False 而从不落盘，用户拖多大都白拖。
        self._geometry_ready = True
        geo = (self.controller.cfg.get("ui") or {}).get("panel_geometry")
        if isinstance(geo, (list, tuple)) and len(geo) == 4:
            try:
                x, y, w, h = (int(v) for v in geo)
            except (TypeError, ValueError):
                x = y = w = h = 0
            # 旧版默认只有 726x566，直接沿用会把面板钉在小尺寸上，
            # 用户明明调大过也没用 —— 小于最小尺寸的一律丢掉，按新默认居中。
            # 另外 V4.5 起整窗多了投影留白，旧几何要按新的下限抬一手。
            if w >= self._px(MIN_W) and h >= self._px(MIN_H) and \
                    self._on_screen(x, y, w, h):
                self.setGeometry(
                    x, y,
                    max(w, self.minimumWidth()),
                    max(h, self.minimumHeight()),
                )
                return
        self.move_to_center()

    def _on_screen(self, x, y, w, h) -> bool:
        """至少要留 80x40 在某个屏幕里，否则当成拔掉显示器后的无效坐标。"""
        try:
            probe = QRect(x, y, w, h)
            for screen in QGuiApplication.screens():
                hit = probe.intersected(screen.availableGeometry())
                if hit.width() >= self._px(80) and hit.height() >= self._px(40):
                    return True
        except Exception:
            pass
        return False

    def move_to_center(self):
        """首次打开时按屏幕比例给一个够大的尺寸，然后落在主屏中央。"""
        try:
            screens = QGuiApplication.screens()
            if not screens:
                return
            geo = screens[0].availableGeometry()
            # 固定 1120x740 在 1080p 上只占三分之一，用户会嫌小；
            # 按可用区域的 78% x 86% 取，但不超过默认值的 1.35 倍，
            # 免得 4K 屏上摊成一张铺满全屏的巨窗。
            # 这里的尺寸是「整窗」，含四周给投影留的空白，要把它加回去，
            # 否则加了投影之后可视面积会白白少一圈。
            want_w = int(min(geo.width() * 0.78,
                             self._px(DEFAULT_W) * 1.35)) + self._spad * 2
            want_h = int(min(geo.height() * 0.86,
                             self._px(DEFAULT_H) * 1.35)) + self._spad + self._spad_b
            self.resize(
                min(max(self.width(), want_w, self.minimumWidth()),
                    max(self.minimumWidth(), geo.width() - self._px(8))),
                min(max(self.height(), want_h, self.minimumHeight()),
                    max(self.minimumHeight(), geo.height() - self._px(8))),
            )
            self.move(
                max(0, geo.x() + (geo.width() - self.width()) // 2),
                max(0, geo.y() + (geo.height() - self.height()) // 2),
            )
        except Exception:
            pass

    def _save_geometry(self):
        if not self._geometry_ready:
            return
        g = self.geometry()
        self.controller.apply_panel_patch(
            {"ui": {"panel_geometry": [g.x(), g.y(), g.width(), g.height()]}}
        )

    # ---------- 构建 ----------
    def _build(self):
        outer = QHBoxLayout(self)
        # 四边留出投影的空白，底板画在这圈空白之内
        outer.setContentsMargins(self._spad, self._spad, self._spad, self._spad_b)
        outer.setSpacing(0)
        self._sidebar = self._build_sidebar()
        outer.addWidget(self._sidebar)
        outer.addWidget(self._build_main(), 1)

        # 描边必须压在所有内容之上，见 ui_shell._Rim 的说明
        self.ensure_rim()

        # 无边框窗口没有系统缩放边，右下角放一个透明的 QSizeGrip 负责拖拽，
        # 缩放手柄本身由 paintEvent 画
        self._grip = SilentGrip(self)
        self._grip.setFixedSize(self._px(16), self._px(16))
        self._grip.setToolTip("拖动可调整面板大小")
        self._grip.raise_()

    # ---------------- 侧栏 ----------------
    def _build_sidebar(self):
        side = _Sidebar(self._scale, self)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(self._px(12), self._px(16), self._px(12), self._px(12))
        lay.setSpacing(self._px(3))

        lay.addWidget(self._build_brand())
        lay.addSpacing(self._px(10))
        lay.addWidget(self._build_primary())
        lay.addSpacing(self._px(12))

        lay.addWidget(self._section_label("操作"))
        self._nav_rerun = NavItem("refresh", "重新识别", self._scale)
        self._nav_rerun.setToolTip("用当前词表重新识别所有贴图　Ctrl+R")
        self._nav_rerun.clicked.connect(self._on_recognize_all)
        lay.addWidget(self._nav_rerun)

        lay.addSpacing(self._px(12))
        lay.addWidget(self._section_label("设置"))

        for key, icon, title in NAV_PAGES[:-1]:
            item = NavItem(icon, title, self._scale)
            item.clicked.connect(lambda k=key: self._goto(k))
            self._navs[key] = item
            lay.addWidget(item)

        # V4.3：识别规则不再弹独立窗口，就在这里翻一页，所以不要箭头
        rules = NavItem(NAV_RULES[1], NAV_RULES[2], self._scale, value="")
        rules.setToolTip(
            "编辑要标注的类别、关键词和颜色。\n"
            "改完立刻用新词表重新识别所有贴图，不用重启程序。　Ctrl+E"
        )
        rules.clicked.connect(lambda: self._goto("rules"))
        self._navs["rules"] = rules
        lay.addWidget(rules)

        last_key, last_icon, last_title = NAV_PAGES[-1]
        item = NavItem(last_icon, last_title, self._scale)
        item.clicked.connect(lambda k=last_key: self._goto(k))
        self._navs[last_key] = item
        lay.addWidget(item)

        lay.addStretch(1)

        lay.addWidget(hline(self._scale, "#262626"))
        lay.addSpacing(self._px(4))
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(self._px(2))
        self._btn_close_all = QPushButton("关闭全部贴图")
        self._btn_close_all.setObjectName("ghost")
        self._btn_close_all.setCursor(Qt.PointingHandCursor)
        self._btn_close_all.setFocusPolicy(Qt.NoFocus)
        self._btn_close_all.setToolTip("关掉所有贴图窗口（识别服务继续后台运行）")
        self._btn_close_all.clicked.connect(self._on_close_all)
        tools.addWidget(self._btn_close_all)
        tools.addStretch(1)
        lay.addLayout(tools)
        lay.addSpacing(self._px(4))
        lay.addWidget(self._build_user_row())
        return side

    def _build_brand(self):
        box = _DragArea()          # 侧栏顶部空白也能拖动窗口
        lay = QHBoxLayout(box)
        lay.setContentsMargins(self._px(2), 0, self._px(1), 0)
        lay.setSpacing(self._px(9))

        # 程序图标：之前这里只有文字，任务栏和侧栏都看不出这是哪个程序
        side = self._px(32)
        self._logo = QLabel()
        self._logo.setFixedSize(side, side)
        self._logo.setAlignment(Qt.AlignCenter)
        self._logo.setStyleSheet("background:transparent;")
        self._logo.setToolTip("StoneEye · 框选屏幕，自动标出景别与运镜")
        pm = app_pixmap(side)
        if not pm.isNull():
            self._logo.setPixmap(pm)
        self._logo.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._logo, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(self._px(1))
        name = QLabel("StoneEye")
        name.setObjectName("brand")
        self._ver = QLabel(f"V{_version()}")
        self._ver.setObjectName("brandver")
        text.addWidget(name)
        text.addWidget(self._ver)
        lay.addLayout(text, 1)

        self._pill = QPushButton("● 已同步")
        self._pill.setObjectName("pill")
        self._pill.setCursor(Qt.PointingHandCursor)
        self._pill.setFocusPolicy(Qt.NoFocus)
        self._pill.setToolTip("所有改动即时生效并自动写入 config.json")
        lay.addWidget(self._pill, 0, Qt.AlignTop)
        return box

    def _build_primary(self):
        self._btn_new = AnimatedButton("新建框选", self._scale, kind="primary")
        self._btn_new.setProperty("primary", True)
        self._btn_new.setFixedHeight(self._px(37))
        hotkey = str((self.controller.cfg or {}).get("hotkey") or "")
        self._btn_new.setToolTip(f"框选一块屏幕区域并生成贴图　{hotkey}")
        self._btn_new.clicked.connect(self._on_new_selection)
        return self._btn_new

    def _section_label(self, text):
        label = QLabel(text)
        label.setObjectName("sectitle")
        label.setContentsMargins(self._px(8), 0, 0, self._px(2))
        # QSS 不支持 letter-spacing，字距只能走 QFont
        font = QFont(label.font())
        font.setLetterSpacing(QFont.AbsoluteSpacing, self._px(0.9))
        label.setFont(font)
        return label

    def _build_user_row(self):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(self._px(4), self._px(2), self._px(2), 0)
        lay.setSpacing(self._px(8))

        avatar = QLabel("屏")
        size = self._px(26)
        avatar.setFixedSize(size, size)
        avatar.setAlignment(Qt.AlignCenter)
        # 圆角半径必须 <= 边长的一半：>= 一半时 Qt 会退化成直角方块
        # （150% 缩放下 px(13)=20 > 39/2=19.5，实测头像变方）
        avatar.setStyleSheet(
            f"background:#2E2E2E; color:#EDEDED; border-radius:{size // 2}px;"
            f" font-size:{self._px(11)}px; font-weight:600;"
        )
        lay.addWidget(avatar)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        name = QLabel("本机")
        name.setStyleSheet(
            f"color:{TEXT}; font-size:{self._px(12)}px; background:transparent;")
        self._status = QLabel("● 加载中")
        self._status.setStyleSheet(
            f"color:{STATUS_COLORS['loading']}; font-size:{self._px(10)}px;"
            f" background:transparent;")
        text.addWidget(name)
        text.addWidget(self._status)
        lay.addLayout(text, 1)
        return box

    # ---------------- 右侧 ----------------
    def _build_main(self):
        main = QWidget()
        outer = QVBoxLayout(main)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_topbar())

        self._stack = QStackedWidget()
        self._pages["home"] = self._build_home_page()
        self._pages["display"] = self._build_display_page()
        self._pages["capture"] = self._build_capture_page()
        self._pages["rules"] = self._build_rules_page()
        self._pages["advanced"] = self._build_advanced_page()
        # 顺序必须和侧栏一致：概览 / 显示 / 采集 / 规则 / 高级
        for key in ("home", "display", "capture", "rules", "advanced"):
            self._stack.addWidget(self._pages[key])
        outer.addWidget(self._stack, 1)

        hint = QLabel("所有改动即时生效并自动写入 config.json")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        hint.setContentsMargins(0, self._px(4), 0, self._px(9))
        outer.addWidget(hint)
        return main

    def _build_topbar(self):
        bar = _DragArea()
        bar.setFixedHeight(self._px(44))
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(self._px(26), self._px(13), self._px(14), 0)
        lay.setSpacing(self._px(6))

        self._crumb = QLabel("概览")
        self._crumb.setStyleSheet(
            f"color:{TEXT_3}; font-size:{self._px(11)}px; background:transparent;")
        self._crumb.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._crumb)
        lay.addStretch(1)

        self._btn_copy = QPushButton("复制文本")
        self._btn_copy.setObjectName("ghost")
        self._btn_copy.setCursor(Qt.PointingHandCursor)
        self._btn_copy.setFocusPolicy(Qt.NoFocus)
        self._btn_copy.setToolTip(
            "复制最近一次识别出的文字。漏标时先看这里：\n"
            "字认对了 = 词表缺词；字认错了 = OCR 问题"
        )
        self._btn_copy.clicked.connect(self._on_copy_text)
        lay.addWidget(self._btn_copy)

        self._btn_reload = QPushButton("重载配置")
        self._btn_reload.setObjectName("ghost")
        self._btn_reload.setCursor(Qt.PointingHandCursor)
        self._btn_reload.setFocusPolicy(Qt.NoFocus)
        self._btn_reload.setToolTip("丢掉内存里的改动，重新从 config.json 读取")
        self._btn_reload.clicked.connect(self._on_reload)
        lay.addWidget(self._btn_reload)

        self._btn_json = QPushButton("配置文件")
        self._btn_json.setObjectName("ghost")
        self._btn_json.setCursor(Qt.PointingHandCursor)
        self._btn_json.setFocusPolicy(Qt.NoFocus)
        self._btn_json.setToolTip("用系统默认程序打开 config.json（更多选项在里面）")
        self._btn_json.clicked.connect(self._on_open_json)
        lay.addWidget(self._btn_json)

        sep = QFrame()
        sep.setFixedSize(1, self._px(15))
        sep.setStyleSheet("background:#2E2E2E; border:none;")
        lay.addSpacing(self._px(8))
        lay.addWidget(sep, 0, Qt.AlignVCenter)
        lay.addSpacing(self._px(6))

        self._btn_min = QPushButton("–")
        self._btn_min.setObjectName("wbtn")
        self._btn_min.setFixedSize(self._px(26), self._px(26))
        self._btn_min.setCursor(Qt.PointingHandCursor)
        self._btn_min.setFocusPolicy(Qt.NoFocus)
        self._btn_min.setToolTip("最小化（任务栏 / Alt+Tab 都能找回）")
        self._btn_min.clicked.connect(self.showMinimized)
        lay.addWidget(self._btn_min)
        lay.addSpacing(self._px(1))

        self._btn_hide = QPushButton("×")
        self._btn_hide.setObjectName("wbtn")
        self._btn_hide.setProperty("close", True)
        self._btn_hide.setFixedSize(self._px(26), self._px(26))
        self._btn_hide.setCursor(Qt.PointingHandCursor)
        self._btn_hide.setFocusPolicy(Qt.NoFocus)
        self._btn_hide.setToolTip("收起面板（Esc），后台识别继续工作")
        self._btn_hide.clicked.connect(self.hide)
        lay.addSpacing(self._px(2))
        lay.addWidget(self._btn_hide)
        return bar

    def _page(self, key, title, subtitle):
        """一页 = 滚动区 + 居中 Hero 标题 + 内容列。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)

        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(self._px(30), self._px(2), self._px(30),
                               self._px(14))
        lay.setSpacing(self._px(12))
        # 内容少的时候不要全挤在顶上，上下各留一份弹性空间，
        # 比例 3:4 让内容重心落在几何中心略上方，看着更稳
        lay.addStretch(3)

        hero = QLabel(title)
        hero.setObjectName("hero")
        hero.setAlignment(Qt.AlignCenter)
        sub = QLabel(subtitle)
        sub.setObjectName("herosub")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(hero)
        lay.addWidget(sub)
        lay.addSpacing(self._px(4))
        self._content_layouts = getattr(self, "_content_layouts", {})
        self._content_layouts[key] = lay
        return scroll

    def _build_home_page(self):
        scroll = self._page("home", "StoneEye，帮你标",
                            "框住屏幕任意一块 · 自动圈出景别与运镜")
        lay = self._content_layouts["home"]

        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(self._px(8))
        chips.addStretch(1)
        self._chip_new = chip_label("新建框选", self._scale, primary=True)
        self._chip_new.clicked.connect(self._on_new_selection)
        self._chip_reocr = chip_label("重新识别", self._scale)
        self._chip_reocr.clicked.connect(self._on_recognize_all)
        self._chip_copy = chip_label("复制识别文本", self._scale)
        self._chip_copy.clicked.connect(self._on_copy_text)
        self._chip_rules = chip_label("编辑识别规则", self._scale)
        self._chip_rules.clicked.connect(self._on_edit_rules)
        for btn in (self._chip_new, self._chip_reocr, self._chip_copy,
                    self._chip_rules):
            chips.addWidget(btn)
        chips.addStretch(1)
        lay.addLayout(chips)
        lay.addSpacing(self._px(2))

        # 指标卡
        frame, box = card(self._scale, 10)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(self._px(6))
        head.addWidget(caption("运行状态"))
        head.addStretch(1)
        self._engine = QLabel("● 加载中")
        self._engine.setStyleSheet(
            f"color:{STATUS_COLORS['loading']}; font-size:{self._px(10)}px;"
            f" background:transparent;")
        head.addWidget(self._engine)
        box.addLayout(head)
        box.addWidget(hline(self._scale))

        row = QHBoxLayout()
        row.setContentsMargins(0, self._px(2), 0, 0)
        row.setSpacing(self._px(6))
        self._m_last = Metric("—", "最近耗时", self._scale)
        self._m_avg = Metric("—", "平均耗时", self._scale)
        self._m_count = Metric("0", "累计识别", self._scale)
        self._m_windows = Metric("0", "贴图窗口", self._scale)
        for m in (self._m_last, self._m_avg, self._m_count, self._m_windows):
            row.addWidget(m, 1)
        box.addLayout(row)
        lay.addWidget(frame)

        # 最近一次识别
        frame2, box2 = card(self._scale, 7)
        head2 = QHBoxLayout()
        head2.setContentsMargins(0, 0, 0, 0)
        head2.setSpacing(self._px(6))
        head2.addWidget(caption("最近一次识别"))
        head2.addStretch(1)
        self._btn_copy2 = QPushButton("复制")
        self._btn_copy2.setObjectName("mini")
        self._btn_copy2.setCursor(Qt.PointingHandCursor)
        self._btn_copy2.setFocusPolicy(Qt.NoFocus)
        self._btn_copy2.clicked.connect(self._on_copy_text)
        head2.addWidget(self._btn_copy2)
        box2.addLayout(head2)
        self._preview = QLabel("还没有识别结果，先框选一块屏幕试试。")
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet(
            f"color:{TEXT_2}; font-size:{self._px(11)}px; background:transparent;"
            f" line-height:160%;")
        box2.addWidget(self._preview)
        lay.addWidget(frame2)
        lay.addWidget(self._build_hotkey_card())
        lay.addStretch(4)
        return scroll

    def _build_hotkey_card(self):
        frame, box = card(self._scale, 8)
        box.addWidget(caption("快捷键"))
        row = QHBoxLayout()
        row.setContentsMargins(0, self._px(1), 0, 0)
        row.setSpacing(self._px(7))
        for keys, what in (("Ctrl N", "新建框选"), ("Ctrl R", "重新识别"),
                           ("Ctrl E", "编辑规则"), ("Esc", "收起面板")):
            kbd = QLabel(keys)
            kbd.setObjectName("kbd")
            kbd.setAlignment(Qt.AlignCenter)
            tip = QLabel(what)
            tip.setStyleSheet(
                f"color:{TEXT_3}; font-size:{self._px(11)}px;"
                f" background:transparent;")
            row.addWidget(kbd)
            row.addWidget(tip)
            row.addSpacing(self._px(6))
        row.addStretch(1)
        box.addLayout(row)
        return frame

    def _build_display_page(self):
        scroll = self._page("display", "显示与外观",
                            "决定贴图上出现什么、不出现什么")
        lay = self._content_layouts["display"]

        frame, box = card(self._scale, 2)
        box.addWidget(caption("贴图"))
        self._sw_progress = self._add_switch(
            box, "顶部进度条", "live", "show_progress",
            "识别时在贴图顶部画一条细进度条")
        self._sw_status = self._add_switch(
            box, "状态条", "live", "show_status",
            "贴图角落的深色小条，显示耗时、命中数和图例")
        self._sw_legend = self._add_switch(
            box, "分类图例", "live", "show_legend",
            "在状态条里用色块标出每一类命中了几个")
        self._sw_border = self._add_switch(
            box, "贴图边框", "sticker", "show_border",
            "贴图四周画一圈亮边并闪现一下，原位粘贴时也能看出截了哪块",
            reocr=True)
        lay.addWidget(frame)

        frame2, box2 = card(self._scale, 8)
        box2.addWidget(caption("状态条位置"))
        live = (self.controller.cfg or {}).get("live", {}) or {}
        self._corner = CornerPicker(
            live.get("status_corner", "bottom_right"), self._scale)
        self._corner.picked.connect(
            lambda key: self._push({"live": {"status_corner": str(key)}}))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(self._px(18))
        # 示意图拉满整行会变成 700px 长、190px 高的极端比例，
        # 里面那几条假文字就成了一根根进度条；限宽 460 才像一段正文。
        # 但写死宽度在 900px 小窗里又会把说明挤成一条窄柱，所以给上下限 + 3:2 分配
        self._corner.setMinimumWidth(self._px(300))
        self._corner.setMaximumWidth(self._px(460))
        row.addWidget(self._corner, 3)
        row.addWidget(self._hint_label(
            "直接点示意图的四个角即可。\n"
            "右下最稳：行尾通常空白，压不到正文。"), 2)
        box2.addLayout(row)
        lay.addWidget(frame2)
        lay.addStretch(4)
        return scroll

    def _build_capture_page(self):
        scroll = self._page("capture", "采集与识别",
                            "多久抓一次 · 按句还是按词 · 精度怎么取舍")
        lay = self._content_layouts["capture"]
        live = (self.controller.cfg or {}).get("live", {}) or {}

        frame, box = card(self._scale, 2)
        box.addWidget(caption("采集"))
        self._mode = SegmentedControl(
            MODE_OPTIONS, live.get("capture_mode", "snapshot"), self._scale)
        self._mode.setToolTip(
            "快照定格：框选那一刻的画面冻结成图片，之后切屏也不变\n"
            "实时刷新：持续抓同一区域，适合盯一块会变的面板")
        self._mode.setMaximumWidth(self._px(330))
        self._mode.picked.connect(self._on_mode_pick)
        box.addLayout(self._labeled("模式", self._mode))
        self._sw_adaptive = self._add_switch(
            box, "静止降频", "live", "adaptive_capture",
            "画面长时间不动时自动降低抓屏频率，省 CPU")
        lay.addWidget(frame)

        frame2, box2 = card(self._scale, 2)
        box2.addWidget(caption("标注"))
        self._grain = SegmentedControl(
            GRAIN_OPTIONS, live.get("highlight_mode", "clause"), self._scale)
        self._grain.setToolTip(
            "整句标注：以逗号 / 句号 / 分号断句，命中的整句上色\n"
            "词级标注：只框住命中的词本身")
        self._grain.setMaximumWidth(self._px(330))
        self._grain.picked.connect(self._on_grain_pick)
        box2.addLayout(self._labeled("粒度", self._grain))
        lay.addWidget(frame2)

        frame3, box3 = card(self._scale, 2)
        box3.addWidget(caption("精度"))
        self._sw_guard = self._add_switch(
            box3, "语义校验", "live", "context_guard",
            "裸运镜词必须带镜头语境才标注，避免「坐凳环绕草坪」这类物体动作被误标",
            reocr=True)
        self._sw_snap = self._add_switch(
            box3, "框贴合字", "ocr", "snap_boxes",
            "用像素墨迹收紧 OCR 框、吸附字符边界，标注框和下划线更贴字",
            reocr=True)
        self._sw_detail = self._add_switch(
            box3, "局部重认", "ocr", "detail_retry",
            "句子明显在讲镜头却没命中时，把那一句放大 2x 再认一次（略增耗时）",
            reocr=True)
        lay.addWidget(frame3)
        lay.addStretch(4)
        return scroll

    def _build_rules_page(self):
        """识别规则页（V4.3）：编辑器直接嵌在面板里，不弹独立窗口。"""
        from .rule_editor import RulesPage      # 打开面板时才加载规则引擎

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)

        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(self._px(30), self._px(8), self._px(30),
                               self._px(14))
        lay.setSpacing(self._px(10))

        hero = QLabel("识别规则")
        hero.setObjectName("hero")
        hero.setAlignment(Qt.AlignCenter)
        sub = QLabel("命中这些词就在贴图上标注 · 保存后立刻用新词表重识别")
        sub.setObjectName("herosub")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(hero)
        lay.addWidget(sub)
        lay.addSpacing(self._px(2))

        self._rules_page = RulesPage(self.controller, self._scale)
        self._rules_page.saved.connect(self._on_rules_saved)
        lay.addWidget(self._rules_page, 1)
        return scroll

    def _build_advanced_page(self):
        scroll = self._page("advanced", "高级参数",
                            "改完即时生效 · 想回到出厂值点右上角")
        lay = self._content_layouts["advanced"]

        frame, box = card(self._scale, 4)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, self._px(2))
        head.setSpacing(self._px(6))
        head.addWidget(caption("采集与渲染"))
        head.addStretch(1)
        reset = QPushButton("恢复默认")
        reset.setObjectName("mini")
        reset.setCursor(Qt.PointingHandCursor)
        reset.setFocusPolicy(Qt.NoFocus)
        reset.setToolTip("把下面这些参数恢复成出厂值（不影响词表）")
        reset.clicked.connect(self._on_reset_advanced)
        head.addWidget(reset)
        box.addLayout(head)
        box.addWidget(hline(self._scale))

        for section_name, key, title, low, high, suffix, sc, dec, tip in ADVANCED_SLIDERS:
            self._add_slider(box, title, section_name, key, low, high,
                             suffix, sc, dec, tip)
        lay.addWidget(frame)

        lay.addWidget(self._hint_label(
            "采集 / 识别间隔调大更省 CPU；帧差阈值调小更灵敏。"
            "单帧块数设 0 表示不限制，区域越大反而越慢。"))
        lay.addStretch(4)
        return scroll

    # ---------- 控件工厂 ----------
    def _hint_label(self, text):
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        label.setContentsMargins(self._px(4), self._px(2), self._px(4), 0)
        return label

    def _labeled(self, text, widget):
        row = QHBoxLayout()
        row.setContentsMargins(self._px(2), self._px(2), self._px(2), self._px(2))
        row.setSpacing(self._px(10))
        name = QLabel(text)
        name.setObjectName("field")
        name.setFixedWidth(self._px(36))
        row.addWidget(name)
        row.addWidget(widget, 0)
        row.addStretch(1)
        return row

    def _add_switch(self, layout, label, section, key, tip, reocr=False,
                    hint=""):
        sw = SwitchRow(label, self._scale, hint=hint)
        sw.setToolTip(tip)
        sw.toggled.connect(
            lambda on, sec=section, k=key, r=reocr:
            self._push({sec: {k: bool(on)}}, reocr=r)
        )
        layout.addWidget(sw)
        self._switches[(section, key)] = sw
        return sw

    def _add_slider(self, layout, label, section, key,
                    low, high, suffix, scale=1.0, decimals=0, tip=""):
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, self._px(3), 0, self._px(3))
        box.setSpacing(self._px(12))

        name = QLabel(label)
        name.setObjectName("field")
        name.setFixedWidth(self._px(58))

        slider = QSlider(Qt.Horizontal)
        slider.setRange(int(low), int(high))
        slider.setCursor(Qt.PointingHandCursor)
        slider.setFocusPolicy(Qt.NoFocus)

        value = QLabel("")
        value.setObjectName("value")
        value.setFixedWidth(self._px(54))
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        slider.valueChanged.connect(
            lambda v, s=slider, vl=value, sec=section, k=key,
                   sc=scale, sf=suffix, d=decimals:
            self._on_slider(s, vl, sec, k, sc, sf, d, v)
        )
        box.addWidget(name)
        box.addWidget(slider, 1)
        box.addWidget(value)
        if tip:
            row.setToolTip(tip)
        layout.addWidget(row)
        self._sliders[(section, key)] = (slider, value, scale, suffix, decimals)
        return slider

    def _bind_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self._on_new_selection)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._on_recognize_all)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self._on_edit_rules)

    # ---------- 分页 ----------
    def _goto(self, key: str):
        scroll = self._pages.get(key)
        if scroll is None:
            return
        changed = key != self._current
        self._current = key
        self._stack.setCurrentWidget(scroll)
        self._scroll = scroll          # 当前活动页的滚动区（外部调试/测试用）
        for name, item in self._navs.items():
            item.set_active(name == key)
        titles = {k: t for k, _i, t in NAV_PAGES}
        titles["rules"] = NAV_RULES[2]
        self._crumb.setText(titles.get(key, ""))
        self._sync_nav(animate=changed)
        if changed:
            self._fade_page(scroll)

    def _sync_nav(self, animate: bool = False):
        """让侧栏的选中胶囊对准当前页那一行。"""
        item = self._navs.get(self._current)
        sidebar = getattr(self, "_sidebar", None)
        if item is None or sidebar is None:
            return
        sidebar.set_highlight(QRectF(item.geometry()), animate=animate)

    def _fade_page(self, widget):
        """切页时新页面淡入 —— QSS 没有 transition，只能自己播。"""
        if not self.isVisible():
            return
        try:
            widget.setGraphicsEffect(None)
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(200)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda w=widget: self._end_fade(w))
            self._page_anim = anim
            anim.start()
            # 兜底：动画被系统掐掉（省电模式 / 远程桌面）也不会留下半透明页
            QTimer.singleShot(400, lambda w=widget: self._end_fade(w))
        except Exception:
            try:
                widget.setGraphicsEffect(None)
            except Exception:
                pass

    @staticmethod
    def _end_fade(widget):
        try:
            widget.setGraphicsEffect(None)
        except Exception:
            pass

    # ---------- 数值同步 ----------
    def sync_from_config(self):
        """把 controller 当前配置回填到所有控件（打开面板 / 重载配置时调用）。"""
        cfg = self.controller.cfg or {}
        self._syncing = True
        try:
            for (section, key), (slider, label, scale, suffix, decimals) \
                    in self._sliders.items():
                try:
                    value = float(cfg.get(section, {}).get(key, 0))
                except (TypeError, ValueError, AttributeError):
                    continue
                slider.setValue(int(round(value * scale)))
                label.setText(self._label_text(value, scale, decimals, suffix))

            for (section, key), switch in self._switches.items():
                switch.setChecked(bool(cfg.get(section, {}).get(key, True)))

            live = cfg.get("live", {}) or {}
            self._mode.set_value(live.get("capture_mode", "snapshot"))
            self._grain.set_value(live.get("highlight_mode", "clause"))
            self._corner.set_value(live.get("status_corner", "bottom_right"))
            self._refresh_rule_hint()
            page = getattr(self, "_rules_page", None)
            if page is not None:
                page.refresh_if_clean()
        finally:
            self._syncing = False
        self._refresh_stats()

    @staticmethod
    def _label_text(value, scale, decimals, suffix):
        """百分比滑块要显示 100% 而不是 1.0（内部仍存 1.0）。"""
        shown = value * scale if suffix == "%" else value
        return _fmt(shown, decimals, suffix)

    def _on_slider(self, slider, label, section, key, scale, suffix, decimals, raw):
        # raw 是滑块原始刻度，internal 是存进配置的值（缩放类按 100 折算）
        internal = round(raw / scale, 4) if scale else float(raw)
        label.setText(self._label_text(internal, scale, decimals, suffix))
        self._push({section: {key: internal}})
        # 引擎参数改了要重跑一次才看得到效果
        if not self._syncing and section == "ocr":
            self._schedule_reocr()

    def _schedule_reocr(self):
        """改完停顿 900ms 再重识别，避免拖滑块过程中反复触发。"""
        if self._reocr_timer is None:
            self._reocr_timer = QTimer(self)
            self._reocr_timer.setSingleShot(True)
            self._reocr_timer.setInterval(900)
            self._reocr_timer.timeout.connect(self.controller.recognize_all)
        self._reocr_timer.start()

    def _push(self, patch, reocr=False):
        if self._syncing:
            return
        self.controller.apply_panel_patch(patch, reocr=reocr)
        self._mark_applied()

    def _mark_applied(self):
        self._pill.setText("● 已应用")
        if self._saved_timer is None:
            self._saved_timer = QTimer(self)
            self._saved_timer.setSingleShot(True)
            self._saved_timer.setInterval(1300)
            self._saved_timer.timeout.connect(self._mark_saved)
        self._saved_timer.start()

    def _mark_saved(self):
        self._pill.setText(f"● 已保存 {datetime.now():%H:%M}")

    # ---------- 交互 ----------
    def _on_mode_pick(self, mode):
        self._mode.set_value(mode)
        self._push({"live": {"capture_mode": mode}})

    def _on_grain_pick(self, mode):
        self._grain.set_value(mode)
        self._push({"live": {"highlight_mode": mode}}, reocr=True)

    def _on_new_selection(self):
        # 不自己 hide()：controller 会在拍屏前收起面板、取消框选时再还回来，
        # 这里抢先隐藏会让「取消」之后面板不见了。
        self.controller.start_selection()

    def _on_recognize_all(self):
        self.controller.recognize_all()
        self._pill.setText("● 已提交重识别")

    def _on_close_all(self):
        self.controller.close_all()

    def _on_reload(self):
        self.controller.reload_config()
        self.sync_from_config()
        self._pill.setText("● 已重新加载")

    def _on_open_json(self):
        self.controller.open_config()

    def _on_reset_advanced(self):
        defaults = ConfigManager.defaults()
        patch = {}
        self._syncing = True
        try:
            for (section, key), (slider, label, sc, suffix, dec) \
                    in self._sliders.items():
                raw = defaults.get(section, {}).get(key)
                if raw is None:
                    continue
                slider.setValue(int(round(float(raw) * sc)))
                patch.setdefault(section, {})[key] = (
                    round(float(raw), dec) if dec else round(float(raw))
                )
            for (section, key), switch in self._switches.items():
                if (section, key) == ("live", "adaptive_capture"):
                    value = bool(defaults.get(section, {}).get(key, True))
                    switch.setChecked(value)
                    patch.setdefault(section, {})[key] = value
        finally:
            self._syncing = False
        self._push(patch, reocr=True)
        self._pill.setText("● 高级参数已复位")

    def _refresh_rule_hint(self):
        rules = (self.controller.cfg or {}).get("rules", []) or []
        words = sum(len(r.get("patterns") or []) for r in rules)
        active = sum(1 for r in rules if r.get("enabled", True))
        item = self._navs.get("rules")
        if item is not None:
            item.set_value(f"{active}/{len(rules)}类 · {words}词")

    def _on_edit_rules(self):
        """跳到「识别规则」页 —— 不再另开窗口。"""
        self._goto("rules")

    def _on_rules_saved(self):
        self._refresh_rule_hint()
        self._mark_applied()

    def _on_copy_text(self):
        """漏标时先确认是 OCR 认错字，还是词表里真的没有这个词。"""
        manager = getattr(self.controller, "manager", None)
        text = manager.latest_text() if manager is not None else ""
        if not text:
            self._flash_copy("暂无文本", "暂无可复制的文本")
            return
        QGuiApplication.clipboard().setText(text)
        self._flash_copy(f"已复制 {len(text)} 字", f"已复制 {len(text)} 字")

    def _flash_copy(self, short, full=None):
        if full:
            self._preview.setText(full)
        self._btn_copy.setText(short)
        self._btn_copy2.setText("已复制")
        QTimer.singleShot(
            1500, lambda: (self._btn_copy.setText("复制文本"),
                           self._btn_copy2.setText("复制")))

    # ---------- 状态 ----------
    def _refresh_stats(self):
        service = getattr(self.controller, "service", None)
        manager = getattr(self.controller, "manager", None)
        windows = manager.count() if manager is not None else 0
        self._btn_close_all.setEnabled(windows > 0)
        self._nav_rerun.setEnabled(windows > 0)
        self._chip_reocr.setEnabled(windows > 0)
        self._m_windows.set_value(str(windows))

        text = ""
        if manager is not None:
            try:
                text = manager.latest_text() or ""
            except Exception:
                text = ""
        if text != getattr(self, "_latest_text", None):
            self._latest_text = text
            self._preview.setText(self._elide(text, 160) if text
                                  else "还没有识别结果，先框选一块屏幕试试。")

        if service is None:
            return
        try:
            st = service.stats()
        except Exception:
            return

        if st.get("ready"):
            status, color = "就绪", STATUS_COLORS["ready"]
        elif st.get("attempted"):
            status, color = "不可用", STATUS_COLORS["failed"]
        else:
            status, color = "加载中", STATUS_COLORS["loading"]
        if status != getattr(self, "_status_text", None):
            # 每秒都重设 QSS 会触发一次样式重算，只在状态真的变化时更新
            self._status_text = status
            self._status.setText(f"● {status}")
            self._status.setStyleSheet(
                f"color:{color}; font-size:{self._px(10)}px; background:transparent;")
            self._engine.setText(f"● {status}")
            self._engine.setStyleSheet(
                f"color:{color}; font-size:{self._px(10)}px; background:transparent;")

        last = float(st.get("last_ms", 0.0))
        avg = float(st.get("avg_ms", 0.0))
        self._m_last.set_value(f"{last:.0f}" if last > 0 else "—")
        self._m_avg.set_value(f"{avg:.0f}" if avg > 0 else "—")
        self._m_count.set_value(str(int(st.get("ocr_count", 0))))

    def _elide(self, text: str, limit: int) -> str:
        """把长文本压成一行，超出用省略号，避免卡片被撑高。"""
        flat = " ".join(text.split())
        if len(flat) <= limit:
            return flat
        return flat[:limit].rstrip() + "…"

    # ---------- 窗口行为 ----------
    def paint_beyond_body(self, painter, body):
        """底板画完之后再补一笔：右下角三条 45 度斜线，暗示这里能拖拽改大小。"""
        painter.setPen(QPen(QColor("#3A3A3A"), max(1, self._px(1))))
        corner = self._px(5)
        tick = self._px(6)
        for i in range(3):
            gap = i * self._px(4)
            painter.drawLine(
                int(body.right() - corner - tick - gap), int(body.bottom() - corner),
                int(body.right() - corner), int(body.bottom() - corner - tick - gap),
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)      # 描边层跟着整窗走
        if getattr(self, "_grip", None) is not None:
            self._grip.move(
                self.width() - self._grip.width() - self._spad - self._px(3),
                self.height() - self._grip.height() - self._spad_b - self._px(3),
            )
            self._grip.raise_()
        if getattr(self, "_navs", None):
            self._sync_nav(animate=False)

    def showEvent(self, event):
        super().showEvent(event)
        self._geometry_ready = True
        # 从最小化状态被叫回来时，光 show() 不管用，得先清掉窗口状态
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.sync_from_config()
        # 布局这会儿才算出每行的位置，选中胶囊要等一拍才能对准
        QTimer.singleShot(0, lambda: self._sync_nav(animate=False))
        if not getattr(self, "_fade_done", False):
            self._fade_done = True
            QTimer.singleShot(0, self._fade_in)

    def hideEvent(self, event):
        super().hideEvent(event)
        # 入场动画播到一半被收起的话，不归位窗口就会停在下沉的位置
        self.finish_slide_in()
        self._save_geometry()

    def closeEvent(self, event):
        # 只隐藏，后台识别 / 贴图继续工作
        event.ignore()
        self._save_geometry()
        self.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)


def _version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:
        return "4.1"
