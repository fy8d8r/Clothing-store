@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -r requirements-web.txt
if errorlevel 1 pause & exit /b 1
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000"
py app.py
pause
