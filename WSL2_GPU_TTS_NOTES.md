# GPT-SoVITS on AMD GPU via WSL2 — experiment notes

分支：`experiment/gpu-tts-rocm-wsl2`。目的是把 GPT-SoVITS 從 CPU 挪到 GPU 上跑，
省掉現在 CPU 推理佔用的大量系統 RAM（見專案規劃 10.5 系列，AMD 顯卡在原生
Windows 下沒有 PyTorch ROCm 支援，只能靠 WSL2）。**這條路刻意留在獨立分支，
不進 `master`**——步驟多、版本要求精確，不適合放進給別人直接用的主線。

## 為什麼版本要釘死

這條路目前沒有官方一鍵支援，版本組合是從實測案例＋已知 bug 回報拼出來的，
版本差一點就會出問題（ABI 不合直接 crash、VRAM 讀取錯誤直接 segfault）。
**不要自己升級成「最新版」**，除非先驗證過新版本真的沒有下面這些坑。

## 已知可行的版本組合

參考同款顯卡（RX 9060 XT）的實測案例（note.com/crst10，跑類似的本地 TTS
app）：

- **OS**：WSL2 Ubuntu **24.04**（不是 26.04 — ROCm WSL 不支援 26.04）
- **Windows**：Windows **11**（ROCm on WSL 官方要求，Windows 10 會直接連
  GPU 都偵測不到，見 `ROCm/ROCm#6323`，這台機器本來就是 Win11，這條不是問題）
- **ROCm**：7.2.1
- **PyTorch**：`2.11.0+rocm7.2`（實測案例用的版本）——
  **或**改用修過 VRAM 上限 bug 的 nightly（見下方），兩者擇一
- **TorchCodec**：0.11.1（0.10.0 會有 undefined symbol crash）
- 需要手動編譯 `librocdxg`（要 Windows SDK headers）
- 需要 `LD_PRELOAD` 蓋掉 PyTorch 內建的 HSA runtime，不然會跟系統的衝突

## 已知會踩到、但已經有解法的坑

- **`ROCm/ROCm#6295`：RX 9060 XT（gfx1200）在 PyTorch 下 16GB VRAM 被硬性
  限制成只能用 ~8GB，超過直接 segfault（不是正常的 OOM 錯誤）。**
  根因是 KFD topology 的記憶體 bank 列舉 bug，AMD 已修好，用這個 nightly：
  ```
  pip install --force-reinstall --index-url https://rocm.nightlies.amd.com/v2/gfx120X-all/ \
    "torch==2.10.0+rocm7.13.0a20260513" \
    "torchvision==0.25.0+rocm7.13.0a20260513" \
    "torchaudio==2.10.0+rocm7.13.0a20260513"
  ```
  如果照上面的固定版本組合裝，之後才發現 VRAM 讀出來只有 ~8GB，就是踩到這個
  bug，換這個 nightly（或更新的）試試看。

- **GPT-SoVITS 自己的原始碼要 patch**：`torchaudio.load` 在 ROCm 環境下有
  問題，社群回報的修法是改用 `soundfile` 讀音檔（原始討論見 GPT-SoVITS repo
  的 issue，具體 issue 編號待這次動手時重新確認，上次記錄的 #2743 未經這次
  驗證）。

## 現況

**尚未開始動手**——這份筆記是動手前的版本/風險整理，還沒實際跑過安裝步驟。
下一步是照上面的版本組合，在 WSL2 Ubuntu 24.04 裡照 Irodori-TTS 那篇的 15
個步驟跑一次，看 GPT-SoVITS 能不能一樣裝起來。
