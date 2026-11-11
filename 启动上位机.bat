@echo off
chcp 936 >nul
cd /d "%~dp0"

rem 上位机依赖 tkinter，需用系统 Python 3.12 而非内置精简版
set PY=D:\py\python.exe
if not exist "%PY%" set PY=python

echo 启动 T1-Remote 上位机 ...
"%PY%" t1_gui.py
if errorlevel 1 pause
