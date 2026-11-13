"""背景執行緒音樂播放器（sounddevice 串流 + soundfile 解碼，不碰 Tk）。

soundfile 0.14（libsndfile 1.2）可解 mp3／wav／flac／ogg，不需要額外安裝套件。
邊讀邊播（不整首讀進記憶體）；每個區塊先套用 gain_fn() 的音量，角色說話時降低（ducking）。
"""

import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

from pet_playlist import Playlist

BLOCK_FRAMES = 4096  # 約 0.09 秒一個區塊；停止／下一首最慢這麼久內反應
DUCK_GAIN = 0.2  # 角色說話時的音樂音量比例


def duck_gain(speaking: bool) -> float:
    """目前該用的音樂音量比例：說話中降低，講完恢復。"""
    return DUCK_GAIN if speaking else 1.0


def apply_gain(block: np.ndarray, prev: float, cur: float) -> np.ndarray:
    """區塊內由 prev 線性變到 cur，避免音量突變的爆音。"""
    if prev == cur:
        return block if cur == 1.0 else block * cur
    ramp = np.linspace(prev, cur, len(block), dtype=np.float32)
    return block * (ramp[:, None] if block.ndim > 1 else ramp)


class MusicPlayer:
    def __init__(self, gain_fn=lambda: 1.0, on_ended=None):
        self._gain_fn = gain_fn
        self._on_ended = on_ended  # 執行緒自己結束（不是被 stop）時呼叫，在背景執行緒裡
        self._thread = None
        self._stop = threading.Event()
        self._skip = threading.Event()

    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def play(self, tracks) -> bool:
        """開始隨機播放 tracks；已經在播或清單是空的回傳 False。"""
        if self.is_playing() or not tracks:
            return False
        self._stop = threading.Event()
        self._skip = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(Playlist(tracks), self._stop, self._skip), daemon=True)
        self._thread.start()
        return True

    def next(self) -> None:
        if self.is_playing():
            self._skip.set()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=1.5)
        self._thread = None

    def _run(self, playlist, stop, skip) -> None:
        failures = 0
        gain = self._gain_fn()
        while not stop.is_set() and failures < len(playlist):
            path = playlist.next()
            skip.clear()
            try:
                gain = self._play_file(path, stop, skip, gain)
                failures = 0
            except Exception as exc:
                print(f"[music] 播放失敗，跳過 {path}：{exc!r}")
                failures += 1
        if not stop.is_set() and self._on_ended is not None:
            self._on_ended()

    def _play_file(self, path, stop, skip, gain) -> float:
        with sf.SoundFile(path) as f, sd.OutputStream(
                samplerate=f.samplerate, channels=f.channels, dtype="float32") as out:
            while not stop.is_set() and not skip.is_set():
                block = f.read(BLOCK_FRAMES, dtype="float32", always_2d=True)
                if len(block) == 0:
                    break
                cur = self._gain_fn()
                out.write(np.ascontiguousarray(apply_gain(block, gain, cur), dtype=np.float32))
                gain = cur
        return gain
