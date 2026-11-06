# StoneEye

框选屏幕任意一块 → OCR 认出画面里的「景别 / 运镜」→ 按句子切分、上色 → 把标注贴回原处并跟着走。
面向小说转漫画的分镜标注：看得懂「全景 / 中景 / 特写 / 推 / 拉 / 摇 / 移」，也能自己加词表。

当前版本 **4.8.1**。

---

## 功能

- **框选贴图**：拖一块区域就能贴一张实时跟随的标注层；位置、缩放、透明度都能调。
- **分类着色**：景别与运镜用不同颜色标注，同一句里命中的词一起亮。
- **整句切分**：按逗号、句号、分号、破折号断句，可切「整句标注 / 词级标注」。
- **快照 / 实时**：快照定格当时画面；实时模式按帧差决定要不要重新识别。
- **控制面板**：概览 / 显示 / 采集与识别 / 识别规则 / 高级参数 五页，改完即时生效。
- **托盘常驻**：`Ctrl+Alt+Q` 唤起面板，`Ctrl+N` 新建框选，`Ctrl+R` 重新识别。
- **单实例**：重复双击 exe 不会再开出第二个托盘，第二个实例会把已有实例的面板唤起来。

## 截图编辑态与标注工具栏

按 `Ctrl+Alt+Q` 框选完，画面会先冻结进入可标注的编辑态，选区下方浮出一条
和微信 / QQ 截图同款的 16 键工具条（两条灰色分隔线分三组）：

| 组 | 功能 |
|---|---|
| 基础标注 | 矩形框、圆形 / 椭圆、表情贴纸、箭头、画笔、马赛克、文字 |
| 进阶智能 | 自动序号（1、2、3…）、OCR 提取文字、截长图 |
| 操作 | 撤销、保存、钉在桌面、发送 / 分享、取消（红）、完成（绿） |

- 选中绘制工具后，上方会出现颜色 / 线宽选项条；文字工具另外给 `A-` / `A+` 调字号。
- 表情工具弹出 20 个表情，选完在选区里点哪贴哪。
- 马赛克是**真打码**：把底图那块缩到 7×7 再放大回去，不是盖一层半透明色块；撤销可还原。
- OCR 提取文字复用本项目的 RapidOCR，结果是可一键复制的文本面板。
- 截长图用 Win32 滚轮事件自动滚 8 帧，按重叠行算位移后拼成一张长图；要求目标窗口
  能响应滚轮，且滚动期间别动鼠标。长图生成后不能再叠加标注，直接完成 / 保存即可。
- 快捷键：`Ctrl+Z` 撤销、`Ctrl+S` 保存、`Enter` 完成、`Esc` / 右键取消。
- **完成**＝复制到剪贴板并生成置顶贴图（保留原来的 OCR 景别/运镜标注链路）；
  **钉在桌面**＝只生成置顶贴图；**发送**＝复制到剪贴板，去聊天窗口 `Ctrl+V`；
  **保存**＝写出 PNG；**取消**＝丢弃。
- 工具条、选项条、表情板都登记为编辑器的 owned window，并且不抢键盘焦点：
  点画布绘制、换颜色、切工具、弹窗返回之后都稳定浮在最上层（4.8.1 修复了
  「点一下工具条就消失」）。
- 想要原来「框完立刻贴图」的一步式操作，用托盘菜单「快速贴图（跳过标注）」。
- Windows 没有通用的「发给当前聊天窗口」接口，所以「发送」降级为复制图片到剪贴板。

## 目录

```
main.py                 程序入口（--selftest 自检 / --panel 直接开面板）
app/                    核心：采集、OCR、规则、贴图窗、控制面板
  ui_shell.py           无边框浮窗外壳（圆角 + 投影 + 入场动画）
  ui_theme.py           配色 / 圆角 / 字号规范
  ui_widgets.py         自绘控件（线性图标、胶囊开关、分段控件）
  capture_icons.py      工具栏 16 枚线性图标
  capture_toolbar.py    16 键工具条 + 颜色/线宽选项条 + 表情板
  screenshot_editor.py  全屏编辑态、标注撤销栈、合成输出
  scroll_capture.py     滚轮截长图 + 重叠行拼接
  single_instance.py    命名互斥体 + 本地管道唤起
installer/setup_app.py  安装 / 卸载程序（向导界面）
assets/                 图标（app.ico / app.png / icon-source.jpg）
tools/                  辅助脚本（图标、payload、截图校验）
build.bat               打包应用（onedir / onefile / clean）
build_installer.bat     打包安装程序
sr_check_panel.py       控制面板离屏自检（45+ 项）
sr_check_editor.py      截图编辑态离屏自检（40+ 项）
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
build_installer.bat        :: 安装程序 -> dist\StoneEye_安装程序_v4.8.1.exe
```

`build_installer.bat` 依赖上一步的 `dist\StoneEye`：它会把那份产物压成
`installer\payload.zip` 再塞进安装程序里。只改了安装界面、没改应用本体时，用
`build_installer.bat nopayload` 跳过压缩，能省十几秒。

> 打包脚本会先清理 `dist`，如果提示「安全删除被拒绝」，把旧目录**改名**挪开再打包即可。

## 安装

双击 `StoneEye_安装程序_v4.8.1.exe`：

1. 选安装位置（默认 `%LOCALAPPDATA%\Programs\StoneEye`，免管理员）；
2. 勾快捷方式 / 开机自启；
3. 解压 → 写注册表 → 建快捷方式。

卸载走「设置 → 应用 → 已安装的应用」，或运行安装目录里的 `StoneEye.exe --uninstall`
（卸载界面由应用本体提供，装完只有一份 exe，不额外占空间）。

### 静默安装 / 卸载（部署用）

```bat
StoneEye_安装程序_v4.8.1.exe --silent --dir "D:\Apps\StoneEye"
StoneEye_安装程序_v4.8.1.exe --silent --no-desktop --no-startmenu --autostart
StoneEye.exe --uninstall --quiet
```

退出码 `0` 成功、`1` 失败、`2` 装好了但快捷方式/注册表写失败；日志写在安装包同目录的 `install.log`。

## 自检

```bat
python sr_check_panel.py                        :: 面板离屏校验，应「全部通过」
python sr_check_editor.py                       :: 编辑态 / 工具栏 / 长图拼接校验
python tools/check_zorder.py --assert           :: 真实桌面验证浮窗不会被编辑器盖住
dist\StoneEye\StoneEye.exe --smoke              :: 打包后验证延迟导入模块齐全
dist\StoneEye\StoneEye.exe --selftest           :: 打包后验证模型可用
```

## 已知限制

- 全局热键在非管理员下可能注册不上，需要时右键 exe「以管理员身份运行」。
- 内容区只到 Win32 子窗口级，游戏内 D3D 全屏画面抓不到。
- 多屏混合 DPI 下坐标按系统 DPI 近似换算。
