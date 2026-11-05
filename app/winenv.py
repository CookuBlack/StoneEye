# -*- coding: utf-8 -*-
"""Windows 安装 / 卸载相关的公共动作（V4.6）。

为什么放在 app 里而不是 installer 里：卸载界面是**应用本体**提供的
（`StoneEye.exe --uninstall`），这样装完不用再多塞一个 47 MB 的
独立卸载器；安装程序和卸载共用同一份路径 / 注册表 / 快捷方式代码，
也不会出现"装的位置和卸的位置对不上"这种低级错误。
"""
import base64
import os
import subprocess
import sys
import time
import winreg

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QVBoxLayout,
)

from .assets import app_icon
from .ui_shell import ShellWindow
from .ui_theme import PANEL_QSS, scaled_qss
from .ui_widgets import AnimatedButton

APP_NAME = "StoneEye"
APP_ID = "StoneEye"
EXE_NAME = "StoneEye.exe"
PUBLISHER = "杨载工坊"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\%s" % APP_ID

CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------- 路径
def default_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", APP_ID)


def desktop_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Desktop")


def start_menu_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f GB" % n


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- 快捷方式
def _ps_quote(text: str) -> str:
    return str(text).replace("'", "''")


def make_shortcut(lnk: str, target: str, workdir: str, icon: str, desc: str = ""):
    """建 .lnk。走 PowerShell 的 WScript.Shell，比 ctypes 调 IShellLink 短得多。

    用 -EncodedCommand（UTF-16LE + base64）而不是 -Command：中文路径和引号
    都不用操心转义。
    """
    script = (
        "$s=(New-Object -COM WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';"
        "$s.WorkingDirectory='%s';"
        "$s.IconLocation='%s';"
        "$s.Description='%s';"
        "$s.Save()" % (_ps_quote(lnk), _ps_quote(target), _ps_quote(workdir),
                       _ps_quote(icon), _ps_quote(desc))
    )
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                   creationflags=CREATE_NO_WINDOW, timeout=30)


# ---------------------------------------------------------------- 注册表
def set_reg(key_path: str, name: str, value, kind=None):
    kind = kind or winreg.REG_SZ
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


def del_reg_tree(key_path: str):
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        return
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                except OSError:
                    break
                del_reg_tree(key_path + "\\" + sub)
    except OSError:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except OSError:
        pass


def read_reg(key_path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def install_location() -> str:
    loc = read_reg(UNINSTALL_KEY, "InstallLocation")
    if isinstance(loc, str) and loc:
        return loc
    return default_dir()


# ---------------------------------------------------------------- 进程
def app_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % EXE_NAME,
                              "/NH"], capture_output=True, text=True,
                             cwd=os.environ.get("TEMP") or os.path.expanduser("~"),
                         creationflags=CREATE_NO_WINDOW).stdout
        return EXE_NAME.lower() in out.lower()
    except Exception:
        return False


def kill_app():
    """结束正在跑的程序，但**必须排除自己**。

    卸载就是 StoneEye.exe --uninstall 在跑：按映像名一刀切会把
    卸载器自己砍掉，结果目录一个文件都没删、退出码还是 1。
    """
    try:
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME,
                        "/FI", "PID ne %d" % os.getpid()],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def schedule_self_delete(target: str):
    """卸载完把自己（和目录）也删掉：等 2 秒 -> 删 exe -> 删目录。

    Windows 不允许删除正在运行的 exe，只能交给 cmd 等进程退出后再动手。
    """
    if not getattr(sys, "frozen", False):
        return
    me = os.path.abspath(sys.executable)
    if os.path.dirname(me).lower() != os.path.abspath(target).lower():
        return
    # 三件事：等进程退出 -> 换到别的工作目录 -> 删 exe 再删目录（失败再等一轮）。
    # 不换目录会留下一个空壳：cmd 的当前目录如果是安装目录，rmdir 永远删不掉它。
    cmd = ('ping -n 3 127.0.0.1 >nul & cd /d "%%TEMP%%" & del /f /q "%s" & '
           'rmdir /s /q "%s" & if exist "%s" (ping -n 3 127.0.0.1 >nul & '
           'rmdir /s /q "%s")') % (me, target, target, target)
    try:
        subprocess.Popen(cmd, shell=True,
                         cwd=os.environ.get("TEMP") or os.path.expanduser("~"),
                         creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def remove_install(target: str) -> None:
    """反着来：删快捷方式 -> 删注册表 -> 杀进程 -> 删文件。"""
    for lnk in (os.path.join(desktop_dir(), APP_NAME + ".lnk"),
                os.path.join(start_menu_dir(), APP_NAME + ".lnk")):
        try:
            os.remove(lnk)
        except OSError:
            pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_ID)
    except OSError:
        pass
    del_reg_tree(UNINSTALL_KEY)
    kill_app()
    import shutil
    shutil.rmtree(target, ignore_errors=True)


# ---------------------------------------------------------------- 卸载界面
class UninstallWindow(ShellWindow):
    """一个确认框 + 一个进度提示。和安装程序同一套底板，关掉就没了。"""

    def __init__(self, target: str):
        super().__init__()
        self._target = target
        self._busy = False

        self.setObjectName("root")
        extra = """
QWidget#root { font-family: "Microsoft YaHei UI","Segoe UI"; font-size: 13px;
               color: #EFEFEF; }
QLabel#wiztitle { font-size: 19px; font-weight: 600; color: #F6F6F6; }
QLabel#hint { font-size: 12px; color: #6F6F6F; background: transparent; }
"""
        self.setStyleSheet(scaled_qss(PANEL_QSS, self._scale) + scaled_qss(extra, self._scale))
        self.setWindowTitle("卸载 %s" % APP_NAME)
        self.setWindowIcon(app_icon())
        self.setFixedSize(self._px(560) + self._spad * 2,
                          self._px(300) + self._spad * 2)
        self._build()
        self.center_on_screen()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._spad + self._px(26), self._spad + self._px(24),
                                 self._spad + self._px(26), self._spad + self._px(20))
        outer.setSpacing(self._px(10))

        self._title = QLabel("卸载 %s" % APP_NAME)
        self._title.setObjectName("wiztitle")
        outer.addWidget(self._title)
        self._hint = QLabel(
            "会删除程序文件、快捷方式和开机启动项。\n"
            "配置文件 config.json 会一起删掉 —— 里面的词表如果还要用，请先备份。")
        self._hint.setObjectName("hint")
        self._hint.setWordWrap(True)
        outer.addWidget(self._hint)
        self._path = QLabel(self._target)
        self._path.setObjectName("hint")
        self._path.setWordWrap(True)
        outer.addWidget(self._path)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(self._px(10))
        self._btn_cancel = AnimatedButton("取消", self._scale, kind="chip")
        self._btn_cancel.clicked.connect(self.close)
        self._btn_go = AnimatedButton("卸载", self._scale, kind="primary")
        self._btn_go.clicked.connect(self._start)
        row.addStretch(1)
        row.addWidget(self._btn_cancel)
        row.addWidget(self._btn_go)
        outer.addLayout(row)
        self.ensure_rim()

    def _start(self):
        if self._busy:
            return
        self._busy = True
        self._btn_go.setEnabled(False)
        self._btn_cancel.setEnabled(False)
        self._title.setText("正在卸载…")
        self._hint.setText("关闭正在运行的程序，删除文件与快捷方式。")
        QApplication.processEvents()
        try:
            remove_install(self._target)
        except Exception as exc:      # noqa: BLE001
            self._title.setText("没卸干净")
            self._hint.setText(str(exc))
            self._btn_cancel.setText("关闭")
            self._btn_cancel.setEnabled(True)
            self._btn_go.setVisible(False)
            return
        self._title.setText("已经卸干净了")
        self._hint.setText("程序文件已删除。这个窗口关掉后就没了。")
        self._btn_cancel.setText("关闭")
        self._btn_cancel.setEnabled(True)
        self._btn_go.setVisible(False)
        schedule_self_delete(self._target)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._enter_done:
            self._enter_done = True
            QTimer.singleShot(0, self.slide_in)


def run_uninstall(argv) -> int:
    target = install_location()
    for i, arg in enumerate(argv):
        if arg == "--dir" and i + 1 < len(argv):
            target = argv[i + 1]
    if "--quiet" in argv:
        try:
            remove_install(target)
        except Exception:
            return 1
        schedule_self_delete(target)
        return 0
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(True)
    win = UninstallWindow(target)
    win.show()
    return app.exec()
