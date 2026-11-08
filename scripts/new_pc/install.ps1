# 在新電腦上安裝桌寵（祥子）。雙擊同一個資料夾裡的「install.bat」執行，不用回答任何問題。
# 會自動安裝：Miniconda、Python 環境（套件版本跟原本的電腦一模一樣）、Node.js、舞台、Ollama 和對話／看圖模型，
# 最後在桌面建立捷徑。已經裝過的步驟會跳過，所以中途失敗可以直接再執行一次。
# 電腦負荷不了（例如顯示卡記憶體不夠）不會擋安裝，只是跑起來會比較慢。
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Platform = Join-Path $Root "TTSdata/platform"
$Stage = Join-Path $Root "TTSdata/livliv-stage"
$EnvName = "tts-platform"
$VisionModel = "qwen3-vl:2b"

function Step($text) { Write-Host ""; Write-Host "==== $text ====" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Need-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "找不到 winget。請先到 Microsoft Store 安裝或更新「應用程式安裝程式」(App Installer)，再重新執行。"
    }
}
function Winget-Install($id) {
    Need-Winget
    winget install -e --id $id --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
function Find-Conda {
    $candidates = @(
        (Join-Path $env:USERPROFILE "miniconda3/Scripts/conda.exe"),
        "C:/miniconda3/Scripts/conda.exe",
        "C:/ProgramData/miniconda3/Scripts/conda.exe",
        (Join-Path $env:LOCALAPPDATA "miniconda3/Scripts/conda.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

Step "1/6 Miniconda"
$conda = Find-Conda
if (-not $conda) {
    Winget-Install "Anaconda.Miniconda3"
    $conda = Find-Conda
    if (-not $conda) { throw "Miniconda 裝好了但找不到 conda.exe，請重開電腦後再執行一次。" }
}
Write-Host "conda: $conda"

Step "2/6 Python 環境（第一次約 10～20 分鐘）"
$envPython = Join-Path (Split-Path (Split-Path $conda)) "envs/$EnvName/python.exe"
if (-not (Test-Path $envPython)) {
    # 用 conda-forge，避開 Anaconda 預設頻道要先同意服務條款的問題
    & $conda create -y -n $EnvName -c conda-forge --override-channels python=3.11
}
& $envPython -m pip install --upgrade pip
& $envPython -m pip install -r (Join-Path $Root "requirements-lock.txt") --extra-index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw "Python 套件安裝失敗，看上面的錯誤訊息。" }
# 啟動檔用這個環境變數找 Python（見 TTSdata/platform/scripts/launch_hidden.bat）
[Environment]::SetEnvironmentVariable("TTS_PLATFORM_PYTHON", $envPython, "User")
$env:TTS_PLATFORM_PYTHON = $envPython

Step "3/6 Node.js 和舞台"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Winget-Install "OpenJS.NodeJS.LTS" }
Push-Location $Stage
npm ci
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "舞台套件安裝失敗（npm ci）。" }
Pop-Location

Step "4/6 Ollama 和模型（對話模型約 8 GB，看網速）"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Winget-Install "Ollama.Ollama" }
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) { $ollama = Join-Path $env:LOCALAPPDATA "Programs/Ollama/ollama.exe" }
try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434 -TimeoutSec 3 | Out-Null }
catch { Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep -Seconds 5 }
$chatModel = "qwen2.5:14b"
$envFile = Join-Path $Platform ".env"
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern "^OLLAMA_MODEL=(.+)$" | Select-Object -First 1
    if ($line) { $chatModel = $line.Matches[0].Groups[1].Value.Trim() }
}
foreach ($m in @($chatModel, $VisionModel)) {
    Write-Host "下載 $m"
    & $ollama pull $m
    if ($LASTEXITCODE -ne 0) { throw "下載模型 $m 失敗。" }
}

Step "5/6 桌面捷徑"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "桌寵祥子.lnk"))
$lnk.TargetPath = Join-Path $Platform "start_desktop_pet_hidden.vbs"
$lnk.WorkingDirectory = $Platform
$lnk.Save()

Step "6/6 檢查"
$ok = $true
& $envPython -c "import torch, funasr, sounddevice, pynput, win32gui; print('Python 套件 OK', torch.__version__)"
if ($LASTEXITCODE -ne 0) { $ok = $false; Write-Host "Python 套件有問題" -ForegroundColor Red }
if (-not (Test-Path (Join-Path $Stage "node_modules/electron/dist/electron.exe"))) { $ok = $false; Write-Host "舞台沒裝好" -ForegroundColor Red }
$models = & $ollama list
foreach ($m in @($chatModel, $VisionModel)) {
    if (-not ($models -match [regex]::Escape($m))) { $ok = $false; Write-Host "模型 $m 沒裝好" -ForegroundColor Red }
}
if ($ok) {
    Write-Host ""
    Write-Host "安裝完成！雙擊桌面的「桌寵祥子」啟動（第一次開要等 1～2 分鐘載入）。" -ForegroundColor Green
    Write-Host "有問題時看 TTSdata/platform/logs/ 裡的 pet.log、tts.log。"
} else {
    Write-Host "有步驟沒成功，看上面紅字。修好後再執行一次 install.bat，已完成的步驟會跳過。" -ForegroundColor Yellow
}
