@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ============================================================
rem  StoneEye 打包脚本
rem
rem  用法（在项目根目录）：
rem    build.bat            打包成文件夹版（默认，启动快）
rem    build.bat onedir     同上
rem    build.bat onefile    打包成单个 exe（启动慢几秒）
rem    build.bat clean      删除 build\ dist\ 中间产物
rem
rem  Python 用 D 盘的 miniconda py311 环境：base 里没装 PySide6，
rem  用默认 python 打包会「打包成功、运行报缺模块」。
rem ============================================================

set "PY=D:\App\Miniconda\envs\pyt311\python.exe"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=onedir"

if /i "%MODE%"=="clean" goto :clean

if not exist "%PY%" (
  echo [ERROR] 找不到 %PY%
  echo         请改成你自己的 conda 环境路径后重试。
  goto :fail
)

echo ============================================
echo  Python : %PY%
echo  Mode   : %MODE%
echo ============================================

"%PY%" -c "import PyInstaller, PySide6" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] 该环境缺少 PyInstaller 或 PySide6，先执行：
  echo         "%PY%" -m pip install -r requirements.txt
  goto :fail
)

rem 有正在运行的实例会锁住 dist 里的 exe
tasklist /fi "imagename eq StoneEye.exe" 2>nul | find /i "StoneEye.exe" >nul
if not errorlevel 1 (
  echo [WARN] StoneEye.exe 正在运行，请先退出托盘程序再打包。
  goto :fail
)

set "FLAGS=--noconfirm --clean --windowed --name StoneEye"
set "FLAGS=%FLAGS% --icon assets\app.ico"
set "FLAGS=%FLAGS% --add-data assets;assets"
set "FLAGS=%FLAGS% --add-data config.json;."
rem 只收 rapidocr 自己的模型文件；onnxruntime 由官方 hook 收 DLL。
rem 千万别 --collect-all onnxruntime：会顺带拉进 onnxruntime.transformers
rem -> torch，产物体积从 300MB 变成 3GB。
set "FLAGS=%FLAGS% --collect-all rapidocr_onnxruntime"
set "FLAGS=%FLAGS% --collect-binaries onnxruntime"
set "FLAGS=%FLAGS% --hidden-import keyboard"
set "FLAGS=%FLAGS% --hidden-import app.control_panel"
set "FLAGS=%FLAGS% --hidden-import app.rule_editor"
set "FLAGS=%FLAGS% --hidden-import app.box_refine"
set "FLAGS=%FLAGS% --hidden-import app.assets"
set "FLAGS=%FLAGS% --hidden-import app.ui_theme"
set "FLAGS=%FLAGS% --hidden-import app.ui_widgets"
set "FLAGS=%FLAGS% --hidden-import app.ui_shell"
set "FLAGS=%FLAGS% --hidden-import PySide6.QtNetwork"
set "FLAGS=%FLAGS% --hidden-import app.single_instance"
set "FLAGS=%FLAGS% --hidden-import app.capture_icons"
set "FLAGS=%FLAGS% --hidden-import app.capture_toolbar"
set "FLAGS=%FLAGS% --hidden-import app.screenshot_editor"
set "FLAGS=%FLAGS% --hidden-import app.scroll_capture"
set "FLAGS=%FLAGS% --exclude-module tkinter"
set "FLAGS=%FLAGS% --exclude-module torch"
set "FLAGS=%FLAGS% --exclude-module torchvision"
set "FLAGS=%FLAGS% --exclude-module torchaudio"
set "FLAGS=%FLAGS% --exclude-module scipy"
set "FLAGS=%FLAGS% --exclude-module sklearn"
set "FLAGS=%FLAGS% --exclude-module pandas"
set "FLAGS=%FLAGS% --exclude-module matplotlib"
set "FLAGS=%FLAGS% --exclude-module IPython"

rem conda 把 ctypes 依赖的 ffi.dll 放在 Library\bin（不在 site-packages），
rem PyInstaller 扫不到，打包后一启动就报
rem   "DLL load failed while importing _ctypes: 找不到指定的模块"
rem 顺手把 lzma / bz2 / ssl / sqlite 这几个 conda 版依赖一起带上。
for %%I in ("%PY%") do set "ENVROOT=%%~dpI"
for %%D in (ffi.dll liblzma.dll libbz2.dll libexpat.dll libcrypto-3-x64.dll libssl-3-x64.dll sqlite3.dll zlib.dll) do (
  if exist "%ENVROOT%Library\bin\%%D" (
    set "FLAGS=!FLAGS! --add-binary "%ENVROOT%Library\bin\%%D;.""
  )
)

if /i "%MODE%"=="onefile" (
  set "FLAGS=%FLAGS% --onefile"
  set "OUTDIR=dist"
) else (
  set "FLAGS=%FLAGS% --onedir"
  set "OUTDIR=dist\StoneEye"
)

"%PY%" -m PyInstaller %FLAGS% main.py
if errorlevel 1 goto :fail

rem 配置放在 exe 旁边，打包后用户可继续手工改词表；
rem 已存在则不覆盖，避免把用户调好的词表冲掉
if exist "%OUTDIR%\config.json" (
  echo [SKIP] 保留已有 %OUTDIR%\config.json
) else (
  copy /y config.json "%OUTDIR%\" >nul
)

echo.
echo ============================================
echo  [OK] 产物：%OUTDIR%\StoneEye.exe
if /i "%MODE%"=="onefile" (
  echo  单文件版：把 dist\StoneEye.exe 拷走即用。
  echo  首次启动会解压到临时目录，约 5~15 秒，属正常现象。
) else (
  echo  文件夹版：整个 dist\StoneEye 一起拷走，双击里面的 exe。
  echo  启动比单文件快，也不会被杀软反复扫临时目录。
)
echo.
echo  托盘没图标 / 快捷键无效时：
echo    - 全局热键需要管理员权限，右键 exe「以管理员身份运行」
echo    - 看同目录 error.log
echo ============================================
echo.
if "%~1"=="" if not defined SR_NOPAUSE pause
exit /b 0

:clean
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist StoneEye.spec del /q StoneEye.spec
echo [OK] 已清理 build\ dist\
if "%~1"=="" if not defined SR_NOPAUSE pause
exit /b 0

:fail
echo.
echo [FAILED] 打包失败，把上面的错误日志贴出来排查。
echo 调试技巧：把 --windowed 换成 --console 可以保留控制台看报错。
if "%~1"=="" if not defined SR_NOPAUSE pause
exit /b 1
