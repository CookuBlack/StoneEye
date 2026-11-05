# StoneEye

框选屏幕任意一块 → OCR 认出画面里的「景别 / 运镜」→ 按句子切分、上色 → 把标注贴回原处并跟着走。
面向小说转漫画的分镜标注：看得懂「全景 / 中景 / 特写 / 推 / 拉 / 摇 / 移」，也能自己加词表。

当前版本 **4.7.0**。

---

## 功能

- **框选贴图**：拖一块区域就能贴一张实时跟随的标注层；位置、缩放、透明度都能调。
- **分类着色**：景别与运镜用不同颜色标注，同一句里命中的词一起亮。
- **整句切分**：按逗号、句号、分号、破折号断句，可切「整句标注 / 词级标注」。
- **快照 / 实时**：快照定格当时画面；实时模式按帧差决定要不要重新识别。
- **控制面板**：概览 / 显示 / 采集与识别 / 识别规则 / 高级参数 五页，改完即时生效。
- **托盘常驻**：`Ctrl+Alt+Q` 唤起面板，`Ctrl+N` 新建框选，`Ctrl+R` 重新识别。

## 目录

```
main.py                 程序入口（--selftest 自检 / --panel 直接开面板）
app/                    核心：采集、OCR、规则、贴图窗、控制面板
  ui_shell.py           无边框浮窗外壳（圆角 + 投影 + 入场动画）
  ui_theme.py           配色 / 圆角 / 字号规范
  ui_widgets.py         自绘控件（线性图标、胶囊开关、分段控件）
installer/setup_app.py  安装 / 卸载程序（向导界面）
assets/                 图标（app.ico / app.png / icon-source.jpg）
tools/                  辅助脚本（图标、payload、截图校验）
build.bat               打包应用（onedir / onefile / clean）
build_installer.bat     打包安装程序
sr_check_panel.py       控制面板离屏自检（45+ 项）
```

## 开发运行

```bat
D:\App\Miniconda\envs\pyt311\python.exe main.py
```

依赖在 `requirements.txt`。环境是 miniconda 的 `py311`：

```bat
D:\App\Miniconda\envs\pyt311\python.exe -m pip install -r requirements.txt
```

## 图标

图标是 `assets/icon-source.jpg` 转出来的，脚本会自己裁白边、切圆角、出多尺寸：

```bat
python tools\icon_from_image.py            :: -> assets/app.png + assets/app.ico
```

换一张源图（1024×1024 方形最好）重跑即可，exe、任务栏、托盘、侧栏共用同一份。

## 打包

```bat
build.bat onedir           :: 文件夹版 -> dist\StoneEye\
build.bat onefile          :: 单文件版
build_installer.bat        :: 安装程序 -> dist\StoneEye_安装程序_v4.7.0.exe
```

`build_installer.bat` 依赖上一步的 `dist\StoneEye`：它会把那份产物压成
`installer\payload.zip` 再塞进安装程序里。只改了安装界面、没改应用本体时，用
`build_installer.bat nopayload` 跳过压缩，能省十几秒。

> 打包脚本会先清理 `dist`，如果提示「安全删除被拒绝」，把旧目录**改名**挪开再打包即可。

## 安装

双击 `StoneEye_安装程序_v4.7.0.exe`：

1. 选安装位置（默认 `%LOCALAPPDATA%\Programs\StoneEye`，免管理员）；
2. 勾快捷方式 / 开机自启；
3. 解压 → 写注册表 → 建快捷方式。

卸载走「设置 → 应用 → 已安装的应用」，或运行安装目录里的 `StoneEye.exe --uninstall`
（卸载界面由应用本体提供，装完只有一份 exe，不额外占空间）。

### 静默安装 / 卸载（部署用）

```bat
StoneEye_安装程序_v4.7.0.exe --silent --dir "D:\Apps\StoneEye"
StoneEye_安装程序_v4.7.0.exe --silent --no-desktop --no-startmenu --autostart
StoneEye.exe --uninstall --quiet
```

退出码 `0` 成功、`1` 失败、`2` 装好了但快捷方式/注册表写失败；日志写在安装包同目录的 `install.log`。

## 自检

```bat
python sr_check_panel.py                        :: 面板离屏校验，应「全部通过」
dist\StoneEye\StoneEye.exe --selftest           :: 打包后验证模型可用
```

## 已知限制

- 全局热键在非管理员下可能注册不上，需要时右键 exe「以管理员身份运行」。
- 内容区只到 Win32 子窗口级，游戏内 D3D 全屏画面抓不到。
- 多屏混合 DPI 下坐标按系统 DPI 近似换算。
