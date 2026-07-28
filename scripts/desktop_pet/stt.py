"""
Push-to-talk 語音輸入：按住熱鍵錄音，放開後轉成文字，**塞進輸入框讓你確認/修改
後自己按 Enter 送出**，不會自動幫你送出——這是刻意的設計，解決 Discord 版語音
輸入被擱置的那個問題（聽錯了會直接餵給 LLM，使用者完全無法事先確認）。

預設轉文字用 SenseVoice（FunAudioLLM/SenseVoiceSmall，本地跑），中英日夾雜辨識
準確度比 faster-whisper 好很多，CPU 上也比 Whisper 快很多（非自回歸架構，一次
解碼整段），適合這台沒有可用 GPU 跑語音模型的機器。模型只在第一次真的按熱鍵用到
時才載入，不在程式啟動時就佔記憶體——這台機器記憶體本來就偏緊（見專案規劃文件），
能不常駐就不常駐。

想改用雲端語音辨識，在 .env 裡設 `STT_PROVIDER=openai`（預設是 local，不寫這行
就跟以前一樣）並填 `OPENAI_API_KEY`——沒明確選 openai 的話，就算環境裡剛好有這個
key 也不會被拿去用，不是「偵測到就自動切」。
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

SENSEVOICE_MODEL = "iic/SenseVoiceSmall"
RECORD_SAMPLE_RATE = 16000  # SenseVoice / Whisper 都吃 16kHz 單聲道
MIN_DURATION_SEC = 0.3  # 太短的錄音（誤按）直接丟棄，不轉文字

# 要選了才會切，不是偵測到 OPENAI_API_KEY 存在就自動切走
STT_PROVIDER = os.environ.get("STT_PROVIDER", "local").strip().lower()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_STT_MODEL = os.environ.get("OPENAI_STT_MODEL", "whisper-1")
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"

_model: Optional[AutoModel] = None
_model_lock = threading.Lock()


def _resolve_input_device() -> Optional[int]:
    """挑錄音用的麥克風。如果系統預設錄音裝置剛好是 CABLE Output（通常是設定
    VTube Studio 對嘴輸入時，不小心把系統預設錄音裝置也改掉了），錄到的會是
    虛擬音效卡的空氣，不是你的聲音——這裡自動避開，改選第一個看起來像真麥克風
    的裝置，但最好還是手動把 Windows 的預設錄音裝置換回真麥克風。"""
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
    global _model
    with _model_lock:
        if _model is None:
            print("[stt] 載入 SenseVoice（第一次用到，可能需要下載模型，稍等）...")
            _model = AutoModel(
                model=SENSEVOICE_MODEL, device="cpu", disable_update=True, disable_pbar=True
            )
            print("[stt] SenseVoice 載入完成")
    return _model


def _build_multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes, content_type: str):
    """組 multipart/form-data 的 request body，純標準函式庫做，不另外引入 requests。"""
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
    """audio：16kHz、單聲道、float32 numpy array"""
    if STT_PROVIDER == "openai":
        print(f"[stt] 語音辨識走雲端 OpenAI（{OPENAI_STT_MODEL}）")
        return _transcribe_openai(audio)
    print("[stt] 語音辨識走本地 SenseVoice")
    model = _get_model()
    results = model.generate(input=audio, language="auto", use_itn=True, cache={})
    if not results:
        return ""
    return rich_transcription_postprocess(results[0]["text"]).strip()


class PushToTalkRecorder:
    """按住開始錄音、放開停止；停止後在背景執行緒轉文字，完成才呼叫 on_transcribed。"""

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
            return  # 太短，大概是誤按

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
        with self._lock:
            if self._recording:
                self._frames.append(indata.copy())
