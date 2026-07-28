@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0GPT-SoVITS"
if "%TTS_PLATFORM_PYTHON%"=="" set "TTS_PLATFORM_PYTHON=C:\miniconda3\envs\tts-platform\python.exe"
"%TTS_PLATFORM_PYTHON%" api_v2.py -a 127.0.0.1 -p 9880
pause
