"""螢幕讀取主動搭話：背景迴圈、開關、詢問視窗、主動搭話觸發、遊戲資料查詢。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

import threading
import time
import tkinter as tk
from typing import Optional

from pet_config import (
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_BG,
    COLOR_PANEL,
    COLOR_TEXT,
    SCREEN_WATCH_INTERVAL_SEC,
)
from pet_prompts import _LOOKUP_SKIP_PROCESSES, _screen_watch_prompt
from pet_prefs import _save_prefs
from screen_watch import look_at_screen
import screen_watch
import web_lookup


class ScreenWatchMixin:
    """螢幕讀取主動搭話：背景迴圈、開關、詢問視窗、主動搭話觸發、遊戲資料查詢。"""

    # ============================================================
    # 螢幕讀取主動搭話（背景執行緒，詳見規劃文件 10.5）
    # ============================================================

    def _screen_watch_loop(self) -> None:
        """背景常駐迴圈，負責定期觸發「看螢幕、主動搭話」的行為。

        這個迴圈不管開關狀態如何都會啟動（這樣右鍵選單才能即時切換
        生效，不需要重新啟動整個迴圈），但只有 self._screen_watch_enabled
        為 True 的時候才會真的執行看螢幕的動作。

        用短間隔（_TICK_SEC）醒來檢查，而不是直接 sleep 整個
        SCREEN_WATCH_INTERVAL_SEC 那麼久，是為了讓「功能關閉中」的
        這段時間不會被計入下一次看螢幕的倒數——關閉期間會持續重置
        last_check，重新打開後才會開始重新倒數一輪完整的間隔，
        不會因為關閉了很久、一打開就馬上觸發。

        時間到了、但使用者正在忙（生成中）或正在打字（輸入框開著）：
        不會直接放棄這一輪，而是記住「還欠一次」（pending），之後每
        _TICK_SEC 繼續確認使用者有沒有空下來，一旦空下來就立刻補做——
        而且是補做的當下才真的截圖，抓的是那一刻最新的畫面，
        不是欠著的這段期間之前的舊畫面。
        """
        _TICK_SEC = 5
        last_check = time.monotonic()
        pending = False

        while True:
            time.sleep(_TICK_SEC)

            if not self._screen_watch_enabled:
                last_check = time.monotonic()
                pending = False
                continue

            if time.monotonic() - last_check >= SCREEN_WATCH_INTERVAL_SEC:
                pending = True

            if not pending:
                continue
            if self._busy or self._input_popup is not None:
                if time.monotonic() - last_check >= SCREEN_WATCH_INTERVAL_SEC + 60:
                    print(f"[screen_watch] 等了超過一分鐘還不能看：busy={self._busy}")
                    last_check = time.monotonic() - SCREEN_WATCH_INTERVAL_SEC
                continue

            last_check = time.monotonic()
            pending = False

            caption = look_at_screen()
            if not caption:
                continue

            look = dict(screen_watch.last_look)
            prompt = _screen_watch_prompt(caption, look, self._game_info_for(look))
            self.root.after(0, lambda p=prompt: self._trigger_proactive(p))

    def _on_game_mode_toggle(self) -> None:
        """右鍵選單「遊戲模式」：下一輪看螢幕就生效，並記住這次選擇。"""
        screen_watch.game_mode = self._game_mode_var.get()
        _save_prefs(game_mode=screen_watch.game_mode)

    def _on_screen_watch_toggle(self) -> None:
        """右鍵選單的開關被點擊：立刻改變執行中的行為。

        如果目前是「記住選擇」模式（一開始的詢問視窗有勾記住，或這台
        機器已經有記住的偏好），會順便更新記住的值，維持選單狀態跟
        下次啟動時的狀態一致；沒開記住模式的話，就只影響這次執行，
        不會寫入偏好檔案。
        """
        self._screen_watch_enabled = self._screen_watch_var.get()
        if self._remember_screen_watch:
            _save_prefs(screen_watch_enabled=self._screen_watch_enabled)

    def _prompt_screen_watch_choice(self) -> None:
        """程式啟動、還沒有「記住的」偏好時跳出來詢問一次，不需要去改 .env。

        貼在角色下方彈出，跟輸入列一樣，維持一致的視覺風格。
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("螢幕讀取主動搭話")
        dialog.configure(bg=COLOR_PANEL)
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.transient(self.root)

        msg = (
            "要不要讓她偶爾看一眼你的螢幕、自己決定要不要主動找你講話？\n"
            "（截圖只會送去本地看圖模型，有排除清單保護敏感視窗，細節見 README）"
        )
        tk.Label(
            dialog,
            text=msg,
            justify="left",
            wraplength=320,
            font=("Microsoft JhengHei", 11),
            fg=COLOR_TEXT,
            bg=COLOR_PANEL,
            padx=16,
            pady=12,
        ).pack()

        remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dialog,
            text="記住這個選擇，下次啟動不再詢問",
            variable=remember_var,
            font=("Microsoft JhengHei", 10),
            fg=COLOR_TEXT,
            bg=COLOR_PANEL,
            selectcolor=COLOR_BG,
            activebackground=COLOR_PANEL,
            activeforeground=COLOR_TEXT,
        ).pack(padx=16, pady=(0, 8), anchor="w")

        button_row = tk.Frame(dialog, bg=COLOR_PANEL)
        button_row.pack(padx=16, pady=(0, 14))

        def _choose(enabled: bool) -> None:
            self._screen_watch_enabled = enabled
            self._screen_watch_var.set(enabled)
            self._remember_screen_watch = remember_var.get()
            if self._remember_screen_watch:
                _save_prefs(screen_watch_enabled=enabled)
            dialog.destroy()

        tk.Button(
            button_row,
            text="開啟",
            command=lambda: _choose(True),
            bg=COLOR_ACCENT,
            fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT_HOVER,
            relief="flat",
            padx=14,
            pady=4,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            button_row,
            text="先不要",
            command=lambda: _choose(False),
            bg=COLOR_BG,
            fg=COLOR_TEXT,
            activebackground=COLOR_PANEL,
            relief="flat",
            padx=14,
            pady=4,
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", lambda: _choose(False))
        dialog.update_idletasks()
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.root.winfo_height() + 8
        dialog.geometry(f"+{x}+{y}")

    def _trigger_proactive(self, prompt_for_llm: str) -> None:
        """觸發一次「主動搭話」，流程跟 _submit 幾乎一樣。

        唯一差別是不會記一筆「你：...」的歷史紀錄——因為這句話不是
        使用者真的打的字，而是螢幕讀取觸發出來的內部提示詞。
        """
        if self._busy:
            return

        self._busy = True
        self._set_dialogue("...", is_status=True)
        # 主動搭話（看螢幕、Claude 通知）不做「說不確定就查」：那是她自己起的頭，不是回答主人的問題
        threading.Thread(target=self._handle_message, args=(prompt_for_llm, False), daemon=True).start()

    def _game_info_for(self, look: dict) -> Optional[str]:
        """前景是新的遊戲／程式（不在一般程式清單裡）時，在背景用視窗標題查一次資料。
        查到之前先回傳 None，不拖慢這次講話；查到後同一個程式之後都會帶上（記在 _game_info）。"""
        process = (look.get("process") or "").lower()
        if not process or process in _LOOKUP_SKIP_PROCESSES:
            return None
        if not hasattr(self, "_game_info"):
            self._game_info = {}
        if process not in self._game_info:
            self._game_info[process] = None  # 標記查詢中，避免重複開執行緒
            query = web_lookup.clean_title(look.get("title", "")) or process.removesuffix(".exe")

            def _lookup():
                self._game_info[process] = web_lookup.search(query)

            threading.Thread(target=_lookup, daemon=True).start()
        return self._game_info.get(process)
