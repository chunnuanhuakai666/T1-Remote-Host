@echo off
chcp 936 >nul
cd /d "%~dp0"
title T1-Remote 上位机 Pro

rem 内置 Python 没有 tkinter，这里固定用系统 Python 3.12
set PY=D:\py\python.exe
if not exist "%PY%" set PY=python

echo.
echo   T1-Remote 上位机 Pro
echo   ----------------------------------------
echo   正在启动本地服务并打开窗口...
echo   关闭本窗口即退出服务
echo.

"%PY%" t1_server.py

if errorlevel 1 (
  echo.
  echo 启动失败，请检查 Python 与依赖 bleak / hidapi
  pause
)
