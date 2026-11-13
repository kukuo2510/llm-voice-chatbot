"""右鍵「工作」子選單：打工／外送／廚師／股票／收工（只播動畫，不計錢、不存狀態）。

狀態判斷與抽籤都在 pet_work_data（純邏輯）；這裡只負責接 Tk 選單、選股票視窗與氣泡台詞。
"""

import tkinter as tk

from pet_config import COLOR_ACCENT, COLOR_PANEL, COLOR_TEXT
from pet_stock_picker import open_stock_picker
from pet_work_data import (STOCK_NAMES, STOCK_SEQUENCE, WORK_JOBS, current_job,
                           finish_job, job_enabled, settle_stock, start_job)


class WorkMixin:
    """工作子選單。"""

    _stock_picker = None
    _work_menu = None

    def _build_work_menu(self, parent) -> None:
        menu = tk.Menu(parent, tearoff=False, bg=COLOR_PANEL, fg=COLOR_TEXT,
                       activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
                       postcommand=self._refresh_work_menu)
        for label, seq in WORK_JOBS:
            menu.add_command(label=label, command=lambda s=seq: self._on_work_pick(s))
        menu.add_separator()
        menu.add_command(label="收工", command=self._on_work_finish)
        self._work_menu = menu
        parent.add_cascade(label="工作", menu=menu)

    def _refresh_work_menu(self) -> None:
        """每次彈出前更新：沒定義的工作灰掉、不在工作時「收工」灰掉。"""
        ctrl = self._anim_ctrl
        for i, (_, seq) in enumerate(WORK_JOBS):
            self._work_menu.entryconfigure(i, state="normal" if job_enabled(ctrl, seq) else "disabled")
        # 項目依序是：工作們、分隔線、收工
        finish_index = len(WORK_JOBS) + 1
        self._work_menu.entryconfigure(
            finish_index, state="normal" if current_job(ctrl) is not None else "disabled")

    def _on_work_pick(self, sequence: str) -> None:
        self._close_stock_picker()
        if sequence == STOCK_SEQUENCE and current_job(self._anim_ctrl) == STOCK_SEQUENCE:
            started = True  # 已經在看股票（選擇視窗被關掉）：重新開視窗就好
        else:
            started = start_job(self._anim_ctrl, sequence)
        if started and sequence == STOCK_SEQUENCE:
            self._stock_picker = open_stock_picker(self.root, STOCK_NAMES, self._on_stock_choice)

    def _on_work_finish(self) -> None:
        self._close_stock_picker()
        finish_job(self._anim_ctrl)

    def _close_stock_picker(self) -> None:
        picker, self._stock_picker = self._stock_picker, None
        if picker is not None:
            try:
                picker.destroy()
            except Exception:
                pass

    def _on_stock_choice(self, name) -> None:
        """選股票視窗回呼：name 是股票名稱，None 表示「算了」或關掉視窗。"""
        self._stock_picker = None
        _, line = settle_stock(self._anim_ctrl, chosen=name is not None)
        if line:
            self._set_dialogue(line, is_status=False)  # 只顯示氣泡，不送 LLM、不 TTS
