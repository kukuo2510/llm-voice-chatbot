# 把桌寵打包成一個資料夾，拿去新電腦用：複製整個資料夾過去，雙擊裡面的 install.bat。
# 用法：雙擊同資料夾的 pack.bat，或 powershell -File pack.ps1 [輸出資料夾]（預設 D:\desktop_pet_package）
# 會帶走：程式、GPT-SoVITS 和祥子的聲音模型、語氣參考音檔、舞台和角色模型、.env（含金鑰）、
#         桌寵偏好和對話記憶。不帶：語音輸出、log、node_modules、Python 環境（新電腦照版本清單重裝）。
param([string]$Out = "D:\desktop_pet_package")
$ErrorActionPreference = "Stop"
$TTSdata = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$python = $env:TTS_PLATFORM_PYTHON
if (-not $python) { $python = "C:\miniconda3\envs\tts-platform\python.exe" }

New-Item -ItemType Directory -Force -Path $Out | Out-Null
Write-Host "打包到 $Out"

# robocopy 的結束碼 0～7 都算成功
robocopy (Join-Path $TTSdata "platform") (Join-Path $Out "TTSdata\platform") /MIR /NFL /NDL /NJH /NP /XD outputs logs __pycache__ /XF *.pyc
if ($LASTEXITCODE -ge 8) { throw "複製 platform 失敗" }
robocopy (Join-Path $TTSdata "livliv-stage") (Join-Path $Out "TTSdata\livliv-stage") /MIR /NFL /NDL /NJH /NP /XD node_modules __pycache__ /XF *.pyc
if ($LASTEXITCODE -ge 8) { throw "複製 livliv-stage 失敗" }

# Python 套件的確切版本，新電腦照這份重裝，才會跟這台一模一樣
& $python -m pip freeze | Set-Content -Encoding ASCII (Join-Path $Out "requirements-lock.txt")
Copy-Item (Join-Path $PSScriptRoot "install.ps1") $Out -Force
Copy-Item (Join-Path $PSScriptRoot "install.bat") $Out -Force

$size = (Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum / 1GB
Write-Host ("完成，約 {0:N1} GB。整個資料夾複製到新電腦，放在想裝的位置，雙擊 install.bat。" -f $size) -ForegroundColor Green
$global:LASTEXITCODE = 0
