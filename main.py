# -*- coding: utf-8 -*-
"""
StoneEye —— Windows 屏幕框选 OCR 整句标注 + 截图贴图工具（V4.7）
启动入口。

【交互模型】
框选一次 -> 把那一刻的画面冻结成一张无边框置顶贴图（默认 snapshot）
框选是纯粹的**按住左键拖动**：松开确认，Esc / 右键取消。
  不做窗口自动识别（V3.7 的预测框实测不可用，已整体移除），
  鼠标按下即开始画框，没有长按等待之类的中间态。
选定后贴图会浮现并闪一下描边，原位粘贴也能一眼看出这块已经被截下来。
控制面板可以改识别规则（类别 / 关键词 / 颜色），改完立即用新词表重识别。
          -> 后台按标点切句识别（景别 / 运镜）-> 命中的「整句」配上颜色
          -> 切屏 / 切换窗口都不会改变贴图内容 -> 直到双击关闭。
标注单位默认是「逗号/句号/分号/破折号切出来的短句」，框的左右边界严格贴着标点；
整句框用细描边 + 淡填充圈定范围，命中的词另加实线下划线，一眼能看出是「这句在讲运镜」
而不是「整句都是运镜」。live.highlight_mode 改成 word 可退回只框关键词。
裸运镜动词（环绕 / 推进 / 手持…）带语境校验：句中没有镜头语境、左侧又是实体名词时
判为物体自身动作（如「橙色环形坐凳环绕草坪」），不标注；面板「语义校验」可关闭。
识别进度显示在贴图顶部 3px 进度条 + 右下角状态条（排队中 / 3/8 · 62% / 已识别 x.xs）。
右键可「重新截取源区域」更新贴图，或切成 live 实时刷新模式。
托盘菜单「控制面板」（或双击托盘图标）打开参数面板。
面板为无边框深色两栏窗（V4.4，默认约占屏幕 78%x86%，最小 900x660 逻辑像素）：
左栏是操作 + 分页导航，右栏是当前页内容。它是普通顶层窗口，任务栏有按钮、
Alt+Tab 能切到、也能最小化到任务栏 —— 不能再用 Qt.Tool，那个标志会让人以为程序消失了。
首页给运行指标和最近一次识别；「显示」管贴图上出现什么，状态条位置直接在
贴图示意里点四个角；「采集与识别」管模式和精度取舍；「高级参数」放六个滑块。
所有改动即时生效并自动落盘，位置大小会记住；Ctrl+N / Ctrl+R / Ctrl+E / Esc 可用。
左侧「识别规则」是面板里的一页（V4.3 起不再另开窗口），可编辑类别、关键词和颜色，
保存后立即用新词表重识别；正在编辑时切到别的页再切回来，内容还在。

【动效】（V4.4）
面板里的"丝滑"全部自己实现，因为 QSS 没有 transition：
  - 切页时新页面 200ms 淡入（QGraphicsOpacityEffect，结束即摘掉，不留常驻特效）；
  - 侧栏选中底色是一条会滑动的胶囊（240ms OutCubic），不再"这边灭那边亮"；
  - 分段控件的选中块照样滑动（220ms），轨道与高亮自绘；
  - 开关的轨道/圆点颜色按 0→1 进度插值，中途圆点还会微微膨胀；
  - 主按钮与首页胶囊按钮自绘，悬停/按下时底色与文字色做 140ms 补间。
动画只依赖"事件循环 + 真实时间"，所以所有过渡都写了兜底（400ms 后强制收尾），
被系统掐掉动画也只会退化成"直接呈现"，不会留下半透明或错位的控件。
框选与重截前会临时隐藏所有贴图，避免把它们拍进新截图。
采集（每窗口轻量线程，仅 live 模式）与识别（全局单线程）解耦。

【性能要点】
  1. mss 实例按线程缓存，不再每次截图重建抓屏上下文；
  2. 采集线程只在画面真正变化时才发帧，主线程不再每 100ms 重建整幅 QPixmap；
  3. 静止画面自动降采集频率（live.adaptive_capture），回桌面待机几乎不占 CPU；
  4. 进度条只重绘顶部/底部窄带，不重绘整幅贴图；
  5. 识别结果与上次完全相同时跳过重绘；
  6. 单帧分块数上限 ocr.tile_max，超大区域不会切出十几块拖慢整帧；
  7. OCR 走自定义快路径（V3.9）：检测按原像素跑（不再把最小边拉到 736 放大
     15 倍）、关掉方向分类、识别头高度按行高自适应。同一张 935x193 文本贴图
     从 5.5s 降到 0.5s；输入哈希命中缓存时 <2ms。

【坐标约定 / DPI 策略】
全流程统一使用「屏幕物理像素」：
  1. Win32 SetProcessDpiAwareness(2) 开启 Per-Monitor V2，
     保证 mss 截到的是未经系统缩放模糊化的真实像素；
  2. QT_ENABLE_HIGHDPI_SCALING=0 关闭 Qt 的 high-DPI 逻辑缩放，
     使 Qt 逻辑坐标与物理像素 1:1。
     注意：Qt 6 的 QT_SCALE_FACTOR 是与系统 DPR「相乘」的倍率，
     设成 1 并不能关掉缩放，不能用它替代本开关；
  3. app.coords 再做一层运行时换算兜底，万一某些 Qt 版本忽略开关，
     仍会按虚拟桌面「物理宽度 / 逻辑宽度」的比例换算坐标；
  4. 所有 QPixmap 的 devicePixelRatio 恒为 1。

于是 mss 截图、OCR 坐标、Qt 窗口 move()/resize() 三者对齐，
100% / 150% DPI 下高亮都不会错位。

【打包】
用项目根目录的 build.bat（默认 onedir，`build.bat onefile` 出单文件）：
  - 图标来自 assets/app.ico（tools/icon_from_image.py 生成），exe、任务栏、托盘、侧栏共用；
  - conda 环境要额外带上 Library\bin 里的 ffi.dll 等 DLL，否则运行时报
    "DLL load failed while importing _ctypes"；
  - 打包后跑 `StoneEye.exe --selftest` 可以验证模型与 onnxruntime 是否完好，
    结果写进同目录 selftest.log。
"""
import os
import sys
import traceback


# ---------- 必须在 import PySide6 之前执行 ----------
def _setup_dpi():
    if sys.platform != "win32":
        return
    import ctypes
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE_V2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_setup_dpi()
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
# ---------------------------------------------------

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon  # noqa: E402

from app.config_manager import app_dir  # noqa: E402


def _install_excepthook():
    """-w 打包后没有控制台，把未捕获异常落到 error.log 便于排查。"""
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc))
        try:
            with open(os.path.join(app_dir(), "error.log"), "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 70 + "\n" + text)
        except Exception:
            pass
        try:
            sys.stderr.write(text)
        except Exception:
            pass

    sys.excepthook = hook


def _selftest() -> int:
    """打包后自检：造一张中文图片跑一遍 OCR，结果写进 selftest.log。

    打包成 -w（无控制台）后，最怕的是「能启动、但一识别就废」——
    模型没打进去、onnxruntime 的 DLL 缺一个，界面上一点提示都没有。
    这个入口把整条链路（PIL 画字 -> onnxruntime -> 模型 -> 返回结果）
    跑一遍并落盘，用来区分「根本起不来」和「起得来但认不出」：

        StoneEye.exe --selftest
    """
    import time

    started = time.time()
    lines = []

    def log(msg):
        lines.append(msg)
        try:
            sys.stderr.write(str(msg) + "\n")
        except Exception:
            pass

    ok = False
    try:
        from PIL import Image, ImageDraw, ImageFont

        from app.config_manager import ConfigManager
        from app.ocr_engine import OcrEngine

        cfg = ConfigManager().load()
        sample = "镜头缓慢向前推进，全景。随后切到大特写。"
        img = Image.new("RGB", (900, 120), "white")
        draw = ImageDraw.Draw(img)
        font = None
        for path in (r"C:\Windows\Fonts\msyh.ttc",
                     r"C:\Windows\Fonts\simhei.ttf"):
            if os.path.exists(path):
                font = ImageFont.truetype(path, 26)
                break
        draw.text((24, 40), sample, fill="black",
                  font=font or ImageFont.load_default())
        try:
            img.save(os.path.join(app_dir(), "selftest.png"))
        except Exception:
            pass

        engine = OcrEngine(cfg.get("ocr"))
        result = engine.ocr(img)
        joined = "".join(str(item.get("text", "")) for item in result)
        log(f"识别行数: {len(result)}")
        log(f"识别文本: {joined}")
        log(f"模型错误: {getattr(engine, 'error', '') or '无'}")
        ok = len(result) > 0 and ("推进" in joined or "全景" in joined)
    except Exception:
        log(traceback.format_exc())

    log(f"耗时: {time.time() - started:.2f}s")
    log("结果: " + ("PASS" if ok else "FAIL"))
    try:
        with open(os.path.join(app_dir(), "selftest.log"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return 0 if ok else 2


def main() -> int:
    _install_excepthook()

    if "--selftest" in sys.argv:
        return _selftest()

    # 卸载入口：应用本体兼职卸载器，装完就只有一份 exe。
    # 必须赶在创建 QApplication 之前分流 —— 这里会自己建一个，
    # 而且不能顺带把托盘、热键、OCR 线程全拉起来。
    if "--uninstall" in sys.argv:
        from app.winenv import run_uninstall
        return run_uninstall(sys.argv)

    app = QApplication(sys.argv)
    app.setApplicationName("StoneEye")
    app.setApplicationDisplayName("StoneEye")
    app.setQuitOnLastWindowClosed(False)  # 关掉最后一张贴图也不能退出，托盘常驻

    # 任务栏图标：不走 AppUserModelID 的话，打包后任务栏认的是 python.exe，
    # 右键菜单会变成「Python」而且固定不到任务栏
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "StoneEye.App")
        except Exception:
            pass

    from app.assets import app_icon
    app.setWindowIcon(app_icon())

    # 关掉 Qt 的 high-DPI 缩放后，没写死字号的控件（托盘菜单、原始对话框）
    # 会被画成 1:1 物理像素，125% 屏上比系统里其它程序小一圈。
    # 用「像素字号」而不是「点字号」来放大，避免再被 DPI 乘一次。
    try:
        from app.coords import ui_zoom
        z = ui_zoom()
        if z > 1.01:
            f = app.font()
            f.setPixelSize(int(round(13 * z)))
            app.setFont(f)
    except Exception:
        pass

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(
            None, "系统托盘不可用",
            "当前会话没有可用的系统托盘，程序仍会启动，热键可用，但托盘图标可能不显示。",
        )

    from app.tray_app import AppController

    controller = AppController(app)
    controller.start()
    app.aboutToQuit.connect(controller.shutdown)

    # `StoneEye.exe --panel` 直接开控制面板。
    # 用途有两个：给快捷方式加参数就能一键开面板；打包后能绕开全局热键
    # （热键在非管理员下可能注册不上）验证面板在冻结环境里是否正常。
    if "--panel" in sys.argv:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(350, controller.open_panel)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
