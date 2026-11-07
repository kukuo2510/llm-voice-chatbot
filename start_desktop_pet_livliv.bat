@echo off
rem Usage: start_desktop_pet_livliv.bat [model]  (default: sakiko404; also k01, d03ft, sakiko)
chcp 65001 >nul
set PYTHONUTF8=1
set LIVLIV_STAGE=1
cd /d "%~dp0"
set "LIVLIV_MODEL=%~1"
if "%LIVLIV_MODEL%"=="" set "LIVLIV_MODEL=sakiko404"
if "%TTS_PLATFORM_PYTHON%"=="" set "TTS_PLATFORM_PYTHON=C:\miniconda3\envs\tts-platform\python.exe"
rem Close a stage left over from a crash: it would keep the UDP port. Only matches livliv-stage, not AIRI.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='electron.exe'\" | Where-Object { $_.CommandLine -match 'livliv-stage' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
start "" /d "%~dp0..\livliv-stage" "%~dp0..\livliv-stage\node_modules\electron\dist\electron.exe" . --model=%LIVLIV_MODEL%
"%TTS_PLATFORM_PYTHON%" scripts\desktop_pet\desktop_pet.py
pause
