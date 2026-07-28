@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if "%TTS_PLATFORM_PYTHON%"=="" set "TTS_PLATFORM_PYTHON=C:\miniconda3\envs\tts-platform\python.exe"
"%TTS_PLATFORM_PYTHON%" scripts\discord_bot\bot.py
pause
