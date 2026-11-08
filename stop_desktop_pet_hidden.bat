@echo off
rem Stop the TTS server, desktop pet and stage started in the background.
rem Only matches this project's processes (api_v2.py, desktop_pet.py, livliv-stage).
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'api_v2\.py|desktop_pet\.py|livliv-stage' } | ForEach-Object { Write-Host ('stop ' + $_.Name + ' ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 >nul
