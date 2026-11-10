"""播放生成語音、並依音量算出立繪開合嘴的節奏。

從 desktop_pet.py 原封不動搬出來的（含原本 CharacterWindow 裡的 _produce_audio，
類別上保留同名的 staticmethod 當別名）。
"""

import queue
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from pet_config import (CABLE_DEVICE_NAME_HINT, MOUTH_FLAP_PATTERN, MOUTH_HOP_MS,
                        MOUTH_THRESHOLD_RATIO, MOUTH_VOICE_RATIO)
from tts_infer import generate
from speech_lang import text_lang_for
import chat
import livliv_link
from pet_denoise import denoise


def _mouth_envelope(data: np.ndarray, samplerate: int, hop_ms: int = MOUTH_HOP_MS) -> list:
    """把音檔切成一段一段（每段 hop_ms 長），算出每段的音量，回傳每段
    「嘴巴該不該張開」的 bool list。

    這只是「音量夠大就張嘴」的陽春版本，不是真的音素對齊——真正的音素
    對齊需要 TTS 額外吐出逐字時間戳，詳見規劃文件 10.4 的討論。
    """
    mono = data.mean(axis=1) if data.ndim > 1 else data
    hop = max(1, int(samplerate * hop_ms / 1000))

    levels = [
        float(np.sqrt(np.mean(mono[i:i + hop] ** 2)))
        for i in range(0, len(mono), hop)
    ]

    peak = max(levels) if levels else 0.0
    if peak <= 1e-6:
        return [False] * len(levels)

    # 有在發聲的段落照固定節奏開合；每段發聲重新從「開」開始，聲音一出來嘴就張開。
    out = []
    run = 0
    for level in levels:
        if level > peak * MOUTH_VOICE_RATIO:
            out.append(MOUTH_FLAP_PATTERN[run % len(MOUTH_FLAP_PATTERN)])
            run += 1
        else:
            out.append(False)
            run = 0
    return out


def _find_cable_input_device() -> Optional[int]:
    """找出虛擬音效卡（CABLE Input）的播放端裝置編號。

    同名裝置在不同 host API（MME/DirectSound/WASAPI）之下，可能會重複
    出現好幾筆。這裡優先挑選跟 sounddevice 目前預設 host API 一致的
    那一筆，行為會比較穩定；如果都對不上，就退而求其次隨便挑第一筆。
    """
    devices = sd.query_devices()
    default_hostapi = sd.default.hostapi

    candidates = [
        i
        for i, d in enumerate(devices)
        if CABLE_DEVICE_NAME_HINT in d["name"] and d["max_output_channels"] > 0
    ]

    for i in candidates:
        if devices[i]["hostapi"] == default_hostapi:
            return i

    return candidates[0] if candidates else None


def _play_on_device(data: np.ndarray, samplerate: int, device_index: int) -> None:
    """把音訊資料播放到指定的裝置編號上。"""
    channels = data.shape[1] if data.ndim > 1 else 1
    with sd.OutputStream(device=device_index, samplerate=samplerate, channels=channels) as stream:
        stream.write(data.astype(np.float32))


def play_for_avatar(data: np.ndarray, samplerate: int, on_mouth=None) -> None:
    """播放生成的語音：一份播給使用者聽，一份播給虛擬音效卡對嘴用。

    播放目標最多兩個：系統目前的預設輸出裝置（給使用者聽），以及虛擬
    音效卡的 CABLE Input（給 VTube Studio 對嘴用），兩份會同時播放。

    如果有提供 on_mouth callback（簽名為 (is_open: bool) -> None），
    會在另一條獨立的執行緒上，依照音量節奏呼叫它，讓桌寵視窗裡的立繪
    跟著開合嘴——這條「嘴巴開合」的時鐘是跟播放本身各自獨立計算的，
    兩邊都執行完畢後，這支函式才會返回。
    """
    targets: list[int] = []

    default_output = sd.default.device[1]
    if default_output is not None:
        targets.append(default_output)

    cable_index = _find_cable_input_device()
    if cable_index is None:
        print("[desktop_pet] 找不到 CABLE Input 裝置，只會播給你聽，VTube Studio 不會對嘴")
    elif cable_index != default_output:
        targets.append(cable_index)

    threads = [
        threading.Thread(target=_play_on_device, args=(data, samplerate, idx))
        for idx in targets
    ]

    mouth_thread = None
    if on_mouth is not None:
        envelope = _mouth_envelope(data, samplerate)
        hop_seconds = MOUTH_HOP_MS / 1000

        def _run_mouth_clock():
            # 用絕對時間點（而不是累加 sleep）計算每一段該在什麼時候觸發，
            # 避免每段呼叫 on_mouth 本身的微小耗時累積誤差、讓嘴型節奏
            # 隨著音訊長度越拖越慢。
            start = time.monotonic()
            for i, is_open in enumerate(envelope):
                delay = start + i * hop_seconds - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                on_mouth(is_open)
            on_mouth(False)

        mouth_thread = threading.Thread(target=_run_mouth_clock)

    livliv_link.send_clip(data, samplerate)
    for t in threads:
        t.start()
    if mouth_thread:
        mouth_thread.start()

    for t in threads:
        t.join()
    if mouth_thread:
        mouth_thread.join()


def _produce_audio(chunks: list, tones: list, audio_queue: "queue.Queue[tuple]") -> None:
    """背景執行緒：一段一段合成語音，合成好的音訊資料依序丟進 audio_queue。

    放進佇列的是「已經讀進記憶體的音訊資料」而不是檔案路徑——
    tts_infer 只會保留最新幾個輸出檔，段落一多，還沒播到的檔案
    有可能先被清掉，先讀進記憶體就沒有這個問題。

    佇列裡的訊息：("audio", (data, samplerate))、("error", 例外)、("end", None)。
    某一段合成失敗就停止，後面的段落不再合成。
    """
    # 依「角色說話語言」明確指定合成語言，不讓 GPT-SoVITS 自動猜（見 speech_lang.py）。
    # 每次開始講話時才讀，右鍵切換語言後下一句就會套用。
    text_lang = text_lang_for(chat.CHARACTER_SPEECH_LANGUAGE)
    for chunk, tone in zip(chunks, tones):
        try:
            path = generate(chunk, None, tone, text_lang=text_lang)
            data, samplerate = sf.read(str(path), dtype="float32")
            # 播放前先降噪（見 pet_denoise.py）；嘴型和舞台對嘴也用降噪後的音訊
            data = denoise(data, samplerate)
        except Exception as e:
            audio_queue.put(("error", e))
            return
        audio_queue.put(("audio", (data, samplerate)))
    audio_queue.put(("end", None))
