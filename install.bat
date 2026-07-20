@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

REM ClassCtl installer - opens the graphical setup. No console window.

if not exist "%~dp0setup_wizard.py" (
    echo [ERROR] setup_wizard.py was not found next to this file.
    echo Folder: %~dp0
    pause
    exit /b 1
)

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

REM Find a real Python, preferring pythonw.exe (no console window).
REM The Microsoft Store alias in WindowsApps is skipped: it is not a real interpreter.
set "PYW="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$all=@(Get-Command pythonw.exe -All -ErrorAction SilentlyContinue)+@(Get-Command python.exe -All -ErrorAction SilentlyContinue); foreach($x in $all){ if($x.Source -and ($x.Source -notmatch 'WindowsApps') -and (Test-Path $x.Source)){ $x.Source; break } }"`) do set "PYW=%%P"

if not defined PYW goto nopython
if "%PYW%"=="" goto nopython
if not exist "%PYW%" goto nopython

start "" "%PYW%" "%~dp0setup_wizard.py"
exit /b 0

:nopython
echo.
echo ============================================================
echo  Python was not found for the account you are logged in as.
echo ============================================================
echo.
echo  Install Python from https://python.org and tick BOTH:
echo.
echo     [x] Add python.exe to PATH
echo     [x] Install for all users
echo.
echo  "Install for all users" matters: a per-user install is invisible
echo  to other accounts and to the SYSTEM service that runs the agent.
echo.
echo  Then run this installer again.
echo.
pause
exit /b 1
