"""
對話 AI 窗口：接收使用者輸入的中文，回傳「角色用指定語言的回覆」＋「中文翻譯」＋「語氣標籤」。

預設走本地 Ollama（qwen2.5:14b），不需要 API key、不需要網路。想改用雲端 LLM，
在 .env 裡設 `LLM_PROVIDER=openai`（預設是 local，不寫這行就跟以前一樣）並填
`OPENAI_API_KEY`——沒明確選 openai 的話，就算環境裡剛好有這個 key 也不會被拿去用，
不是「偵測到就自動切」。
前置需求（local）：Ollama 已安裝且服務在跑（背景常駐，`ollama serve`，通常裝好後開機
自動啟動），已下載 qwen2.5:14b（`ollama pull qwen2.5:14b`）。

角色實際開口說的語言由 `CHARACTER_SPEECH_LANGUAGE`（.env）決定，預設「日文」——
範例角色（豐川祥子）設計成講日文，換成自己的角色時改這個值就好，不用動程式碼。
給使用者看的翻譯固定是繁體中文（`reply_zh`），跟角色說話的語言是兩件獨立的事。

reply_speech/tone 生成跟 reply_zh 翻譯拆成兩支獨立函式（chat_ja / translate_zh），
是因為 TTS 只吃得到 reply_speech + tone，不需要等翻譯完成——呼叫端可以拿到
reply_speech 後立刻開始跑語音合成，同時平行呼叫 translate_zh，縮短使用者實際等待的時間。
chat() 保留為兩者的合併版本，供不在意這個延遲差異的呼叫端（例如互動式 CLI）使用。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.env import load_dotenv  # noqa: E402

load_dotenv()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

# 角色開口說話用的語言，換角色/換語言時只用改這個值，不用碰下面的提示詞模板
CHARACTER_SPEECH_LANGUAGE = os.environ.get("CHARACTER_SPEECH_LANGUAGE", "日文")

# 要選了才會切，不是偵測到 OPENAI_API_KEY 存在就自動切走
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "local").strip().lower()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

TONE_OPTIONS = ["neutral", "happy", "serious", "hesitant", "intense"]

# 角色人設放在 characters/<角色>/persona.md，改台詞/個性直接編輯那個檔案就好，
# 不用碰這支程式碼；要換角色，把這裡的 "sakiko" 換成你自己的角色資料夾名稱
PERSONA_PATH = Path(__file__).resolve().parents[2] / "characters" / "sakiko" / "persona.md"

_OUTPUT_FORMAT_INSTRUCTIONS = f"""
使用者會用中文輸入。你要回傳一個 JSON，只回傳 JSON，不要有其他文字，格式如下：
{{
  "reply_speech": "用{CHARACTER_SPEECH_LANGUAGE}，以角色的口吻自然回覆——這是要餵給語音合成的，內容本身就要是完整、自然、適合唸出來的{CHARACTER_SPEECH_LANGUAGE}，不要中英日等語言夾雜",
  "reply_zh": "reply_speech 的中文翻譯，給使用者看。一定要用「繁體中文」書寫，且用詞、語感要符合台灣的日常用語習慣（例如：軟體不是软件、網路不是网络、隨身碟不是U盘、你好不是妳好），不要出現任何簡體字或中國大陸慣用詞彙",
  "tone": "依這句回覆的情緒基調，從這幾個選項選一個：neutral（平靜）／happy（溫暖開心）／serious（嚴肅）／hesitant（猶豫低落）／intense（情緒強烈）"
}}
"""

_SPEECH_ONLY_OUTPUT_FORMAT_INSTRUCTIONS = f"""
使用者會用中文輸入。你要回傳一個 JSON，只回傳 JSON，不要有其他文字，格式如下：
{{
  "reply_speech": "用{CHARACTER_SPEECH_LANGUAGE}，以角色的口吻自然回覆——這是要餵給語音合成的，內容本身就要是完整、自然、適合唸出來的{CHARACTER_SPEECH_LANGUAGE}，不要中英日等語言夾雜",
  "tone": "依這句回覆的情緒基調，從這幾個選項選一個：neutral（平靜）／happy（溫暖開心）／serious（嚴肅）／hesitant（猶豫低落）／intense（情緒強烈）"
}}
"""

# 下面這份翻譯提示詞的第一段（語言、用詞）是通用邏輯，換角色/換語言都適用；
# 但「ですわ／ますの語尾」那一段是範例角色（豐川祥子）的專屬說話習慣，換成
# 自己的角色或語言時，這段連同下面的專有名詞讀音對照都要換成你自己的設定
_ZH_TRANSLATE_SYSTEM_PROMPT = f"""你是專業的{CHARACTER_SPEECH_LANGUAGE}翻譯，把使用者提供的{CHARACTER_SPEECH_LANGUAGE}句子翻譯成繁體中文，且用詞、語感要符合台灣的日常用語習慣
（例如：軟體不是软件、網路不是网络、隨身碟不是U盘、你好不是妳好），不要出現任何簡體字或中國大陸慣用詞彙。

原文角色說話帶日文貴族大小姐語尾（ですわ／ますの），這種語氣中文沒有直接對應的文法，
不要逐字翻譯語尾，改用中文裡對應的優雅語感表現，例如：句尾適度使用「呢」、「罷了」、
「才是」，自稱/敬語可以用「您」，用詞偏文雅、不要口語化或俏皮的語助詞（不要用「啦」
「唷」「喔」這種輕鬆語氣詞）。不要每句都硬加語尾湊字數，語氣自然優先。

樂團名、代號、作品名這類專有名詞（例如 Ave Mujica、Oblivionis、CRYCHIC、MyGO!!!!!、
Morfonica）維持原文羅馬字，不要音譯成中文字（不要寫成「阿維·穆吉卡」「奧布利比尼斯」
這種音譯），人名一樣翻成對應的中文漢字（例如 豐川祥子、若葉睦）。

只回傳翻譯後的中文句子，不要有其他文字、不要加引號。"""


def _load_persona_text() -> str:
    return PERSONA_PATH.read_text(encoding="utf-8").strip()


DEFAULT_SYSTEM_PROMPT = _load_persona_text() + "\n" + _OUTPUT_FORMAT_INSTRUCTIONS
DEFAULT_JA_SYSTEM_PROMPT = _load_persona_text() + "\n" + _SPEECH_ONLY_OUTPUT_FORMAT_INSTRUCTIONS


class SakikoReply(BaseModel):
    reply_speech: str
    reply_zh: str
    tone: Literal["neutral", "happy", "serious", "hesitant", "intense"]


class SakikoReplyJa(BaseModel):
    reply_speech: str
    tone: Literal["neutral", "happy", "serious", "hesitant", "intense"]


# qwen2.5 就算提示詞要求繁體/台灣用語，還是偶爾會漏掉幾個大陸慣用詞，
# 這裡對最常見的幾個做事後校正當保險（純字串取代，只挑不會有歧義的詞）
_TW_TERM_FIXES = {
    "软件": "軟體", "軟件": "軟體",
    "网络": "網路", "網絡": "網路",
    "视频": "影片", "視頻": "影片",
    "信息": "訊息",
    "硬盘": "硬碟", "硬碟盤": "硬碟",
    "U盘": "隨身碟", "u盘": "隨身碟",
    "打印": "列印",
    "鼠标": "滑鼠",
    "服务器": "伺服器", "服務器": "伺服器",
    "程序": "程式",
    "质量": "品質",
    "短信": "簡訊",
}


def _fix_tw_terms(text: str) -> str:
    for wrong, right in _TW_TERM_FIXES.items():
        text = text.replace(wrong, right)
    return text


class OllamaNotRunningError(RuntimeError):
    pass


class OpenAIRequestError(RuntimeError):
    pass


def _call_ollama(messages: list[dict], *, json_format: bool, timeout: int = 120) -> str:
    """打一次 Ollama chat API，回傳 content 原始字串（未 parse）"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
    }
    if json_format:
        payload["format"] = "json"
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        raise OllamaNotRunningError(
            "連不上本地 Ollama 服務（http://localhost:11434）。"
            "請確認 Ollama 已啟動（工作列有沒有 Ollama 圖示，或執行 `ollama serve`）。"
        ) from e
    return data["message"]["content"]


def _call_openai(messages: list[dict], *, json_format: bool, timeout: int = 120) -> str:
    """打一次 OpenAI Chat Completions API，回傳 content 原始字串（未 parse）"""
    if not OPENAI_API_KEY:
        raise OpenAIRequestError(
            "LLM_PROVIDER=openai 但沒有設定 OPENAI_API_KEY，"
            "請在 .env 裡填入你自己的 OpenAI API key。"
        )
    payload: dict = {
        "model": OPENAI_CHAT_MODEL,
        "messages": messages,
    }
    if json_format:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        OPENAI_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise OpenAIRequestError(f"OpenAI API 回傳錯誤（HTTP {e.code}）：{body}") from e
    except urllib.error.URLError as e:
        raise OpenAIRequestError(f"連不上 OpenAI API，檢查網路連線：{e}") from e
    return data["choices"][0]["message"]["content"]


def _call_llm(messages: list[dict], *, json_format: bool, timeout: int = 120) -> str:
    """依 LLM_PROVIDER 分流打本地 Ollama 或 OpenAI，兩邊回傳格式在這裡已經對齊，
    呼叫端不用管是哪一個供應商。印一行到主控台，方便切換 provider 之後直接
    肉眼確認這次真的打去哪裡，不用另外寫測試腳本。"""
    if LLM_PROVIDER == "openai":
        print(f"[chat] 對話走雲端 OpenAI（{OPENAI_CHAT_MODEL}）")
        return _call_openai(messages, json_format=json_format, timeout=timeout)
    print(f"[chat] 對話走本地 Ollama（{MODEL}）")
    return _call_ollama(messages, json_format=json_format, timeout=timeout)


def chat_ja(user_message: str, system_prompt: str = DEFAULT_JA_SYSTEM_PROMPT) -> SakikoReplyJa:
    """只生成角色語言的回覆＋語氣標籤（給 TTS 用），不含中文翻譯，走最短路徑。
    函式名沿用 chat_ja 是歷史命名，實際語言由 CHARACTER_SPEECH_LANGUAGE 決定，
    不是寫死日文。"""
    raw_content = _call_llm(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        json_format=True,
    )
    try:
        parsed = json.loads(raw_content)
        return SakikoReplyJa(**parsed)
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(f"LLM 回傳的格式不符預期，原始內容：{raw_content!r}") from e


def translate_zh(reply_speech: str) -> str:
    """把角色說的話翻譯成台灣用語的繁體中文，給使用者看（不在 TTS 的關鍵路徑上，可跟語音生成平行跑）"""
    raw_content = _call_llm(
        [
            {"role": "system", "content": _ZH_TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": reply_speech},
        ],
        json_format=False,
    )
    return _fix_tw_terms(raw_content.strip().strip('"'))


def chat(user_message: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> SakikoReply:
    """呼叫本地 LLM，回傳結構化結果：reply_speech（餵 TTS）、reply_zh（顯示給使用者）、tone（語氣標籤）

    內部就是 chat_ja + translate_zh 的合併版本，兩次呼叫依序執行。
    在意延遲的呼叫端（例如 desktop_pet.py）應該直接分開呼叫 chat_ja / translate_zh，
    讓語音生成跟翻譯平行跑，而不是用這支函式。
    """
    reply_speech = chat_ja(user_message, system_prompt=system_prompt)
    reply_zh = translate_zh(reply_speech.reply_speech)
    return SakikoReply(reply_speech=reply_speech.reply_speech, reply_zh=reply_zh, tone=reply_speech.tone)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print('用法: python chat.py "你想說的話"')
        sys.exit(1)

    reply = chat(sys.argv[1])
    print(f"[角色語言/TTS用] {reply.reply_speech}")
    print(f"[中文顯示]   {reply.reply_zh}")
    print(f"[語氣]       {reply.tone}")
