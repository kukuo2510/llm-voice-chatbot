"""右鍵「鋼琴播放器」子選單：選資料夾、播放、下一首、停止，播放時角色彈琴。

播放器本身在 pet_music_player（背景執行緒），清單邏輯在 pet_playlist；這裡只接選單與動畫。
資料夾記在偏好檔 "music_folder"。角色說話時音樂降到 DUCK_GAIN，講完恢復（見 pet_music_player）。
"""

import tkinter as tk
from tkinter import filedialog

from pet_config import COLOR_ACCENT, COLOR_PANEL, COLOR_TEXT
from pet_music_player import MusicPlayer, duck_gain
from pet_playlist import scan_folder
from pet_prefs import _load_prefs, _save_prefs

PIANO_SEQUENCE = "piano"
MENU_PLAY, MENU_NEXT, MENU_STOP = 2, 3, 4  # 選單項目索引（0 選資料夾、1 分隔線）


class MusicMixin:
    """鋼琴播放器。"""

    _music_player = None
    _music_menu = None

    def _build_music_menu(self, parent) -> None:
        menu = tk.Menu(parent, tearoff=False, bg=COLOR_PANEL, fg=COLOR_TEXT,
                       activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
                       postcommand=self._refresh_music_menu)
        menu.add_command(label="選擇音樂資料夾…", command=self._on_music_choose_folder)
        menu.add_separator()
        menu.add_command(label="播放", command=self._on_music_play)
        menu.add_command(label="下一首", command=self._on_music_next)
        menu.add_command(label="停止", command=self._on_music_stop)
        self._music_menu = menu
        parent.add_cascade(label="鋼琴播放器", menu=menu)

    def _music_folder(self):
        return _load_prefs().get("music_folder")

    def _music_get_player(self) -> MusicPlayer:
        if self._music_player is None:
            self._music_player = MusicPlayer(
                gain_fn=lambda: duck_gain(bool(getattr(self, "_speaking", False))),
                on_ended=lambda: self.root.after(0, self._on_music_ended))
        return self._music_player

    def _music_playing(self) -> bool:
        return self._music_player is not None and self._music_player.is_playing()

    def _refresh_music_menu(self) -> None:
        playing = self._music_playing()
        has_folder = bool(self._music_folder())
        self._music_menu.entryconfigure(MENU_PLAY, state="normal" if has_folder and not playing else "disabled")
        self._music_menu.entryconfigure(MENU_NEXT, state="normal" if playing else "disabled")
        self._music_menu.entryconfigure(MENU_STOP, state="normal" if playing else "disabled")

    def _music_say(self, text: str) -> None:
        self._set_dialogue(text, is_status=True)
        self.root.after(3000, self._clear_status)

    def _on_music_choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="選擇音樂資料夾", initialdir=self._music_folder() or None)
        if not folder:
            return
        _save_prefs(music_folder=folder)
        if self._music_playing():
            self._on_music_stop()
            self._on_music_play()

    def _on_music_play(self) -> None:
        if self._music_playing():
            return
        tracks = scan_folder(self._music_folder())
        if not tracks:
            self._music_say("（音樂資料夾是空的，或還沒選）")
            return
        if self._music_get_player().play(tracks) and self._anim_ctrl is not None:
            self._anim_ctrl.start(PIANO_SEQUENCE)  # 沒定義 piano 時 start 回傳 False，只播音樂

    def _on_music_next(self) -> None:
        if self._music_player is not None:
            self._music_player.next()

    def _on_music_stop(self) -> None:
        if self._music_player is not None:
            self._music_player.stop()
        self._music_stop_anim()

    def _on_music_ended(self) -> None:
        """播放執行緒自己結束（例如整個資料夾都播不出來）。"""
        if not self._music_playing():
            self._music_stop_anim()

    def _music_stop_anim(self) -> None:
        if self._anim_ctrl is not None:
            self._anim_ctrl.stop(PIANO_SEQUENCE)

    def _music_shutdown(self) -> None:
        """結束桌寵時停掉音樂（不動動畫）。"""
        if self._music_player is not None:
            self._music_player.stop()
