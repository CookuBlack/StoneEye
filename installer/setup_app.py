# -*- coding: utf-8 -*-
"""StoneEye 安装程序（V4.9.3）。

为什么自己写而不上 Inno Setup / NSIS：
  - 这台机器访问不了 GitHub，winget 拉 Inno Setup 直接 InternetOpenUrl 失败；
  - 自己写能把「和面板同一张脸」这件事做到底 —— 同样的圆角浮窗、同样的
    灰阶、同样的补间按钮，用户不会觉得装完打开像换了个软件。

产物是**单文件 exe**，payload.zip（应用本体）通过 PyInstaller 打包进去，
运行时解到 %LOCALAPPDATA%\\Programs\\StoneEye。

设计要点：
  - 免管理员：全部写 HKCU + 用户目录，双击就能装，不弹 UAC；
  - 卸载信息写进「应用和功能」，卸载程序就是本 exe 的副本（`--uninstall`）；
  - 解压在子线程里跑，进度按字节算，界面不卡；
  - 三个可选项（桌面 / 开始菜单 / 开机自启）都能反悔，装完还能再改。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import winreg
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from app import __version__
from app.assets import app_icon, app_pixmap, resource_path
from app.ui_theme import (
    BD_CARD, BG_CARD, BG_SIDE, BG_TRACK, PANEL_QSS, TEXT_2, TEXT_3, scaled_qss,
)
from app.ui_shell import ShellWindow
from app.ui_widgets import AnimatedButton, SwitchRow, card, caption, px

from app.winenv import (      # noqa: E402 - 依赖上面的 sys.path 处理
    APP_ID, APP_NAME, CREATE_NO_WINDOW, EXE_NAME, PUBLISHER, RUN_KEY,
    UNINSTALL_KEY, app_running, default_dir, desktop_dir, dir_size, human,
    install_location, kill_app, make_shortcut, remove_install,
    schedule_self_delete, set_reg, start_menu_dir,
)

WIN_W, WIN_H = 880, 580          # 逻辑像素（不含投影留白）
SIDEBAR_W = 248


# ---------------------------------------------------------------- 路径与系统
def payload_path() -> str:
    """payload.zip 在打包后位于 _MEIPASS，开发时在 installer/ 下。"""
    cand = resource_path("payload.zip")
    if os.path.exists(cand):
        return cand
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "payload.zip")


def payload_info() -> dict:
    info_path = resource_path("payload_info.json")
    try:
        with open(info_path, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        pass
    try:
        with zipfile.ZipFile(payload_path()) as zf:
            total = sum(i.file_size for i in zf.infolist())
            return dict(version=__version__, files=len(zf.infolist()), bytes=total)
    except Exception:
        return dict(version=__version__, files=0, bytes=0)


def extract_payload(target: str, progress=None, cancelled=None) -> None:
    """把 payload.zip 铺到 target。进度回调 (已完成字节, 总字节, 当前文件)。"""
    src = payload_path()
    if not os.path.exists(src):
        raise RuntimeError("找不到 payload.zip，安装包可能不完整。")
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        items = zf.infolist()
        total = sum(i.file_size for i in items) or 1
        done = 0
        for item in items:
            if cancelled is not None and cancelled():
                raise RuntimeError("已取消安装。")
            if progress is not None:
                progress(done, total, item.filename)
            zf.extract(item, target)
            done += item.file_size
        if progress is not None:
            progress(total, total, "")


def finalize_install(target: str, version: str, desktop: bool, startmenu: bool,
                     autostart: bool) -> str:
    """文件铺好之后的事：快捷方式、开机自启、卸载登记。返回错误说明（空=全好）。"""
    exe = os.path.join(target, EXE_NAME)
    icon = exe + ",0"
    problem = ""
    try:
        if desktop:
            make_shortcut(os.path.join(desktop_dir(), APP_NAME + ".lnk"),
                          exe, target, icon, APP_NAME)
        if startmenu:
            make_shortcut(os.path.join(start_menu_dir(), APP_NAME + ".lnk"),
                          exe, target, icon, APP_NAME)
        if autostart:
            set_reg(RUN_KEY, APP_ID, '"%s" ' % exe)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, APP_ID)
            except OSError:
                pass

        set_reg(UNINSTALL_KEY, "DisplayName", APP_NAME)
        set_reg(UNINSTALL_KEY, "DisplayVersion", str(version))
        set_reg(UNINSTALL_KEY, "Publisher", PUBLISHER)
        set_reg(UNINSTALL_KEY, "DisplayIcon", icon)
        set_reg(UNINSTALL_KEY, "InstallLocation", target)
        # 卸载入口是应用本体：装完只有一份 exe，不用再塞一个 47 MB 的卸载器
        set_reg(UNINSTALL_KEY, "UninstallString", '"%s" --uninstall' % exe)
        set_reg(UNINSTALL_KEY, "QuietUninstallString", '"%s" --uninstall --quiet' % exe)
        set_reg(UNINSTALL_KEY, "NoModify", 1, winreg.REG_DWORD)
        set_reg(UNINSTALL_KEY, "NoRepair", 1, winreg.REG_DWORD)
        set_reg(UNINSTALL_KEY, "EstimatedSize",
                int(dir_size(target) / 1024), winreg.REG_DWORD)
    except Exception as exc:      # noqa: BLE001
        problem = "%s: %s" % (type(exc).__name__, exc)
    return problem


class InstallWorker(QThread):
    progressed = Signal(int, int, str)      # 已完成字节 / 总字节 / 当前文件
    failed = Signal(str)
    done = Signal()

    def __init__(self, target: str, parent=None):
        super().__init__(parent)
        self.target = target
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        try:
            extract_payload(self.target,
                            progress=lambda d, t, n: self.progressed.emit(d, t, n),
                            cancelled=lambda: self.cancelled)
            self.done.emit()
        except Exception as exc:      # noqa: BLE001 - 装机失败要把原因摊给用户
            self.failed.emit(str(exc))


class UninstallWorker(QThread):
    progressed = Signal(int, str)
    done = Signal()

    def __init__(self, target: str, parent=None):
        super().__init__(parent)
        self.target = target

    def run(self):
        remove_install(self.target)
        self.done.emit()


# ---------------------------------------------------------------- 小组件
class StepItem(QWidget):
    """左侧步骤条的一行：圆圈序号 / 当前步高亮 / 已完成的打勾。"""

    def __init__(self, index: int, text: str, scale: float, parent=None):
        super().__init__(parent)
        self._index = index
        self._text = text
        self._scale = scale
        self._state = "pending"     # pending / active / done
        self.setFixedHeight(px(38, scale))

    def set_state(self, state: str):
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        scale = self._scale
        d = px(19, scale)
        cx, cy = d / 2.0, self.height() / 2.0
        active = self._state == "active"
        done = self._state == "done"

        box = QPainterPath()
        box.addEllipse(0, cy - d / 2.0, d, d)
        if active:
            p.fillPath(box, QColor("#EFEFEF"))
            num_color = QColor("#121212")
        else:
            p.setPen(QPen(QColor("#3A3A3A" if not done else "#4A4A4A"), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(box)
            num_color = QColor("#8A8A8A" if done else "#5E5E5E")

        font = QFont(self.font())
        font.setPixelSize(px(10, scale))
        font.setBold(True)
        p.setFont(font)
        p.setPen(num_color)
        p.drawText(int(0), int(cy - d / 2.0), int(d), int(d),
                   Qt.AlignCenter, "✓" if done else str(self._index + 1))

        font.setPixelSize(px(13, scale))
        font.setBold(active)
        p.setFont(font)
        p.setPen(QColor("#F2F2F2" if active else ("#9A9A9A" if done else "#6A6A6A")))
        p.drawText(int(px(30, scale)), 0, self.width() - int(px(30, scale)),
                   self.height(), Qt.AlignVCenter | Qt.AlignLeft, self._text)
        p.end()


class Side(QWidget):
    """侧栏底板：只画左半边的圆角，右半边多画一个半径落到控件外面。

    面板里也是这么干的 —— 两侧同色的时候，一条 1px 分隔线比换成不同底色更
    廉价、也更不像"两张纸拼起来"。
    """

    def __init__(self, scale: float, parent=None):
        super().__init__(parent)
        self._scale = scale
        self.setFixedWidth(px(SIDEBAR_W, scale))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = px(18, self._scale)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width() + r, self.height()), r, r)
        p.fillPath(path, QColor(BG_SIDE))
        p.end()


class Bar(QWidget):
    """细进度条：自绘，圆头，带一点"正在跑"的呼吸感。"""

    def __init__(self, scale: float, parent=None):
        super().__init__(parent)
        self._scale = scale
        self._value = 0.0
        self.setFixedHeight(px(8, scale))

    def set_value(self, value: float):
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._value) > 0.0005:
            self._value = value
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        h = self.height()
        r = h / 2.0
        track = QPainterPath()
        track.addRoundedRect(0, 0, self.width(), h, r, r)
        p.fillPath(track, QColor(BG_TRACK))
        if self._value > 0:
            w = max(h, self.width() * self._value)
            fill = QPainterPath()
            fill.addRoundedRect(0, 0, w, h, r, r)
            p.fillPath(fill, QColor("#EFEFEF"))
        p.end()


# ---------------------------------------------------------------- 安装窗口
class SetupWindow(ShellWindow):
    def __init__(self):
        super().__init__()
        self._info = payload_info()
        self._target = default_dir()
        self._worker = None
        self._installing = False
        self._finished = False

        self.setObjectName("root")
        extra = """
QWidget#root { font-family: "Microsoft YaHei UI","Segoe UI Variable Text","Segoe UI";
               font-size: 13px; color: #EFEFEF; }
QLabel { background: transparent; font-family: "Microsoft YaHei UI","Segoe UI"; }
QLabel#wiztitle { font-size: 19px; font-weight: 600; color: #F6F6F6; }
QLabel#wizsub   { font-size: 12px; color: %(TEXT_3)s; }
QLabel#brand    { font-size: 15px; font-weight: 600; color: #F4F4F4; }
QLabel#brandver { font-size: 11px; color: %(TEXT_3)s;
                  font-family: "Cascadia Mono","Consolas"; }
QLabel#caption  { font-size: 11px; font-weight: 600; color: %(TEXT_2)s; }
QLabel#hint     { font-size: 12px; color: %(TEXT_3)s; }
QLabel#pathval  { font-size: 12px; color: #DCDCDC;
                  font-family: "Cascadia Mono","Consolas"; }
QLabel#big      { font-size: 26px; font-weight: 600; color: #F7F7F7; }
QFrame#card { background: %(BG_CARD)s; border: 1px solid %(BD_CARD)s;
              border-radius: %(R_CARD)spx; }
QWidget#swrow { background: transparent; border-radius: 8px; }
QWidget#swrow:hover { background: #232323; }
QLineEdit { background: #1D1D1D; color: #F0F0F0; border: 1px solid #303030;
            border-radius: 9px; padding: 9px 11px; font-size: 12px;
            font-family: "Cascadia Mono","Consolas";
            selection-background-color: #4A4A4A; }
QLineEdit:focus { border: 1px solid #6E6E6E; background: #1F1F1F; }
""" % dict(TEXT_2=TEXT_2, TEXT_3=TEXT_3, BG_CARD=BG_CARD, BD_CARD=BD_CARD,
           R_CARD="14")
        self.setStyleSheet(scaled_qss(PANEL_QSS, self._scale) + scaled_qss(extra, self._scale))
        self.setWindowTitle("安装 %s" % APP_NAME)
        self.setWindowIcon(app_icon())
        self.setFixedSize(self._px(WIN_W) + self._spad * 2,
                          self._px(WIN_H) + self._spad * 2)

        self._build()
        self.center_on_screen()

    # ---------- 布局 ----------
    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(self._spad, self._spad, self._spad, self._spad)
        outer.setSpacing(0)
        outer.addWidget(self._build_side())
        outer.addWidget(self._build_main(), 1)
        self.ensure_rim()

        # 关掉窗口 = 取消；安装中要拦一下
        self._btn_close.clicked.connect(self._on_cancel)

    def _build_side(self) -> QWidget:
        side = Side(self._scale)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(self._px(26), self._px(30), self._px(20), self._px(24))
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(self._px(11))
        logo = QLabel()
        logo.setPixmap(app_pixmap(self._px(34)))
        logo.setFixedSize(self._px(34), self._px(34))
        head.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(self._px(1))
        name = QLabel(APP_NAME)
        name.setObjectName("brand")
        ver = QLabel("安装程序 · v%s" % self._info.get("version", __version__))
        ver.setObjectName("brandver")
        col.addWidget(name)
        col.addWidget(ver)
        head.addLayout(col)
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(self._px(30))

        self._steps = []
        for i, text in enumerate(("安装位置", "附加任务", "正在安装")):
            item = StepItem(i, text, self._scale)
            self._steps.append(item)
            lay.addWidget(item)
            lay.addSpacing(self._px(6))

        lay.addStretch(1)
        self._size_label = QLabel("")
        self._size_label.setObjectName("hint")
        self._size_label.setWordWrap(True)
        lay.addWidget(self._size_label)
        self._set_size_label()
        return side

    def _set_size_label(self):
        total = self._info.get("bytes", 0)
        self._size_label.setText(
            "本安装包内含 %s 个文件\n安装后占用约 %s"
            % (self._info.get("files", 0), human(total)))

    def _build_main(self) -> QWidget:
        main = QWidget()
        lay = QVBoxLayout(main)
        lay.setContentsMargins(self._px(6), self._px(22), self._px(26), self._px(18))
        lay.setSpacing(0)

        # 顶部标题行 + 关闭按钮
        top = QHBoxLayout()
        top.setSpacing(self._px(8))
        title_col = QVBoxLayout()
        title_col.setSpacing(self._px(4))
        self._title = QLabel("")
        self._title.setObjectName("wiztitle")
        self._subtitle = QLabel("")
        self._subtitle.setObjectName("wizsub")
        title_col.addWidget(self._title)
        title_col.addWidget(self._subtitle)
        top.addLayout(title_col, 1)
        self._btn_close = QPushButton("×", self)
        self._btn_close.setObjectName("wbtn")       # 透明底 + 悬停变灰，见 PANEL_QSS
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.setFixedSize(self._px(26), self._px(26))
        top.addWidget(self._btn_close, 0, Qt.AlignTop)
        lay.addLayout(top)
        lay.addSpacing(self._px(20))

        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_dest())
        self._stack.addWidget(self._page_tasks())
        self._stack.addWidget(self._page_install())
        lay.addWidget(self._stack, 1)
        lay.addSpacing(self._px(14))

        foot = QHBoxLayout()
        foot.setSpacing(self._px(10))
        self._btn_cancel = AnimatedButton("取消", self._scale, kind="chip")
        self._btn_cancel.clicked.connect(self._on_cancel)
        foot.addWidget(self._btn_cancel)
        foot.addStretch(1)
        self._btn_back = AnimatedButton("上一步", self._scale, kind="chip")
        self._btn_back.clicked.connect(self._on_back)
        self._btn_next = AnimatedButton("下一步", self._scale, kind="primary")
        self._btn_next.clicked.connect(self._on_next)
        foot.addWidget(self._btn_back)
        foot.addWidget(self._btn_next)
        lay.addLayout(foot)

        self._goto_page(0)
        # 等底部按钮都建好再校验一次路径（_on_path_changed 会按结果禁用「下一步」）
        self._on_path_changed(self._target)
        return main

    # ---------- 三个页面 ----------
    def _page_dest(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self._px(12))

        card_frame, cl = card(self._scale, spacing=10)
        cl.addWidget(caption("安装到"))
        self._path_edit = QLineEdit(self._target)
        self._path_edit.setMinimumHeight(self._px(36))
        self._path_edit.textChanged.connect(self._on_path_changed)
        cl.addWidget(self._path_edit)

        row = QHBoxLayout()
        row.setSpacing(self._px(8))
        self._btn_browse = AnimatedButton("选择文件夹…", self._scale, kind="chip")
        self._btn_browse.clicked.connect(self._on_browse)
        row.addWidget(self._btn_browse)
        self._btn_default = AnimatedButton("恢复默认", self._scale, kind="chip")
        self._btn_default.clicked.connect(
            lambda: self._path_edit.setText(default_dir()))
        row.addWidget(self._btn_default)
        row.addStretch(1)
        cl.addLayout(row)

        self._path_hint = QLabel("")
        self._path_hint.setObjectName("hint")
        self._path_hint.setWordWrap(True)
        cl.addWidget(self._path_hint)
        lay.addWidget(card_frame)

        tip, tl = card(self._scale, spacing=6)
        tl.addWidget(caption("安装的是什么"))
        body = QLabel(
            "StoneEye：框选屏幕任意一块，自动识别画面里的景别与运镜并贴回原处，"
            "跑在托盘里，用 %s 随时唤起。" % "Ctrl+Alt+Q")
        body.setObjectName("field")
        body.setWordWrap(True)
        tl.addWidget(body)
        note = QLabel("安装到当前用户目录，不需要管理员权限；卸载从「应用和功能」里走。")
        note.setObjectName("hint")
        note.setWordWrap(True)
        tl.addWidget(note)
        lay.addWidget(tip)
        lay.addStretch(1)
        note = QLabel("装好之后在托盘图标上右键就能打开控制面板，所有设置都在里面。")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        return page

    def _page_tasks(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self._px(12))

        frame, cl = card(self._scale, spacing=2)
        cl.addWidget(caption("快捷方式"))
        self._sw_desktop = SwitchRow("桌面快捷方式", self._scale,
                                     "双击就能开面板，省的记快捷键")
        self._sw_desktop.setChecked(True)
        cl.addWidget(self._sw_desktop)
        self._sw_start = SwitchRow("开始菜单", self._scale,
                                   "在开始菜单里建一个入口，方便搜索和固定")
        self._sw_start.setChecked(True)
        cl.addWidget(self._sw_start)
        lay.addWidget(frame)

        frame2, cl2 = card(self._scale, spacing=2)
        cl2.addWidget(caption("启动"))
        self._sw_auto = SwitchRow("开机自动运行", self._scale,
                                  "登录后自动进托盘，框选热键随叫随到")
        self._sw_auto.setChecked(False)
        cl2.addWidget(self._sw_auto)
        lay.addWidget(frame2)
        lay.addStretch(1)
        note = QLabel("不确定就保持默认：快捷方式重装一次就能补，开机自启随时可以关。")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        return page

    def _page_install(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(self._px(10))
        # 2:3 上下留白：进度块略高于正中，比死贴顶部耐看
        lay.addStretch(2)

        self._big = QLabel("正在解压文件…")
        self._big.setObjectName("big")
        lay.addWidget(self._big)
        lay.addSpacing(self._px(4))
        self._bar = Bar(self._scale)
        lay.addWidget(self._bar)
        lay.addSpacing(self._px(8))
        self._detail = QLabel("")
        self._detail.setObjectName("hint")
        lay.addWidget(self._detail)
        lay.addSpacing(self._px(18))

        self._done_row = QWidget()
        dr = QHBoxLayout(self._done_row)
        dr.setContentsMargins(0, 0, 0, 0)
        dr.setSpacing(self._px(8))
        self._btn_run = AnimatedButton("立即打开", self._scale, kind="chip", primary=True)
        self._btn_run.clicked.connect(self._launch_app)
        self._btn_open = AnimatedButton("打开安装目录", self._scale, kind="chip")
        self._btn_open.clicked.connect(self._open_dir)
        dr.addWidget(self._btn_run)
        dr.addWidget(self._btn_open)
        dr.addStretch(1)
        self._done_row.setVisible(False)
        lay.addWidget(self._done_row)
        lay.addStretch(3)
        self._install_note = QLabel(
            "装好约 331 MB。之后每次框选都要调用 OCR 模型，第一次会慢一点。")
        self._install_note.setObjectName("hint")
        self._install_note.setWordWrap(True)
        lay.addWidget(self._install_note)
        return page

    # ---------- 状态流转 ----------
    def _goto_page(self, index: int):
        self._index = index
        self._stack.setCurrentIndex(index)
        titles = (
            ("装到这台电脑上", "选个位置，剩下的交给它"),
            ("顺手建几个入口", "都可以以后再改"),
            ("", ""),
        )
        if index < 2:
            self._title.setText(titles[index][0])
            self._subtitle.setText(titles[index][1])
        else:
            self._title.setText("正在安装")
            self._subtitle.setText(self._target)
        for i, item in enumerate(self._steps):
            item.set_state("active" if i == index else ("done" if i < index else "pending"))
        self._btn_back.setVisible(index == 1 and not self._installing)
        self._btn_next.setText("下一步" if index == 0 else "开始安装")
        self._btn_next.setVisible(index < 2)
        self._btn_cancel.setVisible(not self._finished)

    def _on_path_changed(self, text: str):
        self._target = text.strip() or default_dir()
        path = self._target
        parent = os.path.dirname(path.rstrip("\\/")) or path
        problem = ""
        if os.path.exists(path) and not os.path.isdir(path):
            problem = "这个路径是个文件，换个目录。"
        else:
            try:
                free = shutil.disk_usage(parent if os.path.exists(parent) else os.path.splitdrive(path)[0] + "\\").free
                need = self._info.get("bytes", 0) * 1.15
                if free < need:
                    problem = "磁盘剩余 %s，装不下（需要约 %s）。" % (
                        human(free), human(need))
            except Exception:
                free = None
        self._path_hint.setText(problem or "安装到该目录，不会动其它文件。")
        self._path_hint.setStyleSheet(
            "color:%s;" % ("#E06C64" if problem else TEXT_3))
        if getattr(self, "_btn_next", None) is not None:
            self._btn_next.setEnabled(not problem)

    def _on_browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选择安装位置", self._target,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks)
        if chosen:
            chosen = chosen.rstrip("\\/")
            if os.path.basename(chosen).lower() != APP_ID.lower():
                chosen = os.path.join(chosen, APP_ID)
            self._path_edit.setText(chosen)

    def _on_back(self):
        if self._index > 0 and not self._installing:
            self._goto_page(self._index - 1)

    def _on_next(self):
        if self._installing:
            return
        if self._index == 0:
            self._goto_page(1)
            return
        self._start_install()

    def _on_cancel(self):
        if self._installing:
            if self._worker is not None:
                self._worker.cancel()
            return
        self.close()

    # ---------- 安装 ----------
    def _start_install(self):
        self._installing = True
        self._goto_page(2)
        self._title.setText("正在安装")
        self._subtitle.setText(self._target)
        self._btn_back.setVisible(False)
        self._btn_next.setVisible(False)
        self._btn_cancel.setText("中止")
        self._big.setText("正在解压文件…")
        self._bar.set_value(0)

        if app_running():
            kill_app()

        self._worker = InstallWorker(self._target, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(self._on_installed)
        self._worker.start()

    def _on_progress(self, done: int, total: int, name: str):
        ratio = (done / total) if total else 0.0
        self._bar.set_value(ratio)
        # 大字说阶段，细节行说文件与百分比，免得和顶部标题重复念「正在安装」
        self._big.setText("正在收尾…" if ratio >= 0.999 else "正在解压文件…")
        if name:
            short = name if len(name) < 58 else "…" + name[-56:]
            self._detail.setText("%s    %d%%" % (short, int(ratio * 100)))
        else:
            self._detail.setText("100%")

    def _on_failed(self, message: str):
        self._installing = False
        self._bar.set_value(0)
        self._big.setText("没装上")
        self._title.setText("安装失败")
        self._detail.setText(message)
        self._btn_cancel.setText("关闭")
        self._btn_next.setText("重试")
        self._btn_next.setVisible(True)
        self._btn_next.clicked.disconnect()
        self._btn_next.clicked.connect(self._retry)

    def _retry(self):
        self._btn_next.clicked.disconnect()
        self._btn_next.clicked.connect(self._on_next)
        self._start_install()

    def _on_installed(self):
        self._installing = False
        self._finished = True
        self._bar.set_value(1.0)
        self._detail.setText("")

        problem = finalize_install(
            self._target, str(self._info.get("version", __version__)),
            self._sw_desktop.isChecked(), self._sw_start.isChecked(),
            self._sw_auto.isChecked())

        self._steps[2].set_state("done")
        self._title.setText("安装完成")
        self._big.setText("装好了")
        self._detail.setText("装在 %s" % self._target)
        if problem:
            self._detail.setText("文件已装好，但写快捷方式 / 注册表时出错：%s" % problem)
        self._install_note.setText(
            "从「设置 → 应用 → 已安装的应用」里可以卸载，卸载程序就在安装目录里。")
        self._done_row.setVisible(True)
        self._btn_cancel.setText("关闭")
        self._btn_next.setText("完成")
        self._btn_next.setVisible(True)
        try:
            self._btn_next.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._btn_next.clicked.connect(self.close)

    def _launch_app(self):
        exe = os.path.join(self._target, EXE_NAME)
        try:
            subprocess.Popen([exe], cwd=self._target, close_fds=True,
                             creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
        self.close()

    def _open_dir(self):
        try:
            os.startfile(self._target)     # noqa: S606 - 就是要用系统关联打开
        except Exception:
            pass

    def closeEvent(self, event):
        if self._installing and self._worker is not None:
            self._worker.cancel()
            self._worker.wait(1500)
        self.finish_slide_in()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._enter_done:
            self._enter_done = True
            QTimer.singleShot(0, self.slide_in)


# ---------------------------------------------------------------- 卸载窗口
def parse_flags(argv) -> dict:
    opts = dict(target=None, desktop=True, startmenu=True, autostart=False,
                silent=False, quiet=False)
    for i, arg in enumerate(argv):
        if arg == "--dir" and i + 1 < len(argv):
            opts["target"] = argv[i + 1]
        elif arg == "--no-desktop":
            opts["desktop"] = False
        elif arg == "--no-startmenu":
            opts["startmenu"] = False
        elif arg == "--autostart":
            opts["autostart"] = True
        elif arg == "--silent":
            opts["silent"] = True
        elif arg == "--quiet":
            opts["quiet"] = True
    return opts


def _log_path() -> str:
    base = os.path.dirname(os.path.abspath(sys.executable)) if getattr(
        sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "install.log")


def _log(text: str):
    """静默模式没有控制台，把结果写进 install.log（部署脚本看退出码即可）。"""
    try:
        with open(_log_path(), "a", encoding="utf-8") as fp:
            fp.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), text))
    except Exception:
        pass


def run_install_silent(opts) -> int:
    target = opts["target"] or default_dir()
    _log("静默安装 -> %s" % target)
    try:
        if app_running():
            kill_app()
        extract_payload(target)
        problem = finalize_install(target, str(payload_info().get("version", __version__)),
                                   opts["desktop"], opts["startmenu"], opts["autostart"])
    except Exception as exc:      # noqa: BLE001
        _log("失败：%s: %s" % (type(exc).__name__, exc))
        return 1
    _log("完成" + ("（警告：%s）" % problem if problem else ""))
    return 0 if not problem else 2


def run_setup(argv):
    app = QApplication(argv)
    app.setApplicationName(APP_ID)
    app.setQuitOnLastWindowClosed(True)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "StoneEye.Setup")
        except Exception:
            pass
    app.setWindowIcon(app_icon())
    win = SetupWindow()
    win.show()
    return app.exec()


def main() -> int:
    argv = sys.argv[:]
    opts = parse_flags(argv)
    if "--uninstall" in argv:
        from app.winenv import run_uninstall
        return run_uninstall(argv)
    if opts["silent"]:
        return run_install_silent(opts)
    return run_setup(argv)


if __name__ == "__main__":
    sys.exit(main())
