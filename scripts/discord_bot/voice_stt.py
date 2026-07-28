"""
語音輸入（STT）：接收使用者在語音頻道講的話，用 VAD 判斷一句話講完了沒，
轉成文字後丟給呼叫端（bot.py 接回既有的文字對話 pipeline）。

依賴 discord-ext-voice-recv（discord.py 官方不支援收語音，見專案規劃文件 10.2.2）。

已知限制：
- faster-whisper 在這台機器上是 CPU 執行——AMD 顯卡，CTranslate2 官方不支援 ROCm
  （見 TTS通用平台_專案規劃.md 十、10.2.2），中英日夾雜時的準確度用 medium 模型，
  不夠準再考慮換 large-v3（但更慢、更吃 CPU）。
- Whisper 的語言判斷是整段音訊選一種語言解碼，不是逐字切換，所以同一句話中英日
  夾雜時，非主要語言的詞有可能被誤譯音或翻錯，這是 Whisper 本身的限制。
- AudioSink.write() 是 discord-ext-voice-recv 背景執行緒呼叫的同步函式，不是
  coroutine，所以這裡用 asyncio.run_coroutine_threadsafe 把工作丟回事件迴圈。
"""
import asyncio
import audioop
import threading
import time
from typing import Awaitable, Callable, Optional

import discord
import numpy as np
import webrtcvad
from discord.ext import voice_recv
from faster_whisper import WhisperModel

SAMPLE_RATE = 48000  # discord 語音固定 48kHz
CHANNELS = 2  # discord 語音固定 stereo
SAMPLE_WIDTH = 2  # 16-bit

# webrtcvad 只吃剛好 10/20/30ms 長度的幀（單聲道、SAMPLE_WIDTH bytes/sample）。
# 實際收到的封包長度不一定剛好落在這幾種（FEC/PLC、封包合併等），長度對不上
# 的話就不做雜音過濾、直接當語音收下——之前版本嚴格要求長度剛好等於 20ms，
# 結果幾乎所有封包都被濾掉，變成「聽不到」、抓到的語音也七零八落
_VAD_FRAME_SIZES = {int(SAMPLE_RATE * ms / 1000) * SAMPLE_WIDTH for ms in (10, 20, 30)}

WHISPER_SAMPLE_RATE = 16000
WHISPER_MODEL_SIZE = "medium"  # 中英日夾雜辨識用；不夠準可以換成 "large-v3"（更慢）

SILENCE_MS_TO_FLUSH = 800  # 這麼久沒收到語音封包，視為一句話講完了
MIN_UTTERANCE_MS = 400  # 太短的雜音（誤觸 VAD）直接丟棄不轉文字
CHECK_INTERVAL_SEC = 0.25  # 定時檢查「有沒有人講完話」的間隔

OnUtterance = Callable[[discord.Member, str], Awaitable[None]]

_whisper_model: Optional[WhisperModel] = None


def _get_whisper_model() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        print(f"[voice_stt] 載入 faster-whisper（{WHISPER_MODEL_SIZE}, CPU）中...")
        _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        print("[voice_stt] faster-whisper 載入完成")
    return _whisper_model


class _UserBuffer:
    __slots__ = ("frames", "last_packet_time")

    def __init__(self):
        self.frames: list[bytes] = []
        self.last_packet_time = 0.0


class TranscribingSink(voice_recv.AudioSink):
    """收語音、用 VAD 判斷斷句，斷句後轉文字並呼叫 on_utterance(member, text)"""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_utterance: OnUtterance):
        super().__init__()
        self._loop = loop
        self._on_utterance = on_utterance
        self._vad = webrtcvad.Vad(2)  # 0~3，數字越大越嚴格（越不容易把雜音當語音）
        self._lock = threading.Lock()
        self._buffers: dict[int, _UserBuffer] = {}
        self._members: dict[int, discord.Member] = {}
        self._checker_task: Optional[asyncio.Task] = None
        self._closed = False

    def wants_opus(self) -> bool:
        return False  # 拿解碼後的 PCM，不是原始 opus

    def write(self, user: Optional[discord.Member], data: "voice_recv.VoiceData") -> None:
        # 注意：這個函式在 discord-ext-voice-recv 的背景執行緒被呼叫，不是事件迴圈
        if user is None or user.bot:
            return
        pcm = data.pcm
        if not pcm:
            return

        try:
            mono = audioop.tomono(pcm, SAMPLE_WIDTH, 0.5, 0.5)
        except audioop.error:
            return

        # 只有長度剛好是 VAD 支援的幀長才做雜音過濾；長度不符就不過濾，寧可多收
        # 一點雜音，也不要把使用者實際講的話誤丟掉
        if len(mono) in _VAD_FRAME_SIZES:
            try:
                if not self._vad.is_speech(mono, SAMPLE_RATE):
                    return
            except Exception:
                pass

        with self._lock:
            buf = self._buffers.get(user.id)
            if buf is None:
                buf = _UserBuffer()
                self._buffers[user.id] = buf
                self._members[user.id] = user
            buf.frames.append(mono)
            buf.last_packet_time = time.monotonic()

    def start(self) -> None:
        self._checker_task = asyncio.run_coroutine_threadsafe(self._check_loop(), self._loop)

    def cleanup(self) -> None:
        self._closed = True
        if self._checker_task is not None:
            self._loop.call_soon_threadsafe(self._checker_task.cancel)

    async def _check_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(CHECK_INTERVAL_SEC)
                self._dispatch_finished_utterances()
        except asyncio.CancelledError:
            pass

    def _dispatch_finished_utterances(self) -> None:
        now = time.monotonic()
        finished: list[tuple[discord.Member, bytes]] = []
        with self._lock:
            for user_id, buf in list(self._buffers.items()):
                if now - buf.last_packet_time >= SILENCE_MS_TO_FLUSH / 1000:
                    del self._buffers[user_id]
                    total_bytes = sum(len(f) for f in buf.frames)
                    duration_ms = total_bytes / (SAMPLE_RATE * SAMPLE_WIDTH) * 1000
                    if duration_ms >= MIN_UTTERANCE_MS:
                        finished.append((self._members[user_id], b"".join(buf.frames)))

        for member, mono_48k in finished:
            asyncio.run_coroutine_threadsafe(self._transcribe_and_dispatch(member, mono_48k), self._loop)

    async def _transcribe_and_dispatch(self, member: discord.Member, mono_48k: bytes) -> None:
        try:
            text = await asyncio.to_thread(self._transcribe, mono_48k)
        except Exception as e:
            print(f"[voice_stt] 轉文字失敗：{e}")
            return
        text = text.strip()
        if text:
            await self._on_utterance(member, text)

    def _transcribe(self, mono_48k: bytes) -> str:
        mono_16k, _ = audioop.ratecv(mono_48k, SAMPLE_WIDTH, 1, SAMPLE_RATE, WHISPER_SAMPLE_RATE, None)
        audio = np.frombuffer(mono_16k, dtype=np.int16).astype(np.float32) / 32768.0
        model = _get_whisper_model()
        # language=None：自動判斷語言。Whisper 是整段選一種語言解碼，中英日夾雜
        # 時非主要語言的部分可能會被誤譯音，這是模型本身的限制（見檔案開頭說明）
        segments, _ = model.transcribe(audio, language=None, vad_filter=False)
        return "".join(segment.text for segment in segments)


def start_listening(voice_client: "voice_recv.VoiceRecvClient", on_utterance: OnUtterance) -> None:
    """在已連進語音頻道的 voice_client 上開始聽音辨識。重複呼叫前要先 stop_listening。"""
    loop = asyncio.get_running_loop()
    sink = TranscribingSink(loop, on_utterance)
    voice_client.listen(sink)
    sink.start()


def stop_listening(voice_client: "voice_recv.VoiceRecvClient") -> None:
    if voice_client.is_listening():
        voice_client.stop_listening()
