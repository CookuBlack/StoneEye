# -*- coding: utf-8 -*-
"""托盘与生命周期（V3.2 快照版）。

框选一次 -> 把那一刻的画面冻结成一张置顶贴图 -> 后台逐句识别
          -> 标注叠加在贴图上 -> 切屏/切换窗口都不影响内容 -> 直到双击关闭。
需要监控变化时才在右键切成 live 实时刷新。
"""
import importlib.util
import time

from PySide6.QtCore import Qt, QObject, Slot, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QMessageBox

from .assets import app_icon
from .config_manager import ConfigManager
from .coords import ui_scale
from .hotkey_manager import HotkeyManager
from .region_selector import RegionSelector
from .screen_capture import capture
from .capture_worker import make_frame
from .screenshot_editor import (
    ScreenshotEditor, capture_desktop, pixmap_to_pil,
)
from .recognition_service import RecognitionService
from .window_manager import WindowManager
from .live_window import MENU_QSS, normalize_mode

REQUIRED_MODULES = ("mss", "PIL", "numpy", "rapidocr_onnxruntime", "keyboard")


def _make_icon() -> QIcon:
    """托盘 / 任务栏图标统一取 assets/app.ico（由 tools/icon_from_image.py 生成）。

    图标缺失时 app.assets 会临时画一个兜底，托盘不会出现空白格。
    """
    return app_icon()


class AppController(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.config_mgr = ConfigManager()
        self.cfg = {}
        self.service = None
        self.manager = None

        self.hotkeys = HotkeyManager(parent=self)
        # 热键回调来自 keyboard 的监听线程，必须排队回主线程
        self.hotkeys.triggered.connect(self.start_selection, Qt.QueuedConnection)
        self.hotkeys.status_changed.connect(self._on_hotkey_status)

        self._selecting = False
        self._tray = None
        self._menu = None
        self._act_select = None
        self._act_close_all = None
        self._last_error_at = 0.0
        self.panel = None
        self._save_timer = None

    # ---------- 启动 ----------
    def start(self):
        self.config_mgr.ensure_exists()
        try:
            self.cfg = self.config_mgr.load()
        except Exception as exc:
            QMessageBox.critical(
                None, "配置错误",
                f"{self.config_mgr.path}\n\n{exc}\n\n将使用内置默认配置启动。",
            )
            self.cfg = ConfigManager.defaults()

        self.service = RecognitionService(self.cfg, self)
        self.manager = WindowManager(self.service, self.cfg, self)
        self.manager.reedit_handler = self.reedit_window
        self.service.worker_error.connect(self._on_worker_error)

        self._build_tray()
        self._tray.show()
        self.service.start()          # 后台预热模型 + 启动 OCR 线程
        self.hotkeys.start()
        self._check_environment()
        mode_note = ("画面定格为快照，切屏不变" if self.cfg["live"]["capture_mode"] == "snapshot"
                     else "实时跟随屏幕区域刷新")
        self._tray.showMessage(
            "StoneEye 已就绪",
            f"按 {self.cfg['hotkey']} 框选一块区域，可用工具条标注 / 打码 / 提取文字 · "
            f"{mode_note} · 完成后仍会生成置顶贴图并标景别/运镜",
            QSystemTrayIcon.Information, 4500,
        )

    def _build_tray(self):
        self._tray = QSystemTrayIcon(_make_icon(), self.app)
        self._tray.setToolTip("StoneEye · 框选屏幕，标出景别与运镜")

        self._menu = QMenu()          # 必须自己持有引用，setContextMenu 不接管所有权
        self._menu.setStyleSheet(MENU_QSS)
        self._act_select = self._menu.addAction(
            f"截图并标注\t{self.cfg['hotkey']}"
        )
        self._act_select.triggered.connect(self.start_selection)
        self._menu.addAction("快速贴图（跳过标注）", self.start_quick_selection)
        self._menu.addAction("控制面板", self.open_panel)
        self._menu.addSeparator()
        self._menu.addAction("重新加载配置", self.reload_config)
        self._menu.addAction("打开配置文件", self.open_config)
        self._menu.addSeparator()
        self._act_close_all = self._menu.addAction("关闭全部贴图", self.close_all)
        self._menu.addSeparator()
        self._menu.addAction("退出", self.quit_app)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)
        self.manager.count_changed.connect(self._on_count_changed)
        self._on_count_changed(self.manager.count())

    def _check_environment(self):
        missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
        if missing:
            self._notify(
                "缺少依赖",
                "请执行 pip install -r requirements.txt\n\n缺失："
                + ", ".join(missing), 8000,
            )
        scale = ui_scale()
        if abs(scale - 1.0) > 0.01:
            self._notify(
                "DPI 换算已启用",
                f"检测到显示缩放 {scale:.2f}x，坐标已自动换算。", 5000,
            )

    # ---------- 主流程 ----------
    @Slot()
    def start_selection(self):
        """截图 -> 标注编辑态 -> 完成。默认路径，工具栏 16 个功能都在这里。"""
        self._do_selection(False)

    @Slot()
    def start_quick_selection(self):
        """老路径：框完直接生成置顶贴图，不进标注编辑态。"""
        self._do_selection(True)

    def _do_selection(self, quick: bool):
        if self._selecting:
            return
        self._selecting = True
        rect, frame = None, None
        panel_was_visible = False
        note = None
        try:
            # 面板与旧贴图都是置顶窗口，不隐藏会被 mss 拍进新截图里
            if self.panel is not None and self.panel.isVisible():
                panel_was_visible = True
                self.panel.hide()
            self.manager.hide_all()
            try:
                # 纯手动拖拽框选（不再做窗口自动识别）
                rect = RegionSelector().select()   # 返回物理像素 Rect 或 None
            except Exception as exc:
                self._notify("框选失败", str(exc), 6000)
                return
            if rect is None:
                return

            if quick:
                image = capture(rect)
                if image.width != rect.w or image.height != rect.h:
                    self._notify("截图尺寸异常",
                                 f"选区 {rect.w}×{rect.h}，实得 "
                                 f"{image.width}×{image.height}。请检查显示缩放。",
                                 7000)
                frame = make_frame(-1, 1, image)
            else:
                # 整屏先冻结：编辑态里选区外是那一瞬间的画面压暗，
                # 而不是背后还在动的桌面。此刻所有贴图仍处于隐藏状态。
                screen_pm, full = capture_desktop()
                editor = ScreenshotEditor(screen_pm, full, rect, self.cfg)
                try:
                    outcome = editor.exec()
                except Exception as exc:
                    self._notify("标注编辑器出错", str(exc), 6000)
                    return
                finally:
                    editor.cleanup()
                action = outcome.get("action", "cancel")
                pm = outcome.get("pixmap")
                if action in ("done", "pin"):
                    if pm is not None and not pm.isNull():
                        frame = make_frame(-1, 1, pixmap_to_pil(pm))
                elif action == "save":
                    note = ("截图已保存", "文件：%s" % outcome.get("path", ""), 3500)
                elif action == "share":
                    note = ("已复制到剪贴板",
                            "去聊天窗口 Ctrl+V 粘贴即可发送", 3000)
        except Exception as exc:
            self._notify("截图失败", str(exc), 6000)
            return
        finally:
            # 必须等截图 / 编辑态都做完才恢复；取消 / 出错也要恢复
            self.manager.show_all()
            self._selecting = False
            if frame is None and note is None and panel_was_visible:
                self.panel.show()      # 取消框选：把面板还给用户

        if note is not None:
            self._notify(*note)
        if frame is None:
            return
        window = self.manager.create(rect, frame)
        if window.mode == "snapshot":
            self._notify(
                "截图贴图已生成",
                "画面已定格在框选那一刻；右键贴图可「在原图上继续标注」",
                3500,
            )
        else:
            self._notify(
                "实时区域已建立",
                "窗口已错开源区域并持续刷新；请把窗口留在源区域之外。", 3500,
            )

    def reedit_window(self, window):
        """贴图右键「在原图上继续标注」：编辑完写回同一张贴图，不新建窗口。

        底图取 window.pixmap（原画面 + 上一次手绘标注），不含运行时的
        OCR 高亮 —— 高亮是叠加层，烤进底图会导致下次识别重复描一遍。
        """
        if (self._selecting or window is None
                or getattr(window, "_closed_emitted", False)):
            return
        self._selecting = True
        editor = None
        try:
            self.manager.hide_all()
            base = window.pixmap.copy()
            if base.isNull():
                return
            editor = ScreenshotEditor.for_image(base, self.cfg)
            self._notify("继续标注",
                         "在已有标注上继续画；完成后会写回这张贴图", 2600)
            outcome = editor.exec()
            action = (outcome or {}).get("action", "cancel")
            pm = (outcome or {}).get("pixmap")
            if action == "cancel" or pm is None or pm.isNull():
                return
            frame = make_frame(window.window_id, window.frame_id + 1,
                               pixmap_to_pil(pm))
            window.update_frame(frame, allow_frozen=True)
            window.request_recognize(quiet=True)
            self._notify("标注已写回贴图",
                         "在已有标注上继续画完了；右键还能再编辑", 3200)
        except Exception as exc:
            self._notify("二次标注失败", str(exc), 6000)
        finally:
            if editor is not None:
                editor.cleanup()
            self.manager.show_all()
            self._selecting = False

    # ---------- 托盘动作 ----------
    def reload_config(self):
        try:
            cfg = self.config_mgr.load()
        except Exception as exc:
            self._notify("配置加载失败", str(exc), 6000)
            return
        self.cfg = cfg
        self.service.update_config(cfg)
        self.manager.cfg = cfg
        self.manager.request_recognize_all(cfg)   # 新词表立刻对所有窗口生效
        self.hotkeys.reload(cfg["hotkey"])
        if self._act_select is not None:
            self._act_select.setText(f"截图并标注\t{cfg['hotkey']}")
        if self.panel is not None:
            self.panel.sync_from_config()
        mode = "快照（画面定格）" if cfg["live"]["capture_mode"] == "snapshot" else "实时刷新"
        self._notify(
            "配置已重新加载",
            f"{len(cfg['rules'])} 条规则 · 采集模式 {mode} · 状态条 {cfg['live']['status_corner']}"
            f" · 已用新词表重识别 {self.manager.count()} 个窗口", 3500,
        )

    # ---------- 控制面板 ----------
    def open_panel(self):
        from .control_panel import ControlPanel    # 延迟导入，缩短启动路径
        if self.panel is None:
            self.panel = ControlPanel(self)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def recognize_all(self):
        if self.manager.count() <= 0:
            self._notify("还没有贴图", f"先按 {self.cfg['hotkey']} 框选一块区域", 3000)
            return
        self.manager.request_recognize_all(self.cfg)
        self._notify("已提交重新识别", f"{self.manager.count()} 个窗口已入队", 2500)

    def apply_panel_patch(self, patch, reocr=False):
        """控制面板改一项参数：合并 -> 热应用 -> 防抖落盘。"""
        patch = patch or {}
        for section, values in patch.items():
            target = self.cfg.setdefault(section, {})
            if isinstance(values, dict) and isinstance(target, dict):
                target.update(values)
            else:
                self.cfg[section] = values

        self.service.update_config(self.cfg)
        # 只刷新窗口的显示/采集参数，不重跑 OCR（拖滑块不该引发全量识别）
        self.manager.apply_config_all(self.cfg)

        mode = (patch.get("live") or {}).get("capture_mode")
        if mode:
            self.manager.set_mode_all(normalize_mode(mode))
        if reocr:
            self.manager.request_recognize_all(self.cfg)

        self._schedule_save()

    def _schedule_save(self):
        """防抖写盘：拖滑块时会连续触发几十次，合并成一次文件写入。"""
        if self._save_timer is None:
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.setInterval(700)
            self._save_timer.timeout.connect(self.save_config_now)
        self._save_timer.start()

    def save_config_now(self):
        try:
            self.config_mgr.write(self.cfg)
        except Exception as exc:
            self._notify("保存配置失败", str(exc), 5000)

    def open_config(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.config_mgr.path))

    def close_all(self):
        self.manager.close_all()

    def quit_app(self):
        self.shutdown()
        self.app.quit()

    def shutdown(self):
        self.hotkeys.stop()
        if self.panel is not None:
            self.panel.hide()
        if self.manager is not None:
            self.manager.close_all()
        if self.service is not None:
            self.service.shutdown()
        if self._tray is not None:
            self._tray.hide()

    # ---------- 辅助 ----------
    def _on_worker_error(self, _wid, message):
        now = time.monotonic()
        if now - self._last_error_at < 5:   # 限频，避免采集失败时刷屏
            return
        self._last_error_at = now
        self._notify("采集 / 识别提示", str(message), 6000)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.open_panel()

    def _on_count_changed(self, count):
        if self._act_close_all is not None:
            self._act_close_all.setText(f"关闭全部贴图 ({count})")
            self._act_close_all.setEnabled(count > 0)

    def _on_hotkey_status(self, message):
        if "失败" in message or "不可用" in message:
            self._notify("热键提示", message, 6000)

    def _notify(self, title, message, msecs=4000):
        if self._tray is not None:
            self._tray.showMessage(
                title, str(message), QSystemTrayIcon.Information, msecs
            )
