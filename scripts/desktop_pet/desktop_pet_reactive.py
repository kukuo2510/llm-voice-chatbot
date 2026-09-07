"""二代目桌寵（互動反應型）：跟 desktop_pet.py（Sakiko，深度對話型）並存的
第二個角色，不需要 LLM 人設／TTS 配音／STT 語音輸入，走輕量的「觸發→反應」
路線——點擊/拖曳角色、桌面刪除檔案都會觸發台詞+動畫反應。

跟 desktop_pet.py 完全獨立的 process，不 import chat.py / tts_infer.py /
stt.py，所以不需要 Ollama、GPT-SoVITS 推理服務在跑，兩支可以同時開著互不
干擾。共用的角色無關邏輯（去背視窗、立繪載入/縮放、螢幕擷取排除）見
pet_shell.py 的說明；重力/落地/自主漫遊的手感參數跟 desktop_pet.py 保持一致。

角色本身還沒定案（人設/語音/立繪都還沒做），這支先把「點擊/拖曳觸發反應」
「桌面刪檔觸發反應」這套機制搭起來、可以實際跑跑看——立繪暫時借用
characters/sakiko/avatar/ 底下的兩張圖當佔位（見下面 ASSETS_DIR 註解），
台詞也是隨手寫的佔位文字，之後角色定案了，只要換 ASSETS_DIR 指向新角色
資料夾、把 REACTIONS 裡的台詞換掉即可，不用動其他邏輯。

互動方式：
    - 點一下角色：從 REACTIONS["click"] 隨機挑一句台詞，播一小段反應動畫
    - 按住角色拖曳：移動視窗位置（跟點一下用有沒有移動位置分辨，不是分開
      的操作，邏輯照抄 desktop_pet.py）
    - 桌面（%USERPROFILE%\\Desktop）刪除檔案：背景執行緒每隔幾秒比對一次
      檔名快照，少了檔案就從 REACTIONS["eat_file"] 挑一句「吃掉」台詞
      （只監控這一個資料夾，不碰資源回收筒；如果 Desktop 被 OneDrive
      重新導向到別的路徑，這裡不會自動偵測，需要的話手動改
      DESKTOP_WATCH_DIR）
    - 右鍵角色：彈出選單（重力開關、結束）
    - 重力/落地（見 desktop_physics.py）：跟 Sakiko 那支手感一致

用法：
    C:\\miniconda3\\envs\\tts-platform\\python.exe scripts\\desktop_pet\\desktop_pet_reactive.py
"""
import os
import random
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_physics  # noqa: E402
from pet_shell import (  # noqa: E402
    MAGIC_TRANSPARENT_COLOR,
    exclude_self_from_screen_capture,
    load_avatar_frames,
)

# 跟 Sakiko 那支同一套氛圍色，維持視覺一致
COLOR_PANEL = "#241a30"
COLOR_ACCENT = "#7c4dbd"
COLOR_ACCENT_HOVER = "#5e3690"
COLOR_TEXT = "#eee6f5"

# 立繪素材：先借用 Sakiko 的兩張圖當佔位（見檔案開頭說明），二代目角色定案、
# 立繪做好之後，把這行換成 characters/<新角色>/avatar 即可
ASSETS_DIR = Path(__file__).resolve().parents[2] / "characters" / "sakiko" / "avatar"
AVATAR_DISPLAY_WIDTH = int(os.environ.get("DESKTOP_PET_REACTIVE_AVATAR_WIDTH", "220"))
AVATAR_MIN_WIDTH = 120
AVATAR_MAX_WIDTH = 600
AVATAR_SCROLL_STEP = 20
DRAG_THRESHOLD_PX = 3  # 按下去移動超過這個距離才算拖曳，不然當成單純點擊

# 桌寵物理（重力／落地／自主漫遊），數值跟 desktop_pet.py 保持一致手感
PHYSICS_TICK_MS = 33
GRAVITY_PX_S2 = 2200.0
FALL_TERMINAL_VELOCITY_PX_S = 1400.0
WANDER_SPEED_PX_S = 110.0
WANDER_MIN_INTERVAL_SEC = 5.0
WANDER_MAX_INTERVAL_SEC = 15.0
WANDER_MIN_DURATION_SEC = 1.0
WANDER_MAX_DURATION_SEC = 3.0

# 反應動畫：借用嘴巴開合的兩張圖快速切換幾下，模擬「有反應」的感覺，之後
# 有專屬反應立繪（例如嚼東西的動作）再換掉這個佔位做法
REACTION_BLINK_COUNT = 3
REACTION_BLINK_INTERVAL_MS = 120
REACTION_DIALOGUE_HOLD_SEC = 2.5  # 反應台詞顯示這麼久之後自動收掉

# 台詞是佔位用的隨手文字，角色人設定案後直接換掉這個 dict 就好（之後也可以
# 搬進 characters/<新角色>/reactions.py，現在先不過度設計成外部設定檔）
REACTIONS: dict[str, list[str]] = {
    "click": [
        "幹嘛戳我啦。",
        "嗯？找我什麼事？",
        "再戳我要生氣了喔。",
        "拖我去別的地方也可以啦。",
    ],
    "eat_file": [
        "{filename}...看起來不錯，吃掉了！",
        "咦，桌面上的 {filename} 不見了，是我吃的沒錯。",
        "{filename} 消化完畢。",
    ],
}

DESKTOP_WATCH_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
DESKTOP_WATCH_INTERVAL_SEC = 3


class ReactivePetWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._dragged = False
        self._drag_origin = (0, 0)
        self._win_origin = (0, 0)
        self._facing = "right"
        self._avatar_width = AVATAR_DISPLAY_WIDTH
        self._pending_avatar_width = self._avatar_width
        self._avatar_resize_job: Optional[str] = None

        self.frame_closed, self.frame_open, avatar_height = load_avatar_frames(ASSETS_DIR, self._avatar_width)
        self.frame_closed_l, self.frame_open_l, _ = load_avatar_frames(ASSETS_DIR, self._avatar_width, mirror=True)

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=MAGIC_TRANSPARENT_COLOR)
        root.wm_attributes("-transparentcolor", MAGIC_TRANSPARENT_COLOR)
        root.geometry(f"{self._avatar_width}x{avatar_height}+420+120")
        root.update_idletasks()
        self._hwnd = exclude_self_from_screen_capture(root.winfo_id())

        self._mouse_down = False
        self._fall_velocity = 0.0
        self._physics_enabled_var = tk.BooleanVar(value=True)
        self._walk_seconds_left = 0.0
        self._reacting = False
        self._next_wander_at = time.monotonic() + random.uniform(WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC)

        self.avatar_label = tk.Label(
            root, image=self.frame_closed, bg=MAGIC_TRANSPARENT_COLOR, bd=0, highlightthickness=0,
        )
        self.avatar_label.place(x=0, y=0, relwidth=1, relheight=1)

        self.dialogue_var = tk.StringVar(value="")
        self.dialogue_label = tk.Label(
            root, textvariable=self.dialogue_var, wraplength=self._avatar_width - 24,
            justify="left", font=("Microsoft JhengHei", 12), fg=COLOR_TEXT, bg=COLOR_PANEL,
            padx=10, pady=6,
        )
        self._update_dialogue_visibility()

        self._context_menu = tk.Menu(
            root, tearoff=False, bg=COLOR_PANEL, fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
        )
        self._context_menu.add_checkbutton(label="重力（桌面感）", variable=self._physics_enabled_var)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="結束", command=self._on_close)

        for widget in (self.avatar_label, self.dialogue_label):
            widget.bind("<ButtonPress-1>", self._on_avatar_press)
            widget.bind("<B1-Motion>", self._on_avatar_motion)
            widget.bind("<ButtonRelease-1>", self._on_avatar_release)
            widget.bind("<Button-3>", self._on_avatar_right_click)
            widget.bind("<MouseWheel>", self._on_avatar_scroll)

        self.root.after(PHYSICS_TICK_MS, self._physics_tick)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        threading.Thread(target=self._desktop_watch_loop, daemon=True).start()

    # ---------- 立繪拖曳 / 點擊 ----------

    def _on_avatar_press(self, event: tk.Event) -> None:
        self._dragged = False
        self._mouse_down = True
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.root.winfo_x(), self.root.winfo_y())

    def _on_avatar_motion(self, event: tk.Event) -> None:
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        if abs(dx) > DRAG_THRESHOLD_PX or abs(dy) > DRAG_THRESHOLD_PX:
            self._dragged = True
        self.root.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")

    def _on_avatar_release(self, event: tk.Event) -> None:
        self._mouse_down = False
        self._fall_velocity = 0.0
        if not self._dragged:
            self._trigger_reaction("click")

    def _on_avatar_right_click(self, event: tk.Event) -> None:
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _on_avatar_scroll(self, event: tk.Event) -> None:
        direction = 1 if event.delta > 0 else -1
        new_width = max(AVATAR_MIN_WIDTH, min(AVATAR_MAX_WIDTH, self._pending_avatar_width + direction * AVATAR_SCROLL_STEP))
        if new_width == self._pending_avatar_width:
            return
        self._pending_avatar_width = new_width
        if self._avatar_resize_job is not None:
            self.root.after_cancel(self._avatar_resize_job)
        self._avatar_resize_job = self.root.after(60, self._apply_avatar_resize)

    def _apply_avatar_resize(self) -> None:
        self._avatar_resize_job = None
        if self._pending_avatar_width == self._avatar_width:
            return
        self._avatar_width = self._pending_avatar_width

        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.frame_closed, self.frame_open, avatar_height = load_avatar_frames(ASSETS_DIR, self._avatar_width)
        self.frame_closed_l, self.frame_open_l, _ = load_avatar_frames(ASSETS_DIR, self._avatar_width, mirror=True)
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self.root.geometry(f"{self._avatar_width}x{avatar_height}+{x}+{y}")
        self.dialogue_label.configure(wraplength=self._avatar_width - 24)

    def _current_frame(self, is_open: bool):
        if self._facing == "left":
            return self.frame_open_l if is_open else self.frame_closed_l
        return self.frame_open if is_open else self.frame_closed

    # ---------- 桌寵物理（重力/落地/自主漫遊，邏輯照抄 desktop_pet.py） ----------

    def _physics_tick(self) -> None:
        if self._physics_enabled_var.get() and not self._mouse_down:
            dt = PHYSICS_TICK_MS / 1000
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            surface_y = desktop_physics.find_landing_y(x, x + w, y + h, self._hwnd)
            falling = y + h < surface_y - 1
            if falling:
                self._fall_velocity = min(self._fall_velocity + GRAVITY_PX_S2 * dt, FALL_TERMINAL_VELOCITY_PX_S)
                new_y = min(y + self._fall_velocity * dt, surface_y - h)
                new_x = x
            else:
                self._fall_velocity = 0.0
                new_y = surface_y - h
                new_x = self._advance_wander(x, w, dt)
            if new_x != x or new_y != y:
                self.root.geometry(f"+{int(new_x)}+{int(new_y)}")
        self.root.after(PHYSICS_TICK_MS, self._physics_tick)

    def _advance_wander(self, x: int, w: int, dt: float) -> float:
        if self._reacting:
            return x
        if self._walk_seconds_left > 0:
            self._walk_seconds_left -= dt
            step = WANDER_SPEED_PX_S * dt * (1 if self._facing == "right" else -1)
            screen_w = self.root.winfo_screenwidth()
            new_x = max(0, min(x + step, screen_w - w))
            if new_x == x:
                self._walk_seconds_left = 0.0
            return new_x
        if time.monotonic() >= self._next_wander_at:
            self._start_wander()
        return x

    def _start_wander(self) -> None:
        self._facing = random.choice(("left", "right"))
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self._walk_seconds_left = random.uniform(WANDER_MIN_DURATION_SEC, WANDER_MAX_DURATION_SEC)
        self._next_wander_at = time.monotonic() + random.uniform(WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC)

    # ---------- 反應（點擊 / 桌面刪檔），都走這條路徑 ----------

    def _trigger_reaction(self, kind: str, **format_args) -> None:
        """從 REACTIONS[kind] 隨機挑一句台詞（可用 format_args 塞變數，例如
        eat_file 塞 filename）、顯示出來，同時借嘴巴開合圖快速閃幾下模擬反應
        動畫。忙碌中（上一個反應還沒收掉）就不疊加，避免台詞互相蓋掉。"""
        if self._reacting:
            return
        lines = REACTIONS.get(kind) or []
        if not lines:
            return
        line = random.choice(lines).format(**format_args)
        self._reacting = True
        self.dialogue_var.set(line)
        self._update_dialogue_visibility()
        self.root.after(0, lambda: self._blink(REACTION_BLINK_COUNT))
        self.root.after(int(REACTION_DIALOGUE_HOLD_SEC * 1000), self._end_reaction)

    def _blink(self, remaining: int) -> None:
        if remaining <= 0:
            self.avatar_label.configure(image=self._current_frame(is_open=False))
            return
        is_open = remaining % 2 == 1
        self.avatar_label.configure(image=self._current_frame(is_open=is_open))
        self.root.after(REACTION_BLINK_INTERVAL_MS, lambda: self._blink(remaining - 1))

    def _end_reaction(self) -> None:
        self._reacting = False
        self.dialogue_var.set("")
        self._update_dialogue_visibility()

    def _update_dialogue_visibility(self) -> None:
        if self.dialogue_var.get():
            self.dialogue_label.place(relx=0.5, rely=1.0, anchor="s", relwidth=1)
        else:
            self.dialogue_label.place_forget()

    # ---------- 桌面刪檔偵測（背景執行緒） ----------

    def _desktop_watch_loop(self) -> None:
        """每隔 DESKTOP_WATCH_INTERVAL_SEC 對桌面資料夾拍一次檔名快照，跟上一次
        比對，少了的檔案視為「被刪除」。第一輪只拍照不比對，不然程式一啟動就
        會把桌面上原本就存在的檔案全部誤判成「剛剛被刪除」。資料夾不存在
        （例如路徑被改過）就整條迴圈跳過，不讓背景執行緒噴例外把程式弄掛。"""
        previous: Optional[set[str]] = None
        while True:
            time.sleep(DESKTOP_WATCH_INTERVAL_SEC)
            try:
                current = {p.name for p in DESKTOP_WATCH_DIR.iterdir()}
            except OSError:
                continue
            if previous is not None:
                removed = previous - current
                for filename in removed:
                    self.root.after(0, lambda f=filename: self._trigger_reaction("eat_file", filename=f))
            previous = current

    def _on_close(self) -> None:
        self.root.destroy()


def main():
    root = tk.Tk()
    ReactivePetWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
