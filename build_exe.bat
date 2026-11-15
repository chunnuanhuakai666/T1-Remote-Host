@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  T1-Remote-Host 打包脚本（PyInstaller，onedir）
REM
REM  用法：双击本文件，或在命令行执行 build_exe.bat
REM  产物：dist\T1RemoteHost\T1RemoteHost.exe
REM
REM  说明：
REM   - onedir 比 onefile 启动快很多（不用每次解压到临时目录），
REM     且 sherpa/onnxruntime 的原生 DLL 多，onefile 容易出问题。
REM   - 模型不打进包里：程序首次运行会自动从 HF 镜像下载，
REM     也可以手动把 model.int8.onnx + tokens.txt 放进
REM     dist\T1RemoteHost\models\sense-voice\
REM ============================================================

REM 找不到 python 时把这里改成完整路径，例如 D:\py\python.exe
if "%PY%"=="" set PY=python

%PY% -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [1/3] 安装 PyInstaller...
    %PY% -m pip install pyinstaller || goto :fail
) else (
    echo [1/3] PyInstaller 已安装
)

echo [2/3] 清理旧产物...
if exist build rmdir /s /q build
if exist dist\T1RemoteHost rmdir /s /q dist\T1RemoteHost

echo [3/3] 开始构建（约 1~2 分钟）...
%PY% -m PyInstaller --noconfirm --onedir --name T1RemoteHost ^
  --add-data "web;web" ^
  --collect-binaries sherpa_onnx ^
  --collect-binaries onnxruntime ^
  --collect-submodules bleak ^
  --collect-submodules pystray ^
  --hidden-import pystray._win32 ^
  --hidden-import t1_tray ^
  --hidden-import hid ^
  --hidden-import pythoncom ^
  --hidden-import win32com.client ^
  --hidden-import winrt ^
  --hidden-import winrt.system ^
  --hidden-import winrt.runtime ^
  --hidden-import winrt.windows.foundation ^
  --hidden-import winrt.windows.foundation.collections ^
  --hidden-import winrt.windows.devices.bluetooth ^
  --hidden-import winrt.windows.devices.bluetooth.advertisement ^
  --hidden-import winrt.windows.devices.bluetooth.genericattributeprofile ^
  --hidden-import winrt.windows.devices.radios ^
  --hidden-import winrt.windows.devices.enumeration ^
  --hidden-import winrt.windows.storage.streams ^
  --exclude-module faster_whisper ^
  --exclude-module ctranslate2 ^
  --exclude-module torch ^
  --exclude-module transformers ^
  --exclude-module sympy ^
  --exclude-module matplotlib ^
  --exclude-module IPython ^
  --exclude-module sklearn ^
  --exclude-module scipy ^
  --exclude-module pandas ^
  --exclude-module PyQt5 ^
  --exclude-module onnxruntime.quantization ^
  --exclude-module onnxruntime.transformers ^
  --exclude-module onnxruntime.tools ^
  --exclude-module onnxruntime.datasets ^
  t1_server.py
if errorlevel 1 goto :fail

if exist t1_ui_config.json copy /y t1_ui_config.json dist\T1RemoteHost\ >nul

echo.
echo 构建完成: dist\T1RemoteHost\T1RemoteHost.exe
echo 直接双击 exe 即为托盘模式；加 --port 8800 可换端口。
echo 模型未打包，首次运行会自动下载（约 229MB，走 hf-mirror.com）。
goto :eof

:fail
echo.
echo 构建失败，请检查上面的错误信息。
exit /b 1
