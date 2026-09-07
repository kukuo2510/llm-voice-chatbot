"""Push-to-talk（按住說話）語音輸入功能。

## 操作流程與設計理念

按住熱鍵開始錄音，放開熱鍵後把錄音轉成文字，**接著把辨識結果塞進輸入框
讓使用者自己確認／修改後、再按 Enter 送出**，並不會自動幫使用者送出。

這是刻意的設計：用來解決先前 Discord 版本語音輸入被擱置的那個問題——
如果語音辨識聽錯了，內容會直接被送去餵給 LLM，使用者完全沒有機會
事先發現並修正錯誤。改成「先確認再送出」之後，就不會有這個風險。

## 為什麼預設用 SenseVoice 做本地辨識

預設轉文字使用 SenseVoice（FunAudioLLM/SenseVoiceSmall），在本地執行。

選它的原因：
- 中英日夾雜的辨識準確度比 faster-whisper 好很多。
- 在 CPU 上執行也比 Whisper 快很多——因為 SenseVoice 是非自回歸架構，
  一次性解碼整段語音，不像自回歸模型要逐字生成，這對「這台機器沒有
  可用 GPU 能拿來跑語音模型」的情境特別重要。

模型只有在第一次真的按下熱鍵、實際用到辨識功能時才會載入，並不會在
程式一啟動就把模型載進記憶體常駐——因為這台機器的記憶體本來就偏緊
（詳見專案規劃文件），能不常駐佔記憶體的東西就盡量不要常駐。

## 雲端語音辨識（選用）

想改用雲端語音辨識，要在 .env 裡明確設定 `STT_PROVIDER=openai`
（預設值是 "local"，不寫這行的話行為跟以前完全一樣）並填好
`OPENAI_API_KEY`。

跟 chat.py 的 LLM_PROVIDER 是同樣的設計原則：一定要「明確選擇」openai
才會切過去——就算環境變數裡剛好已經有 OPENAI_API_KEY，只要沒把
STT_PROVIDER 設成 openai，也不會被拿去使用。
"""

import io
import json
import os
import sys
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf
import sounddevice as sd
from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.env import load_dotenv  # noqa: E402

load_dotenv()


# ============================================================
# 環境變數 / 設定值
# ============================================================

SENSEVOICE_MODEL = "iic/SenseVoiceSmall"

# SenseVoice 跟 Whisper 系列模型都是吃 16kHz 單聲道的音訊輸入。
RECORD_SAMPLE_RATE = 16000

# 太短的錄音多半是不小心誤按熱鍵造成的，直接丟棄、不送去做語音辨識，
# 避免浪費運算資源，也避免辨識出沒有意義的雜訊文字。
MIN_DURATION_SEC = 0.3

# 只有明確設定 STT_PROVIDER=openai 才會切去雲端，不是偵測到
# OPENAI_API_KEY 存在就自動切走（詳見檔案最上面的說明）。
STT_PROVIDER = os.environ.get("STT_PROVIDER", "local").strip().lower()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_STT_MODEL = os.environ.get("OPENAI_STT_MODEL", "whisper-1")
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"

# SenseVoice 模型物件用全域變數延遲載入 + 快取，並用 lock 保護，
# 避免多執行緒同時觸發載入而重複載入好幾份模型。
_model: Optional[AutoModel] = None
_model_lock = threading.Lock()


def _resolve_input_device() -> Optional[int]:
    """挑選要拿來錄音的麥克風裝置。

    背景問題：如果系統目前的預設錄音裝置剛好是「CABLE Output」
    （這通常是設定 VTube Studio 對嘴輸入時，不小心把系統預設錄音裝置
    也一併改掉了造成的），實際錄到的內容會是虛擬音效卡裡的空氣，
    完全錄不到使用者的聲音。

    這裡會自動偵測並避開這種情況，改選第一個「看起來像真麥克風」的
    裝置。但這只是應急處理，最理想的做法還是使用者自己手動把 Windows
    系統的預設錄音裝置換回真正的麥克風。
    """
    devices = sd.query_devices()
    default_idx = sd.default.device[0]

    if default_idx is not None and default_idx >= 0:
        name = devices[default_idx]["name"]
        if "CABLE" not in name:
            return default_idx
        print(f"[stt] 系統預設錄音裝置是「{name}」，不是麥克風，改找別的裝置")

    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and "CABLE" not in d["name"]:
            print(f"[stt] 改用：{d['name']}")
            return i

    return default_idx


def _get_model() -> AutoModel:
    """取得（必要時載入）SenseVoice 模型物件，並確保只會載入一次。"""
    global _model
    with _model_lock:
        if _model is None:
            print("[stt] 載入 SenseVoice（第一次用到，可能需要下載模型，稍等）...")
            _model = AutoModel(
                model=SENSEVOICE_MODEL,
                device="cpu",
                disable_update=True,
                disable_pbar=True,
            )
            print("[stt] SenseVoice 載入完成")
    return _model


# ============================================================
# 雲端語音辨識（OpenAI）
# ============================================================


def _build_multipart(
    fields: dict,
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
):
    """組出 multipart/form-data 格式的 request body。

    刻意純用標準函式庫手動組字串，不另外引入 requests 這類第三方套件，
    減少這支腳本的依賴數量。
    """
    boundary = uuid.uuid4().hex
    parts = []

    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )

    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'.encode()
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())

    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _transcribe_openai(audio: np.ndarray) -> str:
    """把錄音送去 OpenAI 的語音辨識 API，回傳辨識出的文字。"""
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "STT_PROVIDER=openai 但沒有設定 OPENAI_API_KEY，"
            "請在 .env 裡填入你自己的 OpenAI API key。"
        )

    buf = io.BytesIO()
    sf.write(buf, audio, RECORD_SAMPLE_RATE, format="WAV")

    body, content_type = _build_multipart(
        fields={"model": OPENAI_STT_MODEL},
        file_field="file",
        filename="audio.wav",
        file_bytes=buf.getvalue(),
        content_type="audio/wav",
    )

    req = urllib.request.Request(
        OPENAI_TRANSCRIBE_URL,
        data=body,
        headers={
            "Content-Type": content_type,
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI 語音辨識回傳錯誤（HTTP {e.code}）：{body_text}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"連不上 OpenAI API，檢查網路連線：{e}") from e

    return data.get("text", "").strip()


def transcribe(audio: np.ndarray) -> str:
    """把錄音轉成文字，依 STT_PROVIDER 分流走本地 SenseVoice 或雲端 OpenAI。

    audio 參數必須是 16kHz、單聲道、float32 的 numpy array。
    """
    if STT_PROVIDER == "openai":
        print(f"[stt] 語音辨識走雲端 OpenAI（{OPENAI_STT_MODEL}）")
        return _transcribe_openai(audio)

    print("[stt] 語音辨識走本地 SenseVoice")
    model = _get_model()
    results = model.generate(input=audio, language="auto", use_itn=True, cache={})

    if not results:
        return ""

    return rich_transcription_postprocess(results[0]["text"]).strip()


# ============================================================
# Push-to-talk 錄音器
# ============================================================


class PushToTalkRecorder:
    """按住開始錄音、放開停止；停止後在背景執行緒轉文字，完成才呼叫 on_transcribed。

    採用背景執行緒做語音辨識的原因：辨識本身可能需要一段時間
    （尤其是第一次要載入模型時），如果同步阻塞著呼叫端，UI 畫面
    會整個卡住，體感非常差。
    """

    def __init__(
        self,
        on_transcribed: Callable[[str], None],
        on_state_change: Optional[Callable[[bool], None]] = None,
        device: Optional[int] = None,
    ):
        self._on_transcribed = on_transcribed
        self._on_state_change = on_state_change or (lambda recording: None)
        self._device = device if device is not None else _resolve_input_device()
        self._stream: Optional[sd.InputStream] = None
        self._frames: list = []
        self._lock = threading.Lock()
        self._recording = False

    def start(self) -> None:
        """開始錄音（重複呼叫時，如果已經在錄音中則直接忽略）。"""
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._frames = []
            self._stream = sd.InputStream(
                samplerate=RECORD_SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=self._device,
                callback=self._callback,
            )
            self._stream.start()

        self._on_state_change(True)

    def stop(self) -> None:
        """停止錄音，並在背景執行緒把錄到的內容轉成文字。

        如果目前根本沒有在錄音中，直接忽略這次呼叫；如果錄音長度
        短於 MIN_DURATION_SEC（大概率是誤按熱鍵），同樣直接放棄、
        不進行語音辨識。
        """
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            stream, self._stream = self._stream, None
            frames, self._frames = self._frames, []

        if stream is not None:
            stream.stop()
            stream.close()

        self._on_state_change(False)

        if not frames:
            return

        audio = np.concatenate(frames, axis=0).flatten()
        if len(audio) / RECORD_SAMPLE_RATE < MIN_DURATION_SEC:
            # 錄音時間太短，大概是誤按，直接放棄這次錄音。
            return

        def _run():
            try:
                text = transcribe(audio)
            except Exception as e:
                print(f"[stt] 轉文字失敗：{e}")
                return
            if text:
                self._on_transcribed(text)

        threading.Thread(target=_run, daemon=True).start()

    def _callback(self, indata, frames, time_info, status) -> None:
        """sounddevice 的錄音回呼函式，每收到一批音訊資料就呼叫一次。"""
        with self._lock:
            if self._recording:
                self._frames.append(indata.copy())
