"""多輪對話記憶（短期上下文）。

## 為什麼需要這個模組

原本 chat_ja() 每次呼叫只送「系統提示詞＋這一句使用者輸入」給 LLM，
角色完全不記得上一句聊了什麼——使用者問「那你剛剛說的那個呢？」，
她根本不知道「剛剛」是什麼。

這裡的做法參考 Project AIRI（moeru-ai/airi）core-agent 的兩個設計：

1. 保留最近 N 輪（compaction）：AIRI 的 compactConversationEntries 只保留
   最近 recentTurnLimit 輪，更舊的丟掉，避免提示詞無限長大、拖慢本地模型
   或超出 context 長度。這裡沿用「只留最近幾輪」的概念，但不另外呼叫 LLM
   做摘要——多一次 LLM 呼叫對本地 qwen2.5:14b 來說太慢了。

2. 時間前綴（datetime-prefix）：AIRI 會在「使用者訊息」前面加上
   `[YYYY-MM-DD HH:MM] ` 當作時間錨點，讓模型知道現在幾點、兩句話
   之間隔了多久（例如隔天再聊，角色可以自然地說「早安」而不是接著
   昨晚的話題講）。AIRI 特別註明「不要加在角色回覆上」，因為模型會
   模仿格式、自己也開始在回覆裡輸出時間戳——這裡照做，只加在使用者訊息。

## 存檔

對話紀錄會存進本機的 JSON 檔（預設跟這支程式同一層的 .chat_history.json，
已加進 .gitignore），重開程式之後角色還記得之前聊過的內容。
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional


# ============================================================
# 設定值
# ============================================================

# 最多保留幾「輪」對話（一輪 = 一句使用者輸入＋一句角色回覆）。
# 太多會讓每次請求的提示詞變長、本地模型回應變慢；太少角色又容易忘記
# 剛剛在聊什麼。12 輪大約是幾分鐘的閒聊量，可以用環境變數調整。
DEFAULT_MAX_TURNS = int(os.environ.get("CHAT_HISTORY_TURNS", "12"))

DEFAULT_HISTORY_FILE = Path(__file__).resolve().parent / ".chat_history.json"


def format_time_prefix(timestamp: float) -> str:
    """把時間戳格式化成 `[YYYY-MM-DD HH:MM] `（本機時區，結尾含一個空白）。

    格式跟 AIRI 的 formatTimePrefix 一致。舊訊息跟最新訊息都用同一種格式，
    模型比較容易理解「這是時間標記」而不是對話內容的一部分。
    """
    return time.strftime("[%Y-%m-%d %H:%M] ", time.localtime(timestamp))


# ============================================================
# 對話紀錄本體
# ============================================================


class ConversationHistory:
    """保存最近幾輪對話，並轉成 LLM messages 格式。

    每一輪存成一個 dict：
        {"user": 使用者輸入, "assistant": 角色回覆（原始 JSON 字串）, "time": 時間戳}

    角色回覆刻意存「LLM 原本回傳的 JSON 字串」而不是只存台詞，原因是：
    歷史訊息裡的回覆格式跟系統提示詞要求的輸出格式一致，模型下一輪
    比較不會忘記要回傳 JSON（本地小模型很容易被歷史訊息的格式帶偏）。

    這個物件會同時被主執行緒（清除記憶的選單）跟背景執行緒（對話生成）
    存取，所以讀寫都包在 lock 裡。
    """

    def __init__(
        self,
        max_turns: int = DEFAULT_MAX_TURNS,
        path: Optional[Path] = DEFAULT_HISTORY_FILE,
    ):
        self.max_turns = max_turns
        self.path = path
        self._turns: list[dict] = []
        self._lock = threading.Lock()
        self._load()

    # ---------- 讀寫檔案 ----------

    def _load(self) -> None:
        """從存檔讀回之前的對話；檔案不存在或壞掉就當作沒有紀錄。"""
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._turns = [t for t in data if "user" in t and "assistant" in t][-self.max_turns:]
        except Exception as e:
            print(f"[conversation] 對話紀錄讀取失敗，改從空白開始：{e}")
            self._turns = []

    def _save(self) -> None:
        """把目前保留的對話寫回存檔（呼叫端要先拿到 lock）。"""
        if self.path is None:
            return
        try:
            self.path.write_text(
                json.dumps(self._turns, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception as e:
            # 存檔失敗不影響這次對話，只是重開程式後會忘記，印出來就好。
            print(f"[conversation] 對話紀錄存檔失敗：{e}")

    # ---------- 對外介面 ----------

    def to_messages(self) -> list[dict]:
        """把保留的對話轉成 LLM 的 messages 格式（不含系統提示詞與這一輪的新輸入）。"""
        with self._lock:
            messages = []
            for turn in self._turns:
                messages.append({
                    "role": "user",
                    "content": format_time_prefix(turn["time"]) + turn["user"],
                })
                messages.append({"role": "assistant", "content": turn["assistant"]})
            return messages

    def add_turn(self, user_text: str, assistant_text: str, timestamp: float) -> None:
        """記一輪對話；超過 max_turns 的舊對話直接丟掉。"""
        with self._lock:
            self._turns.append({"user": user_text, "assistant": assistant_text, "time": timestamp})
            self._turns = self._turns[-self.max_turns:]
            self._save()

    def clear(self) -> None:
        """清除所有對話記憶（含存檔）。"""
        with self._lock:
            self._turns = []
            self._save()

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)
