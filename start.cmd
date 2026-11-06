@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
py -3 -X utf8 -B "%~dp0app.py" serve %*
if errorlevel 1 pause
