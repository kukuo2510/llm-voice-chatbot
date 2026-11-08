@echo off
rem Double-click to install the desktop pet on this PC (runs install.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
