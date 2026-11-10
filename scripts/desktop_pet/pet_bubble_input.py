"""輸入列（classic 模式）：氣泡樣式的輸入框，出現在頭頂上方，輸入時取代台詞氣泡。

失敗（沒有素材、Tk 影像建不起來）就回傳 False，讓 pet_input 走原本貼在視窗下方的輸入列。
"""

import tkinter as tk

from PIL import ImageTk

from pet_bubble import BUBBLE_PAD_X, BUBBLE_TAIL_H, place_bubble
from pet_bubble_window import mark_failed
from pet_bubble_render import render_input_frame
from pet_config import BUBBLE_FILL, BUBBLE_TEXT
from pet_shell import MAGIC_TRANSPARENT_COLOR

INPUT_W = 330
INPUT_BODY_H = 44


class BubbleInputMixin:
    """氣泡樣式的輸入列。"""

    _popup_photo = None
    _popup_label = None
    _popup_key = None

    def _show_bubble_input(self) -> bool:
        if not self._bubble_enabled():
            return False
        size = (INPUT_W, INPUT_BODY_H + BUBBLE_TAIL_H)
        try:
            self._bubble_probe()
            head, win_rect, work = self._head_and_rect()
            pos = place_bubble(head, size, win_rect, work)
            photo = ImageTk.PhotoImage(render_input_frame(size[0], size[1], pos["tail_dx"]))
        except Exception as exc:
            print(f"[bubble] 氣泡輸入列無法使用，退回舊輸入列：{exc!r}")
            mark_failed()
            return False

        popup = self._make_overlay()
        label = tk.Label(popup, image=photo, bg=MAGIC_TRANSPARENT_COLOR, bd=0, highlightthickness=0)
        label.pack()
        entry = tk.Entry(
            popup,
            font=("Microsoft JhengHei", 13),
            bg=BUBBLE_FILL,
            fg=BUBBLE_TEXT,
            insertbackground=BUBBLE_TEXT,
            relief="flat",
        )
        entry.place(x=BUBBLE_PAD_X, y=8, width=INPUT_W - 2 * BUBBLE_PAD_X, height=INPUT_BODY_H - 16)
        entry.bind("<Return>", self._on_popup_submit)
        entry.bind("<Escape>", lambda e: self._hide_input_popup())
        entry.bind("<FocusOut>", lambda e: self._hide_input_popup())

        self._input_popup = popup
        self._input_entry = entry
        self._popup_is_bubble = True
        self._popup_photo = photo
        self._popup_label = label
        self._popup_key = pos["tail_dx"]
        popup.geometry(f"{size[0]}x{size[1]}+{pos['x']}+{pos['y']}")
        self._bubble_refresh()  # 輸入期間台詞氣泡先收起來
        entry.focus_force()
        return True

    def _position_bubble_input(self) -> None:
        """輸入列跟著角色移動：重算位置，尾巴位置變了就重畫外框。"""
        size = (INPUT_W, INPUT_BODY_H + BUBBLE_TAIL_H)
        head, win_rect, work = self._head_and_rect()
        pos = place_bubble(head, size, win_rect, work)
        if pos["tail_dx"] != self._popup_key:
            self._popup_photo = ImageTk.PhotoImage(render_input_frame(size[0], size[1], pos["tail_dx"]))
            self._popup_label.configure(image=self._popup_photo)
            self._popup_key = pos["tail_dx"]
        self._input_popup.geometry(f"+{pos['x']}+{pos['y']}")
