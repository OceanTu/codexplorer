@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
if not exist "%~dp0payload\app.py" goto source
set "TOKEN_LENS_INSTALLER=%~dp0installer.py"
goto python
:source
echo Building the Windows installer from source...
set "TOKEN_LENS_INSTALLER=%~dp0scripts\install_windows.py"
:python
if defined TOKEN_LENS_PYTHON goto configured
py -3 -X utf8 -B "%TOKEN_LENS_INSTALLER%" %*
set "TOKEN_LENS_RESULT=%ERRORLEVEL%"
goto done
:configured
"%TOKEN_LENS_PYTHON%" -X utf8 -B "%TOKEN_LENS_INSTALLER%" %*
set "TOKEN_LENS_RESULT=%ERRORLEVEL%"
:done
if not "%TOKEN_LENS_RESULT%"=="0" echo Installation failed. Python 3.9+ from python.org is required.
if "%~1"=="" pause
exit /b %TOKEN_LENS_RESULT%
