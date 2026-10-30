@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先按 README 安装项目依赖。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py
pause
