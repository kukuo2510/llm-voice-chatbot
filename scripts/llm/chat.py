"""對話 AI 窗口。

接收使用者輸入的中文，回傳三樣東西：
1. 角色用指定語言講出來的回覆（餵給 TTS 用）
2. 這句回覆的中文翻譯（顯示給使用者看）
3. 這句回覆的語氣標籤（給桌寵之類的呼叫端決定要用什麼表情/動畫）

## LLM 供應商

預設走本地 Ollama（qwen2.5:14b），不需要 API key、不需要網路。

想改用雲端 LLM，要在 .env 裡明確設定 `LLM_PROVIDER=openai`（預設值是
"local"，不寫這行的話行為跟以前完全一樣）並填好 `OPENAI_API_KEY`。
注意：一定要「明確選擇」openai 才會切過去——就算環境變數裡剛好有
OPENAI_API_KEY，只要沒把 LLM_PROVIDER 設成 openai，也不會被拿去用。
這是刻意設計成「選了才會切」，不是「偵測到 key 存在就自動切」，
避免使用者不小心把雲端 API 用掉額度。

前置需求（走 local 時）：
- Ollama 已安裝，且服務在背景常駐（`ollama serve`；一般裝好後開機會自動啟動）
- 已經下載過 qwen2.5:14b 這個模型（`ollama pull qwen2.5:14b`）

## 語言設定

角色實際開口說的語言由環境變數 `CHARACTER_SPEECH_LANGUAGE`（.env）決定，
預設是「日文」——範例角色（豐川祥子）就是設計成講日文，換成自己的角色時
只需要改這個環境變數的值，不用動到下面任何程式邏輯。

給使用者看的翻譯固定是繁體中文（對應 `reply_zh` 欄位），這跟角色開口
說話用的語言是兩件完全獨立的事：語言可以隨便換，但翻譯永遠是中文。

## 為什麼把「生成回覆」跟「翻譯」拆成兩支函式

`chat_ja()`（生成角色語言回覆＋語氣）跟 `translate_zh()`（翻譯成中文）
被拆成兩支獨立函式，而不是合在一起做完再回傳，原因是：

TTS（語音合成）只吃得到 `reply_speech` 跟 `tone` 這兩個欄位，
根本不需要等中文翻譯完成才能開始跑語音合成。所以呼叫端（例如桌寵）
可以在拿到 `reply_speech` 之後立刻開始語音合成，同時「平行」呼叫
`translate_zh()` 去取得中文翻譯——這樣使用者實際感受到的等待時間，
就只受「生成角色回覆」這一次 LLM 呼叫的延遲影響，而不是兩次呼叫加總。

`chat()` 保留下來，是這兩支函式依序執行的合併版本，提供給不在意
這個延遲差異的呼叫端使用（例如簡單的互動式 CLI，用不著搞平行處理）。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

# 讓這支腳本不管從哪個目錄被執行，都能找到同一層的 common/ 套件。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.env import load_dotenv  # noqa: E402
from conversation import ConversationHistory, format_time_prefix  # noqa: E402
import expressions  # noqa: E402  表情標記的詞表（說明給模型看）
import web_lookup  # noqa: E402  〔查：…〕上網查的說明

if __name__ == "__main__":  # 命令列直接執行時，讓拆出去的模組 import chat 拿到的是這個模組本身
    sys.modules.setdefault("chat", sys.modules[__name__])

load_dotenv()


# ============================================================
# 環境變數 / 設定值
# ============================================================

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

# Ollama 的 context 長度（token 數）。有了多輪對話記憶（見 conversation.py）
# 之後，提示詞會比以前長很多：人設＋格式說明＋最近十幾輪對話。Ollama 預設的
# context 長度不夠時，會「無聲地」把最前面的內容截掉——最前面剛好是系統提示詞
# （人設），角色會突然忘記自己是誰。所以這裡明確指定大一點的值。
# 代價是 KV cache 會多吃一些顯示記憶體，記憶體吃緊時可以調小。
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

# 角色開口說話用的語言。換角色、換語言時，只需要改這個環境變數的值，
# 不需要去碰下面任何提示詞模板的程式碼。
CHARACTER_SPEECH_LANGUAGE = os.environ.get("CHARACTER_SPEECH_LANGUAGE", "日文")

# 只有明確設定 LLM_PROVIDER=openai 才會切去雲端，不是偵測到
# OPENAI_API_KEY 存在就自動切走（詳見檔案最上面的說明）。
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "local").strip().lower()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# 可以選擇的語氣標籤，跟 characters/<角色>/voice_config.py 裡的
# TONE_REFS 是對應的——這裡列出的每一種語氣，都要能在那邊找到
# 對應的參考音檔，否則 TTS 端會找不到參考音檔可用。
TONE_OPTIONS = ["neutral", "happy", "serious", "hesitant", "intense"]

# 角色人設放在 characters/<角色>/persona.md，要改台詞風格/個性設定，
# 直接編輯那個檔案就好，不用碰這支程式碼。
#
# 要換角色時，把這裡的 "sakiko" 換成你自己的角色資料夾名稱即可。
PERSONA_PATH = Path(__file__).resolve().parents[2] / "characters" / "sakiko" / "persona.md"


# ============================================================
# 拆出去的模組（名稱照舊可以從 chat 取得）
# ============================================================
#
# 提示詞組裝 -> chat_prompts.py，台灣用語校正 -> tw_terms.py，LLM 呼叫 -> chat_backend.py。
# 設定值（上面那些大寫變數）的本尊還是在這個檔案：那幾個模組讀設定時，每次呼叫都來這裡拿，
# 所以桌寵執行期改 chat.LLM_PROVIDER / chat.CHARACTER_SPEECH_LANGUAGE 照樣立刻生效。

from chat_prompts import (  # noqa: E402,F401
    _TONE_FIELD_INSTRUCTIONS,
    _reply_speech_field_instructions,
    _build_output_format_instructions,
    _build_speech_only_output_format_instructions,
    _build_zh_translate_system_prompt,
    _load_persona_text,
)
from tw_terms import (  # noqa: E402,F401
    _TW_TERM_FIXES,
    _fix_tw_terms,
)
from chat_backend import (  # noqa: E402,F401
    OllamaNotRunningError,
    OpenAIRequestError,
    _call_ollama,
    _call_openai,
    _call_llm,
)


# ============================================================
# 回傳資料結構
# ============================================================


class SakikoReply(BaseModel):
    """chat() 的完整回傳結構：角色回覆＋中文翻譯＋語氣標籤。"""

    reply_speech: str
    reply_zh: str
    tone: Literal["neutral", "happy", "serious", "hesitant", "intense"]


class SakikoReplyJa(BaseModel):
    """chat_ja() 的回傳結構：只有角色回覆＋語氣標籤，不含中文翻譯。"""

    reply_speech: str
    tone: Literal["neutral", "happy", "serious", "hesitant", "intense"]


# ============================================================
# 對外主要介面
# ============================================================


def chat_ja(
    user_message: str,
    system_prompt: Optional[str] = None,
    history: Optional[ConversationHistory] = None,
) -> SakikoReplyJa:
    """只生成角色語言的回覆＋語氣標籤（給 TTS 用），不含中文翻譯，走最短路徑。

    注意：函式名沿用 chat_ja 只是歷史命名，實際使用的語言是由
    CHARACTER_SPEECH_LANGUAGE 這個環境變數決定，並不是寫死日文。

    system_prompt 若沒有指定，會在呼叫當下現場組出來（呼叫
    _build_speech_only_output_format_instructions()），確保吃到的是
    當下最新的語言設定，而不是一份過時、寫死語言的提示詞。

    history（多輪對話記憶，見 conversation.py）：
    - 有給的話，會把最近幾輪對話一起送給 LLM，角色才記得剛剛聊過什麼；
      生成成功之後，這一輪也會自動記進 history。
    - 沒給（None）的話，行為跟以前一樣：每次都是全新對話，不記任何東西。
    - 生成失敗（格式錯誤、連線失敗）的那一輪不會被記進去，
      避免壞掉的回覆留在歷史裡、把之後的回覆也帶偏。
    """
    if system_prompt is None:
        system_prompt = (
            _load_persona_text() + "\n" + _build_speech_only_output_format_instructions()
        )

    now = time.time()
    messages = [{"role": "system", "content": system_prompt}]
    if history is not None:
        messages += history.to_messages()
    # 使用者訊息一律加時間前綴（格式說明見 conversation.format_time_prefix），
    # 系統提示詞裡已經告訴模型這個標記的意思、不要照抄。
    # 最後補一句講話語言的提醒：規則離生成的位置越遠越不被遵守，系統提示詞裡的語言要求
    # 隔著一大串對話紀錄，常常輸給「歷史都是日文」「人設寫了日文口癖」的慣性（切成中文還是講日文、
    # 講日文時夾中文詞都是這個原因）。只加在這次送出的訊息，不存進對話紀錄。
    reminder = (
        f"\n\n（reply_speech 只能用{CHARACTER_SPEECH_LANGUAGE}，不能夾雜其他語言；〔〕表情標記除外。"
        "主人提到的作品、遊戲、人物、新聞，如果你說不出具體內容，就不要裝懂，只回〔查：關鍵字〕）"
    )
    messages.append({"role": "user", "content": format_time_prefix(now) + user_message + reminder})

    raw_content = _call_llm(messages, json_format=True)

    try:
        parsed = json.loads(raw_content)
        reply = SakikoReplyJa(**parsed)
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(f"LLM 回傳的格式不符預期，原始內容：{raw_content!r}") from e

    if history is not None:
        # 存「整理過的 JSON」而不是原始字串：原始字串可能夾雜多餘欄位或空白，
        # 存乾淨的版本，下一輪模型看到的歷史格式才會穩定一致。
        history.add_turn(
            user_message,
            json.dumps(reply.model_dump(), ensure_ascii=False),
            now,
        )

    return reply


def translate_zh(reply_speech: str) -> str:
    """把角色說的話翻譯成台灣用語的繁體中文，給使用者看。

    這支函式不在 TTS 的關鍵路徑上（TTS 不需要中文翻譯就能開始跑），
    呼叫端可以讓這支函式跟語音生成平行執行，藉此縮短使用者實際
    感受到的等待時間。
    """
    raw_content = _call_llm(
        [
            {"role": "system", "content": _build_zh_translate_system_prompt()},
            {"role": "user", "content": reply_speech},
        ],
        json_format=False,
    )
    return _fix_tw_terms(raw_content.strip().strip('"'))


def chat(user_message: str, system_prompt: Optional[str] = None) -> SakikoReply:
    """呼叫 LLM，回傳結構化結果：reply_speech（餵 TTS）、reply_zh（顯示給使用者）、tone（語氣標籤）。

    內部就是 chat_ja() + translate_zh() 依序執行的合併版本。

    在意延遲的呼叫端（例如 desktop_pet.py）應該直接分開呼叫
    chat_ja() / translate_zh()，讓語音生成跟翻譯平行跑，而不是
    用這支函式——這支函式是依序執行，兩次 LLM 呼叫的延遲會疊加。
    """
    if system_prompt is None:
        system_prompt = _load_persona_text() + "\n" + _build_output_format_instructions()

    reply_speech = chat_ja(user_message, system_prompt=system_prompt)
    reply_zh = translate_zh(reply_speech.reply_speech)

    return SakikoReply(
        reply_speech=reply_speech.reply_speech,
        reply_zh=reply_zh,
        tone=reply_speech.tone,
    )


# ============================================================
# 命令列進入點：讓開發者可以直接單獨測試這支腳本
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print('用法: python chat.py "你想說的話"')
        sys.exit(1)

    reply = chat(sys.argv[1])
    print(f"[角色語言/TTS用] {reply.reply_speech}")
    print(f"[中文顯示]   {reply.reply_zh}")
    print(f"[語氣]       {reply.tone}")
