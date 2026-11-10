@echo off
rem Hidden launcher: TTS server + stage + desktop pet, no console windows. Output goes to logs\.
rem Do not double-click this file directly (it would show a console); run "start_desktop_pet_hidden.vbs".
rem To stop: quit the pet from its right-click menu. The stage and the TTS server close with it
rem (the TTS server only if this script started it). "stop_desktop_pet_hidden.bat" is for when
rem something hangs.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
rem PET_AVATAR_CHARACTER set = use that character's two mouth images in the classic window, no LivLiv stage.
if defined PET_AVATAR_CHARACTER (set LIVLIV_STAGE=0) else (set LIVLIV_STAGE=1)
cd /d "%~dp0.."
if not exist logs mkdir logs
if "%LIVLIV_MODEL%"=="" set "LIVLIV_MODEL=sakiko404"
if "%TTS_PLATFORM_PYTHON%"=="" set "TTS_PLATFORM_PYTHON=C:\miniconda3\envs\tts-platform\python.exe"

rem Start the TTS server only if it is not already listening (reuse it otherwise).
netstat -ano | findstr ":9880 " | findstr LISTENING >nul
set "STARTED_TTS="
if errorlevel 1 (
  set "STARTED_TTS=1"
  start "" /b /d "%cd%\GPT-SoVITS" "%TTS_PLATFORM_PYTHON%" api_v2.py -a 127.0.0.1 -p 9880 > logs\tts.log 2>&1
)
rem Wait for the TTS server, up to about 3 minutes.
for /l %%i in (1,1,90) do (
  curl -s -o nul http://127.0.0.1:9880/ && goto tts_ready
  rem "timeout" fails at once without a console window, so ping is used as a 2-second sleep.
  ping -n 3 127.0.0.1 >nul
)
:tts_ready

rem Close a stage left over from a crash (it would hold the UDP port). Only matches livliv-stage.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='electron.exe'\" | Where-Object { $_.CommandLine -match 'livliv-stage' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
if "%LIVLIV_STAGE%"=="1" start "" /d "%cd%\..\livliv-stage" "%cd%\..\livliv-stage\node_modules\electron\dist\electron.exe" . --model=%LIVLIV_MODEL%
"%TTS_PLATFORM_PYTHON%" scripts\desktop_pet\desktop_pet.py > logs\pet.log 2>&1

rem The pet has exited (quit from its menu, or crashed): close the stage, and the TTS server if we started it.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='electron.exe'\" | Where-Object { $_.CommandLine -match 'livliv-stage' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
if defined STARTED_TTS powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'api_v2\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
