@echo off
setlocal EnableExtensions

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo Starting AI Usage Dashboard...
echo The browser should open automatically. If not, use the Local URL shown here.
echo.

".venv\Scripts\python.exe" -m streamlit run app.py
if errorlevel 1 goto failed

exit /b 0

:failed
echo.
echo Failed with error level: %errorlevel%
pause
exit /b %errorlevel%
