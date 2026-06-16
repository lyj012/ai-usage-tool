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

set "CONFIG=%AIUSAGE_CONFIG%"
if "%CONFIG%"=="" set "CONFIG=%CD%\aiusage-config.json"

set "REPORT_DIR=%CD%\data\reports\%TODAY%"
set "REPORT_MD=%REPORT_DIR%\daily-report.md"
set "REPORT_JSON=%REPORT_DIR%\daily-report.json"

echo Generating v2 personal workday report
echo Person: %PERSON%
echo Date: %TODAY%
echo Config: %CONFIG%
echo.

if not exist "%CONFIG%" (
  echo Missing config file: %CONFIG%
  echo Create it first, for example:
  echo python aiusage.py init-config --out "%CONFIG%" --project "ai-usage-tool=%CD%|https://github.com/lyj012/ai-usage-tool"
  goto failed
)

python aiusage.py export-workday --person "%PERSON%" --date "%TODAY%" --config "%CONFIG%" --verbose
if errorlevel 1 goto failed

echo.
echo Done. Generated v2 personal workday report:
echo Markdown: %REPORT_MD%
echo JSON: %REPORT_JSON%
echo.
echo Note: run_export_today.bat is the old v1 zip export for AI usage statistics.
echo Use run_workday_report.bat for v2 personal workday reports.
pause
exit /b 0

:failed
echo.
echo Failed with error level: %errorlevel%
echo This script generates the v2 personal workday report with export-workday.
echo run_export_today.bat is the old v1 zip export.
pause
exit /b %errorlevel%
