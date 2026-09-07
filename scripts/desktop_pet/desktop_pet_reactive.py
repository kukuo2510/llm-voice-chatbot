"""二代目桌寵（互動反應型）。

跟 desktop_pet.py（範例角色 Sakiko，深度對話型）並存的第二個角色，
不需要 LLM 人設／TTS 配音／STT 語音輸入，走的是輕量的「觸發 -> 反應」
路線：點擊／拖曳角色、桌面刪除檔案都會觸發台詞＋動畫反應。

## 跟 desktop_pet.py 的關係

這支是跟 desktop_pet.py 完全獨立的 process，不 import chat.py /
tts_infer.py / stt.py，所以不需要 Ollama、GPT-SoVITS 推理服務在跑，
兩支可以同時開著，互不干擾、互不依賴。

共用的「角色無關」邏輯（視窗去背、立繪載入／縮放、螢幕擷取排除）
定義在 pet_shell.py，詳細說明見該檔案。重力／落地／自主漫遊的手感
參數則跟 desktop_pet.py 保持一致，兩隻桌寵站在同一個桌面上時，
動作節奏會是協調一致的。

## 角色素材是佔位用的

角色本身還沒定案（人設／語音／立繪都還沒做），這支程式先把
「點擊／拖曳觸發反應」「桌面刪檔觸發反應」這套機制搭起來、
讓它可以實際跑跑看：

- 立繪暫時借用 characters/sakiko/avatar/ 底下的兩張圖當佔位
  （見下面 ASSETS_DIR 的註解）。
- 台詞也是隨手寫的佔位文字（見下面 REACTIONS 的說明）。

之後角色定案了，只要把 ASSETS_DIR 改成指向新角色的資料夾、把
REACTIONS 裡的台詞換成正式內容即可，不需要更動其他任何邏輯。

## 互動方式

- 點一下角色：從 REACTIONS["click"] 隨機挑一句台詞，播一小段反應動畫。
- 按住角色拖曳：移動視窗位置（跟「點一下」是用有沒有實際移動位置來
  區分，不是兩種分開綁定的操作，這部分邏輯是照抄 desktop_pet.py）。
- 桌面（%USERPROFILE%\\Desktop）刪除檔案：背景執行緒每隔幾秒比對一次
  檔名快照，發現少了檔案，就從 REACTIONS["eat_file"] 挑一句「吃掉」
  台詞（只監控這一個資料夾，不會去碰資源回收筒；如果 Desktop 被
  OneDrive 重新導向到別的路徑，這裡不會自動偵測到，需要的話要手動
  改 DESKTOP_WATCH_DIR）。
- 右鍵角色：彈出選單（重力開關、結束）。
- 重力／落地（實作見 desktop_physics.py）：手感跟 Sakiko 那支保持一致。

## 用法

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


# ============================================================
# 視覺樣式（跟 Sakiko 那支同一套氛圍色，維持視覺一致）
# ============================================================

COLOR_PANEL = "#241a30"
COLOR_ACCENT = "#7c4dbd"
COLOR_ACCENT_HOVER = "#5e3690"
COLOR_TEXT = "#eee6f5"


# ============================================================
# 立繪相關設定
# ============================================================

# 立繪素材：先借用 Sakiko 的兩張圖當佔位（見檔案開頭說明）。
# 等二代目角色定案、立繪做好之後，把這行換成指向
# characters/<新角色>/avatar 即可。
ASSETS_DIR = Path(__file__).resolve().parents[2] / "characters" / "sakiko" / "avatar"

AVATAR_DISPLAY_WIDTH = int(os.environ.get("DESKTOP_PET_REACTIVE_AVATAR_WIDTH", "220"))
AVATAR_MIN_WIDTH = 120
AVATAR_MAX_WIDTH = 600
AVATAR_SCROLL_STEP = 20

# 滑鼠按下去之後，移動距離要超過這個門檻（單位：像素）才算是「拖曳」，
# 沒超過的話就當成單純的「點擊」。
DRAG_THRESHOLD_PX = 3


# ============================================================
# 桌寵物理設定（重力／落地／自主漫遊）
# ============================================================
#
# 這些數值刻意跟 desktop_pet.py 保持一致，讓兩隻桌寵的手感一樣。

PHYSICS_TICK_MS = 33
GRAVITY_PX_S2 = 2200.0
FALL_TERMINAL_VELOCITY_PX_S = 1400.0
WANDER_SPEED_PX_S = 110.0
WANDER_MIN_INTERVAL_SEC = 5.0
WANDER_MAX_INTERVAL_SEC = 15.0
WANDER_MIN_DURATION_SEC = 1.0
WANDER_MAX_DURATION_SEC = 3.0


# ============================================================
# 反應動畫設定
# ============================================================
#
# 目前借用「嘴巴開合」的兩張圖快速切換幾下，模擬出「有反應」的感覺，
# 之後如果做了專屬的反應立繪（例如咀嚼東西的動作），再換掉這個佔位做法。

REACTION_BLINK_COUNT = 3
REACTION_BLINK_INTERVAL_MS = 120

# 反應台詞顯示這麼久之後，會自動收掉、回到沒有台詞的狀態。
REACTION_DIALOGUE_HOLD_SEC = 2.5


# ============================================================
# 反應台詞（佔位用，角色定案後直接替換內容即可）
# ============================================================
#
# 台詞是佔位用的隨手文字，角色人設定案後直接換掉這個 dict 就好
# （之後也可以考慮搬進 characters/<新角色>/reactions.py 這種外部設定檔，
# 但現在先不過度設計，維持成一個簡單的模組層級常數就好）。
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
    """互動反應型桌寵的主要視窗類別，負責畫面、互動事件與物理模擬。"""

    def __init__(self, root: tk.Tk):
        self.root = root

        # 拖曳狀態相關
        self._dragged = False
        self._drag_origin = (0, 0)
        self._win_origin = (0, 0)

        # 目前面向哪個方向（影響要顯示哪一組立繪：正常或鏡像翻轉）
        self._facing = "right"

        # 立繪縮放狀態：_avatar_width 是目前實際套用的寬度，
        # _pending_avatar_width 是滾輪還沒 debounce 完成前、暫存的目標寬度。
        self._avatar_width = AVATAR_DISPLAY_WIDTH
        self._pending_avatar_width = self._avatar_width
        self._avatar_resize_job: Optional[str] = None

        # 載入兩組立繪：面向右（預設）跟鏡像後的面向左，
        # 各自都有「嘴巴閉合」跟「嘴巴打開」兩張圖。
        self.frame_closed, self.frame_open, avatar_height = load_avatar_frames(
            ASSETS_DIR, self._avatar_width
        )
        self.frame_closed_l, self.frame_open_l, _ = load_avatar_frames(
            ASSETS_DIR, self._avatar_width, mirror=True
        )

        # 視窗設定：無邊框、永遠置頂、用色鍵去背，讓桌寵看起來像是
        # 直接貼在桌面上，而不是一個有邊框的一般視窗。
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=MAGIC_TRANSPARENT_COLOR)
        root.wm_attributes("-transparentcolor", MAGIC_TRANSPARENT_COLOR)
        root.geometry(f"{self._avatar_width}x{avatar_height}+420+120")
        root.update_idletasks()

        # 把桌寵自己的視窗排除在螢幕擷取之外，並取得可重複使用的 hwnd
        # （後續 desktop_physics 找落地面時，需要用這個 hwnd 排除自己）。
        self._hwnd = exclude_self_from_screen_capture(root.winfo_id())

        # 物理／互動狀態
        self._mouse_down = False
        self._fall_velocity = 0.0
        self._physics_enabled_var = tk.BooleanVar(value=True)
        self._walk_seconds_left = 0.0
        self._reacting = False
        self._next_wander_at = time.monotonic() + random.uniform(
            WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC
        )

        # 立繪本體：用一個鋪滿整個視窗的 Label 顯示目前的立繪圖片。
        self.avatar_label = tk.Label(
            root,
            image=self.frame_closed,
            bg=MAGIC_TRANSPARENT_COLOR,
            bd=0,
            highlightthickness=0,
        )
        self.avatar_label.place(x=0, y=0, relwidth=1, relheight=1)

        # 台詞氣泡：只有在有台詞內容時才會顯示出來（見 _update_dialogue_visibility）。
        self.dialogue_var = tk.StringVar(value="")
        self.dialogue_label = tk.Label(
            root,
            textvariable=self.dialogue_var,
            wraplength=self._avatar_width - 24,
            justify="left",
            font=("Microsoft JhengHei", 12),
            fg=COLOR_TEXT,
            bg=COLOR_PANEL,
            padx=10,
            pady=6,
        )
        self._update_dialogue_visibility()

        # 右鍵選單：提供「重力開關」跟「結束程式」兩個選項。
        self._context_menu = tk.Menu(
            root,
            tearoff=False,
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            activebackground=COLOR_ACCENT,
            activeforeground=COLOR_TEXT,
        )
        self._context_menu.add_checkbutton(label="重力（桌面感）", variable=self._physics_enabled_var)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="結束", command=self._on_close)

        # 立繪跟台詞氣泡都要能接收滑鼠事件（點擊、拖曳、右鍵、滾輪縮放），
        # 所以兩個 widget 都綁上同一組事件處理函式。
        for widget in (self.avatar_label, self.dialogue_label):
            widget.bind("<ButtonPress-1>", self._on_avatar_press)
            widget.bind("<B1-Motion>", self._on_avatar_motion)
            widget.bind("<ButtonRelease-1>", self._on_avatar_release)
            widget.bind("<Button-3>", self._on_avatar_right_click)
            widget.bind("<MouseWheel>", self._on_avatar_scroll)

        # 啟動物理模擬迴圈（重力／落地／自主漫遊）。
        self.root.after(PHYSICS_TICK_MS, self._physics_tick)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 啟動背景執行緒監控桌面資料夾的檔案刪除事件。
        threading.Thread(target=self._desktop_watch_loop, daemon=True).start()

    # ============================================================
    # 立繪拖曳 / 點擊
    # ============================================================

    def _on_avatar_press(self, event: tk.Event) -> None:
        """滑鼠左鍵按下：記錄起點座標，先假設這是「點擊」（還沒被判定為拖曳）。"""
        self._dragged = False
        self._mouse_down = True
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.root.winfo_x(), self.root.winfo_y())

    def _on_avatar_motion(self, event: tk.Event) -> None:
        """滑鼠按住移動：跟著移動視窗位置，並判斷是否已經超過拖曳門檻。"""
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]

        if abs(dx) > DRAG_THRESHOLD_PX or abs(dy) > DRAG_THRESHOLD_PX:
            self._dragged = True

        self.root.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")

    def _on_avatar_release(self, event: tk.Event) -> None:
        """滑鼠放開：如果整個過程沒有被判定為拖曳，就當成一次「點擊」觸發反應。"""
        self._mouse_down = False
        self._fall_velocity = 0.0

        if not self._dragged:
            self._trigger_reaction("click")

    def _on_avatar_right_click(self, event: tk.Event) -> None:
        """滑鼠右鍵：彈出右鍵選單。"""
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _on_avatar_scroll(self, event: tk.Event) -> None:
        """滑鼠滾輪：調整立繪顯示寬度（附帶 debounce，避免每滾一格就重新縮放一次圖片）。"""
        direction = 1 if event.delta > 0 else -1
        new_width = max(
            AVATAR_MIN_WIDTH,
            min(AVATAR_MAX_WIDTH, self._pending_avatar_width + direction * AVATAR_SCROLL_STEP),
        )

        if new_width == self._pending_avatar_width:
            return

        self._pending_avatar_width = new_width

        # debounce：如果短時間內滾了好幾格，只在滾動停止 60ms 後才真的
        # 觸發一次縮放運算，避免每滾一格都重新縮放圖片造成明顯延遲。
        if self._avatar_resize_job is not None:
            self.root.after_cancel(self._avatar_resize_job)
        self._avatar_resize_job = self.root.after(60, self._apply_avatar_resize)

    def _apply_avatar_resize(self) -> None:
        """實際套用滾輪縮放後的目標寬度，重新載入並顯示對應大小的立繪。"""
        self._avatar_resize_job = None

        if self._pending_avatar_width == self._avatar_width:
            return

        self._avatar_width = self._pending_avatar_width

        # 縮放時保持視窗左上角座標不變，只改變視窗大小，
        # 避免縮放後桌寵的位置感覺跳動。
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.frame_closed, self.frame_open, avatar_height = load_avatar_frames(
            ASSETS_DIR, self._avatar_width
        )
        self.frame_closed_l, self.frame_open_l, _ = load_avatar_frames(
            ASSETS_DIR, self._avatar_width, mirror=True
        )
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self.root.geometry(f"{self._avatar_width}x{avatar_height}+{x}+{y}")
        self.dialogue_label.configure(wraplength=self._avatar_width - 24)

    def _current_frame(self, is_open: bool):
        """依目前面向方向與嘴巴開闔狀態，回傳對應的立繪圖片物件。"""
        if self._facing == "left":
            return self.frame_open_l if is_open else self.frame_closed_l
        return self.frame_open if is_open else self.frame_closed

    # ============================================================
    # 桌寵物理（重力 / 落地 / 自主漫遊，邏輯照抄 desktop_pet.py）
    # ============================================================

    def _physics_tick(self) -> None:
        """物理模擬的每一個 tick：計算重力下墜或自主漫遊後的新位置。

        只有在「重力開關開啟」且「使用者目前沒有按住拖曳」時才會運算，
        避免使用者正在手動拖曳時，物理模擬又同時搶著改變視窗位置。
        """
        if self._physics_enabled_var.get() and not self._mouse_down:
            dt = PHYSICS_TICK_MS / 1000
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            w = self.root.winfo_width()
            h = self.root.winfo_height()

            surface_y = desktop_physics.find_landing_y(x, x + w, y + h, self._hwnd)
            falling = y + h < surface_y - 1

            if falling:
                # 還沒落地：套用重力加速度，但速度不會超過終端速度上限。
                self._fall_velocity = min(
                    self._fall_velocity + GRAVITY_PX_S2 * dt,
                    FALL_TERMINAL_VELOCITY_PX_S,
                )
                new_y = min(y + self._fall_velocity * dt, surface_y - h)
                new_x = x
            else:
                # 已經落地：重置下墜速度，維持貼齊地面，並嘗試推進自主漫遊。
                self._fall_velocity = 0.0
                new_y = surface_y - h
                new_x = self._advance_wander(x, w, dt)

            if new_x != x or new_y != y:
                self.root.geometry(f"+{int(new_x)}+{int(new_y)}")

        # 不管這次有沒有真的移動，都要排程下一個 tick，讓物理模擬持續運作。
        self.root.after(PHYSICS_TICK_MS, self._physics_tick)

    def _advance_wander(self, x: int, w: int, dt: float) -> float:
        """推進自主漫遊的狀態，回傳這一個 tick 之後的新 x 座標。

        如果正在播放反應動畫（_reacting 為 True），暫停漫遊，
        避免角色一邊講話一邊到處走動看起來很奇怪。
        """
        if self._reacting:
            return x

        if self._walk_seconds_left > 0:
            # 目前正在漫遊中：依面向方向前進，並確保不會走出螢幕範圍。
            self._walk_seconds_left -= dt
            step = WANDER_SPEED_PX_S * dt * (1 if self._facing == "right" else -1)
            screen_w = self.root.winfo_screenwidth()
            new_x = max(0, min(x + step, screen_w - w))

            if new_x == x:
                # 撞到螢幕邊界走不動了，直接提早結束這次漫遊。
                self._walk_seconds_left = 0.0

            return new_x

        if time.monotonic() >= self._next_wander_at:
            # 到了該開始下一次漫遊的時間點，啟動新的一輪漫遊。
            self._start_wander()

        return x

    def _start_wander(self) -> None:
        """開始新的一輪自主漫遊：隨機決定方向與持續時間，並排定下一次漫遊的時間。"""
        self._facing = random.choice(("left", "right"))
        self.avatar_label.configure(image=self._current_frame(is_open=False))
        self._walk_seconds_left = random.uniform(WANDER_MIN_DURATION_SEC, WANDER_MAX_DURATION_SEC)
        self._next_wander_at = time.monotonic() + random.uniform(
            WANDER_MIN_INTERVAL_SEC, WANDER_MAX_INTERVAL_SEC
        )

    # ============================================================
    # 反應（點擊 / 桌面刪檔），都走同一條路徑
    # ============================================================

    def _trigger_reaction(self, kind: str, **format_args) -> None:
        """觸發一次反應：挑台詞、顯示台詞、播放反應動畫。

        從 REACTIONS[kind] 隨機挑一句台詞（可以用 format_args 傳入變數，
        例如 eat_file 這個種類會傳入 filename 變數），顯示出來，同時
        借用嘴巴開合的兩張圖快速切換幾下，模擬出「有反應」的動畫效果。

        如果目前已經在忙碌中（上一個反應動畫還沒收掉），就不會疊加
        新的反應，避免不同的台詞互相蓋掉、使用者看不清楚。
        """
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
        """反覆切換嘴巴開合圖片，模擬簡易的講話動畫，直到剩餘次數歸零。"""
        if remaining <= 0:
            self.avatar_label.configure(image=self._current_frame(is_open=False))
            return

        # 用剩餘次數的奇偶性交替決定嘴巴開／合，做出一開一合的效果。
        is_open = remaining % 2 == 1
        self.avatar_label.configure(image=self._current_frame(is_open=is_open))
        self.root.after(REACTION_BLINK_INTERVAL_MS, lambda: self._blink(remaining - 1))

    def _end_reaction(self) -> None:
        """結束目前的反應：收掉台詞氣泡，回到可以觸發下一次反應的狀態。"""
        self._reacting = False
        self.dialogue_var.set("")
        self._update_dialogue_visibility()

    def _update_dialogue_visibility(self) -> None:
        """依目前是否有台詞內容，決定要不要顯示台詞氣泡。"""
        if self.dialogue_var.get():
            self.dialogue_label.place(relx=0.5, rely=1.0, anchor="s", relwidth=1)
        else:
            self.dialogue_label.place_forget()

    # ============================================================
    # 桌面刪檔偵測（背景執行緒）
    # ============================================================

    def _desktop_watch_loop(self) -> None:
        """背景執行緒：定期監控桌面資料夾，偵測檔案是否被刪除。

        每隔 DESKTOP_WATCH_INTERVAL_SEC 秒，對桌面資料夾拍一次檔名快照，
        跟上一次拍到的快照比對，少了的檔案就視為「被刪除」，觸發對應反應。

        第一輪只單純拍照、不做比對——如果第一輪就跟一個空快照比較，
        程式一啟動就會把桌面上原本就存在的所有檔案，全部誤判成
        「剛剛被刪除」，觸發一連串不合理的反應。

        如果資料夾不存在（例如路徑被改過、或 Desktop 被重新導向到
        別處還沒同步 DESKTOP_WATCH_DIR），就直接跳過這一輪比對，
        不讓背景執行緒因為未捕捉的例外而整個中斷掉。
        """
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
                    # 用預設參數 f=filename 把當下迴圈的值綁進 lambda，
                    # 避免閉包捕捉到迴圈變數的「最後一次」值而不是各自的值。
                    self.root.after(0, lambda f=filename: self._trigger_reaction("eat_file", filename=f))

            previous = current

    def _on_close(self) -> None:
        """關閉桌寵視窗，結束程式。"""
        self.root.destroy()


def main():
    root = tk.Tk()
    ReactivePetWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
