@echo off
setlocal EnableExtensions

cd /d "%~dp0"

python --version >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  echo Install Python first, then double-click this file again.
  goto failed
)

set "PERSON=%AIUSAGE_PERSON%"
if "%PERSON%"=="" set "PERSON=%USERNAME%"

for /f %%D in ('python -c "from datetime import date; print(date.today().isoformat())"') do set "TODAY=%%D"
if "%TODAY%"=="" goto failed

set "OUT_DIR=%AIUSAGE_OUT%"
if "%OUT_DIR%"=="" set "OUT_DIR=%USERPROFILE%\Desktop"

echo Exporting AI usage records
echo Person: %PERSON%
echo Date: %TODAY%
echo Output: %OUT_DIR%
echo Projects: lb-pdf-platform, lb-pdf-admin
echo.

python aiusage.py export-day --person "%PERSON%" --date "%TODAY%" --out "%OUT_DIR%" --project "lb-pdf-platform" --project "lb-pdf-admin" --verbose
if errorlevel 1 goto failed

echo.
echo Done. ZIP file generated in: %OUT_DIR%
pause
exit /b 0

:failed
echo.
echo Failed with error level: %errorlevel%
pause
exit /b %errorlevel%
