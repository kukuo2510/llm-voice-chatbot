"""對話氣泡視窗（classic 模式專用）：無邊框、置頂、色鍵去背的獨立 Toplevel，
顯示在角色頭頂上方，跟著角色移動／縮放，並限制在螢幕工作區內。

舞台模式（livliv_link.ENABLED）完全不使用。任何一步失敗（例如缺素材、Tk 影像建不起來）
就把模組旗標 _failed 設為 True，退回原本疊在立繪下緣的 dialogue_label，不影響其他功能。
"""

import tkinter as tk

from PIL import Image, ImageTk

from pet_avatar_metrics import avatar_metrics
from pet_bubble import head_point, place_bubble
from pet_bubble_render import bubble_image_size, render_bubble
from pet_config import ASSETS_DIR
from pet_shell import MAGIC_TRANSPARENT_COLOR, exclude_self_from_screen_capture
from pet_workarea import work_area_at
import livliv_link


# 氣泡是否已經壞掉（整個行程共用一個旗標；放在模組層級，才不會讓 CharacterWindow 實例多出屬性）。
_failed = False


def mark_failed() -> None:
    global _failed
    _failed = True


class BubbleMixin:
    """頭頂氣泡：顯示／隱藏／跟隨定位。"""

    _bubble_win = None
    _bubble_label = None
    _bubble_photo = None
    _bubble_key = None
    _bubble_size = (0, 0)
    _bubble_shown = False
    _bubble_ready = False
    _bubble_text = ""
    _bubble_is_status = False
    _popup_is_bubble = False

    # ---------- 開關與共用工具 ----------

    def _bubble_enabled(self) -> bool:
        return not livliv_link.ENABLED and not _failed

    def _bubble_probe(self) -> bool:
        """確認 Tk 能建影像（沒有 Tk 根視窗時會失敗），只測一次。"""
        if not self._bubble_ready:
            ImageTk.PhotoImage(Image.new("RGB", (1, 1)))
            self._bubble_ready = True
        return True

    def _head_and_rect(self):
        """回傳 (頭頂螢幕座標, 視窗矩形, 工作區)。頭頂位置由立繪非透明外框算出。"""
        m = avatar_metrics(ASSETS_DIR)
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        head = head_point(x, y, w, h, m["cx_frac"], m["top_frac"])
        fallback = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        return head, (x, y, x + w, y + h), work_area_at(head[0], head[1], fallback)

    def _make_overlay(self) -> tk.Toplevel:
        """建立一個跟主視窗同樣方式去背、置頂、排除螢幕擷取的無邊框視窗。"""
        win = tk.Toplevel(self.root)
        win.geometry("+-32000+-32000")  # 先丟到螢幕外，定位好再顯示，避免左上角閃一下
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=MAGIC_TRANSPARENT_COLOR)
        win.wm_attributes("-transparentcolor", MAGIC_TRANSPARENT_COLOR)
        win.update_idletasks()
        exclude_self_from_screen_capture(win.winfo_id())
        return win

    def _bind_avatar_events(self, widget) -> None:
        """氣泡跟角色一樣能點／拖／右鍵／滾輪。"""
        widget.bind("<ButtonPress-1>", self._on_avatar_press)
        widget.bind("<B1-Motion>", self._on_avatar_motion)
        widget.bind("<ButtonRelease-1>", self._on_avatar_release)
        widget.bind("<Button-3>", self._on_avatar_right_click)
        widget.bind("<MouseWheel>", self._on_avatar_scroll)

    # ---------- 台詞氣泡 ----------

    def _bubble_apply_dialogue(self, text: str, is_status: bool) -> bool:
        """pet_dialogue 呼叫：回傳 True 表示氣泡接手了，False 表示請走原本的 dialogue_label。"""
        if not self._bubble_enabled():
            return False
        try:
            self._bubble_probe()
            self._bubble_text = text
            self._bubble_is_status = is_status
            self._bubble_refresh()
            self.dialogue_label.place_forget()
            return True
        except Exception as exc:  # 退回原本的對話框
            print(f"[bubble] 氣泡無法使用，退回舊對話框：{exc!r}")
            mark_failed()
            self._bubble_destroy()
            return False

    def _bubble_refresh(self) -> None:
        """依目前文字與角色位置，更新氣泡的內容與位置（角色移動時每個 tick 都會呼叫）。"""
        if self._bubble_win is None and not self._bubble_text:
            return
        suspended = self._input_popup is not None and self._popup_is_bubble
        if not self._bubble_text or suspended:
            self._bubble_hide()
            return

        head, win_rect, work = self._head_and_rect()
        pos = place_bubble(head, bubble_image_size(self._bubble_text), win_rect, work)
        key = (self._bubble_text, self._bubble_is_status, pos["tail_dx"])
        if self._bubble_win is None:
            self._bubble_win = self._make_overlay()
            self._bubble_label = tk.Label(
                self._bubble_win, bg=MAGIC_TRANSPARENT_COLOR, bd=0, highlightthickness=0
            )
            self._bubble_label.pack()
            self._bind_avatar_events(self._bubble_label)
        if key != self._bubble_key:
            img = render_bubble(self._bubble_text, self._bubble_is_status, pos["tail_dx"])
            self._bubble_photo = ImageTk.PhotoImage(img)  # 要留住參照，不然會被 GC 回收
            self._bubble_label.configure(image=self._bubble_photo)
            self._bubble_key = key
            self._bubble_size = img.size
        w, h = self._bubble_size
        self._bubble_win.geometry(f"{w}x{h}+{pos['x']}+{pos['y']}")
        if not self._bubble_shown:
            self._bubble_win.deiconify()
            self._bubble_shown = True

    def _bubble_hide(self) -> None:
        if self._bubble_win is not None and self._bubble_shown:
            self._bubble_win.withdraw()
            self._bubble_shown = False

    def _bubble_destroy(self) -> None:
        if self._bubble_win is None:
            return
        try:
            self._bubble_win.destroy()
        except Exception:
            pass
        self._bubble_win = None
        self._bubble_label = None
        self._bubble_photo = None
        self._bubble_key = None
        self._bubble_shown = False
