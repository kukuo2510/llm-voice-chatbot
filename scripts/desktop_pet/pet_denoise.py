"""合成語音播放前的降噪。

背景（2026-11-10）：GPT-SoVITS 合成出來的聲音帶有明顯底噪、聽起來不乾淨。
試聽比較過 noisereduce 的兩種強度：
- 輕度（stationary=True、prop_decrease=0.6）：只壓固定底噪，反而聽起來悶、不清楚
- 強度（stationary=False、prop_decrease=0.9）：連變動的雜音也壓，聲音夠大也清楚
使用者選了強度，所以預設用這組參數。3 秒的音檔在 CPU 上約 30ms，不影響反應速度。

設定：.env 或環境變數 PET_DENOISE=0 可以關掉（預設開）。
沒裝 noisereduce、或降噪出錯時，一律改播原音，不讓桌寵因為降噪就不出聲。
"""

import os

import numpy as np

try:
    import noisereduce as _nr
except ImportError:  # 沒裝套件就當作沒有降噪功能
    _nr = None

# 使用者試聽後選定的強度參數
DENOISE_PARAMS = {"stationary": False, "prop_decrease": 0.9}


def denoise_enabled() -> bool:
    """每次呼叫時才讀環境變數，.env 載入時機早晚都不影響。"""
    return _nr is not None and os.environ.get("PET_DENOISE", "1") != "0"


def denoise(data: np.ndarray, samplerate: int) -> np.ndarray:
    """回傳降噪後的音訊（float32、形狀和輸入相同）；關閉或失敗時原樣回傳。"""
    if not denoise_enabled():
        return data
    try:
        if data.ndim == 1:
            out = _nr.reduce_noise(y=data, sr=samplerate, **DENOISE_PARAMS)
        else:
            # soundfile 讀進來的多聲道是 (樣本數, 聲道)，noisereduce 要 (聲道, 樣本數)
            out = _nr.reduce_noise(y=data.T, sr=samplerate, **DENOISE_PARAMS).T
        return np.ascontiguousarray(out, dtype=np.float32)
    except Exception as e:
        print(f"[denoise] 降噪失敗，改播原音：{e}")
        return data
