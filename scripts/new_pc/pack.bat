@echo off
rem Double-click to pack the desktop pet into D:\desktop_pet_package (runs pack.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack.ps1" %*
pause
