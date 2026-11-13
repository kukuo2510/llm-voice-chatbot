"""Claude Code hook 通知：UDP 監聽與讓角色講一句。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

import json
import socket

from pet_config import CLAUDE_EVENT_MAX_TRIES, CLAUDE_EVENT_PORT, CLAUDE_EVENT_RETRY_MS
from pet_prompts import _CLAUDE_EVENT_PROMPTS


class ClaudeEventMixin:
    """Claude Code hook 通知：UDP 監聽與讓角色講一句。"""

    def _claude_event_loop(self) -> None:
        """背景執行緒：接 Claude Code hook 傳來的通知，轉給主執行緒處理。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", CLAUDE_EVENT_PORT))
        except OSError as e:
            print(f"[claude_event] 埠 {CLAUDE_EVENT_PORT} 開不起來，收不到 Claude 的通知：{e}")
            return
        while True:
            data, _ = sock.recvfrom(4096)
            try:
                event = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if event.get("event") in _CLAUDE_EVENT_PROMPTS:
                print(f"[claude_event] 收到：{event}")
                self.root.after(0, lambda e=event: self._on_claude_event(e))

    def _on_claude_event(self, event: dict, tries: int = 0) -> None:
        """角色有空就讓她講一句；正在講話的話稍後再試，等太久就放棄。"""
        if self._pet_sleeping():  # 睡覺中的通知直接丟掉，不排隊補講
            return
        if self._busy:
            if tries < CLAUDE_EVENT_MAX_TRIES:
                self.root.after(CLAUDE_EVENT_RETRY_MS, lambda: self._on_claude_event(event, tries + 1))
            return
        minutes = max(1, round(float(event.get("seconds", 60)) / 60))
        self._trigger_proactive(_CLAUDE_EVENT_PROMPTS[event["event"]].format(minutes=minutes))
