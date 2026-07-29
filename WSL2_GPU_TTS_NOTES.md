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

## 現況（2026-07-29：端到端跑通了，但結論是「先別換」）

### 跟預期不同的地方

- **不用手動編譯 `librocdxg`**：官方 `amdgpu-install -y --usecase=wsl,rocm --no-dkms`
  已經內建處理好，`rocminfo` 直接抓到 `gfx1200` / `AMD Radeon RX 9060 XT`，
  找不到任何 rocdxg 相關套件——這條路跟部落格描述的 DIY 路徑不同，可能是
  ROCm 7.2 官方安裝腳本後來把這步驟收進去了。
- **`torchaudio.load` 的替代方案不是「改個 import」而已**：真正卡住 GPU
  偵測的關鍵是 PyTorch wheel 自己打包了一份 `torch/lib/libhsa-runtime64.so`
  和 `libamdhip64.so`，這兩個是原生 Linux（`/dev/kfd`）版本，在 WSL（走
  `/dev/dxg`）下會讓 `torch.cuda.is_available()` 回傳 `False`。系統另外裝的
  `hsa-runtime-rocr4wsl-amdgpu` 套件才是 WSL 專用版本。解法是執行時用
  `LD_PRELOAD=/opt/rocm-7.2.0/lib/libhsa-runtime64.so:/opt/rocm-7.2.0/lib/libamdhip64.so`
  蓋掉 PyTorch 自帶的版本，不然裝什麼版本的 PyTorch 都一樣抓不到 GPU。
- `torchaudio.load(ref_audio_path)` 這行（`TTS_infer_pack/TTS.py` 的
  `_get_ref_spec`）改成 `soundfile.read()` 讀出 numpy array 再轉 tensor
  （`torch.from_numpy(raw_audio_np.T)`），這個 patch **只動了 WSL 這邊自己
  獨立複製出來的一份程式碼**（見下方「怎麼測的」），Windows 那邊 production
  在用的 `platform/GPT-SoVITS/` 完全沒被動到。

### 效能結果（不理想——GPU 版本比 CPU 版本慢，不是預期中的加速）

用同一個角色的 finetuned 模型（`sakiko_clean`, v2Pro）、同一句參考音檔，測了
四次抓穩定狀態（MIOpen 第一次遇到新的運算 shape/精度組合要現場調校 kernel，
這個成本要排除掉才能公平比較）：

| 設定 | model load | generate（一句短句） |
|---|---|---|
| fp32，冷（含 MIOpen 調校） | — | 39.1s |
| fp32，熱 | 6.7s | 12.0s |
| fp16，冷（切精度=新一輪調校） | 7.2s | 31.2s |
| fp16，熱 | 6.1s | **13.5s** |

穩定狀態下 GPU 大約 **12–13.5 秒生成一句短句**，fp16 沒有比 fp32 快。對照
CPU 版本的既有記錄（見 [[project-amd-gpu-and-tts-hardware]]）：**~9 秒生成
兩句話**，換算成單句大約 4.5 秒。**也就是這張消費級 RDNA4 顯卡透過
WSL2/ROCm 跑 GPT-SoVITS，反而比現在的 CPU 路徑慢上 2.5–3 倍**，不是預期中
「換 GPU 應該要更快」。合理懷疑是 ROCm/MIOpen 的優化重心在資料中心卡
（CDNA），消費級 RDNA 卡的這類工作負載沒有調得很好，加上 WSL2 的 GPU
虛擬化層（DXG paravirtualization）本身也有一層開銷。

VRAM 峰值：fp16 和 fp32 都差不多，**~1.7GB**（`torch.cuda.max_memory_allocated`
量出來的），比預期省，跟現有 Ollama 兩顆模型（~11.5GB）疊起來大約
13.2GB，16GB 卡還有 ~2.8GB 剩，比原本規劃留給遊戲/工作的 ~4.5GB 更緊繃一點。

### 重要但容易忽略的一點：系統 RAM 不會真的變少

當初想換到 GPU 的動機是「CPU 版 GPT-SoVITS 佔用系統 RAM 太多」。但 **WSL2
本身是一個輕量 VM，Windows 還是要撥一塊 RAM 給這個 VM（工作管理員裡的
`Vmmem` process）**——GPT-SoVITS 的模型權重、PyTorch runtime、Python 直譯器
這些東西搬到 WSL 裡跑，並不會讓它們消失，只是從「Windows 裡一個
python.exe」變成「WSL VM 裡佔用的記憶體」，一樣要跟其他要用電腦的東西
（遊戲、工作）搶系統 RAM。真正會變少的只有「模型的張量運算搬去吃 VRAM
而不是系統 RAM」這一小塊（就是上面量到的 ~1.7GB），不是整個 GPT-SoVITS
process 的記憶體footprint都消失。

### 結論：這個時間點不建議換成這條路

速度變慢 2.5–3 倍、換來的系統 RAM 節省又沒有原本想像的多（因為 WSL VM 本身
要吃記憶體），這筆交易目前不划算。**建議維持現有 CPU 推理**，這份筆記留著
——如果之後 ROCm/MIOpen 對消費級 RDNA 卡的優化變好、或者 WSL2 的 GPU
虛擬化開銷有改善，可以回來重新測一次，版本組合跟已知的坑（本文件其餘部分）
應該還是有效的參考起點。

### 怎麼測的（供之後重跑）

- WSL2 distro 用獨立的 `Ubuntu-24.04`（不是預設的 `Ubuntu`，那個已經是
  26.04），`wsl --install -d Ubuntu-24.04 --no-launch`，全程用 `-u root`
  跑，沒建立額外的一般使用者
- venv：`/root/gpu-test-venv`（`python3 -m venv`）
- GPT-SoVITS 程式碼獨立複製一份到 `/root/GPT-SoVITS-wsl/`（`rsync` 排除
  `pretrained_models`／`finetuned_models`／`text/G2PWModel`），這三個大型
  模型目錄改用 symlink 指回 Windows 那邊 `/mnt/d/.../platform/GPT-SoVITS/`
  的既有檔案，不用重下載 5.8GB——**只有 code 是獨立的，模型權重是共用的**
- `torchaudio.load` 的 patch 只動這份 WSL 獨立複製，Windows production 用
  的 `platform/GPT-SoVITS/` 原封不動
- 測試腳本直接呼叫 `GPT_SoVITS.TTS_infer_pack.TTS.TTS` class（跟
  `api_v2.py` 內部用的是同一個類別），設定檔複製 production 的
  `tts_infer.yaml`「custom」段（`sakiko_clean` finetuned 模型），只把
  `device` 從 `cpu` 改成 `cuda`
