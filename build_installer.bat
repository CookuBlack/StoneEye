@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ============================================================
rem  StoneEye 安装程序 打包脚本
rem
rem  用法（在项目根目录）：
rem    build_installer.bat            生成安装程序
rem    build_installer.bat nopayload  复用上次的 payload.zip（只改界面时快很多）
rem
rem  前置：先跑 build.bat onedir 出文件夹版，安装程序的 payload 从那里来。
rem
rem  产物：dist\StoneEye_安装程序_v<版本>.exe（单文件，约 175 MB）
rem
rem  为什么不用 Inno Setup / NSIS：这台机器访问不了 GitHub，winget 下载
rem  Inno Setup 直接 InternetOpenUrl 失败；自己写还能和面板共用一套视觉。
rem ============================================================

set "PY=D:\App\Miniconda\envs\pyt311\python.exe"
set "MODE=%~1"

if not exist "%PY%" (
  echo [ERROR] 找不到 %PY%
  echo         请改成你自己的 conda 环境路径后重试。
  goto :fail
)

if not exist "dist\StoneEye\StoneEye.exe" (
  echo [ERROR] 还没有 dist\StoneEye，先跑：build.bat onedir
  goto :fail
)

echo ============================================
echo  Python : %PY%
echo ============================================

if /i not "%MODE%"=="nopayload" (
  echo [1/2] 生成 payload.zip
  "%PY%" tools\make_payload.py
  if errorlevel 1 goto :fail
) else (
  if not exist "installer\payload.zip" (
    echo [ERROR] 没有 installer\payload.zip，去掉 nopayload 参数重跑。
    goto :fail
  )
  echo [1/2] 复用已有 payload.zip
)

rem 版本号从 payload_info.json 里取 —— PyInstaller 的 --name 不吃中文，
rem 最终产物靠 move 改成中文名，版本号在这里拿到才能命名
"%PY%" -c "import json;print(json.load(open('installer/payload_info.json',encoding='utf-8'))['version'])" > "%TEMP%\sr_setup_ver.txt" 2>nul
set /p VER=<"%TEMP%\sr_setup_ver.txt"
if "%VER%"=="" set "VER=0.0.0"
echo  版本 : %VER%

echo [2/2] 打包安装程序
"%PY%" -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name StoneEye_Setup ^
  --icon assets\app.ico ^
  --add-data "assets;assets" ^
  --add-data "installer\payload.zip;." ^
  --add-data "installer\payload_info.json;." ^
  --hidden-import app.ui_shell ^
  --hidden-import app.ui_widgets ^
  --hidden-import PySide6.QtNetwork ^
  --exclude-module tkinter ^
  --exclude-module torch ^
  --exclude-module torchvision ^
  --exclude-module scipy ^
  --exclude-module sklearn ^
  --exclude-module pandas ^
  --exclude-module matplotlib ^
  --exclude-module IPython ^
  --exclude-module rapidocr_onnxruntime ^
  --exclude-module onnxruntime ^
  installer\setup_app.py
if errorlevel 1 goto :fail

set "OUT=dist\StoneEye_安装程序_v%VER%.exe"
if exist "%OUT%" del /q "%OUT%"
move /y "dist\StoneEye_Setup.exe" "%OUT%" >nul

echo.
echo ============================================
echo  [OK] 产物：%OUT%
echo  双击即可安装；免管理员，装进 %%LOCALAPPDATA%%\Programs。
echo.
echo  静默安装（部署用）：
echo    "%OUT%" --silent --dir "D:\Apps\StoneEye"
echo    --no-desktop --no-startmenu 可关掉快捷方式
echo  静默卸载：
echo    "%%LOCALAPPDATA%%\Programs\StoneEye\StoneEye.exe" --uninstall --quiet
echo ============================================
echo.
if "%~1"=="" if not defined SR_NOPAUSE pause
exit /b 0

:fail
echo.
echo [FAILED] 打包失败，把上面的错误日志贴出来排查。
if "%~1"=="" if not defined SR_NOPAUSE pause
exit /b 1
