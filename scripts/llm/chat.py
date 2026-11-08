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

# 語氣欄位的說明。serious 的參考音檔是五個裡最低沉的（約 243 Hz），
# 原本只寫「依情緒基調選」時，高傲的角色加上一直吐槽螢幕，模型有一半以上的句子都選 serious，
# 聽起來整天都很陰沉（2026-11-08 量過最近 20 句，12 句在 240～250 Hz）。
# 所以把 serious 限定在真的生氣、訓人的時候，其他拿不準的一律 neutral。
_TONE_FIELD_INSTRUCTIONS = (
    "這句話要用哪種語氣唸，從這幾個選一個：neutral（平常講話，拿不準就選這個）／"
    "happy（開心、溫柔）／serious（只有真的生氣、板起臉訓人、嚴厲警告時才用；"
    "高傲、吐槽、嘴硬、冷淡、一般的認真說明都不算，那些用 neutral）／"
    "hesitant（猶豫、低落、難過）／intense（情緒激動、爆發）"
)

# 角色人設放在 characters/<角色>/persona.md，要改台詞風格/個性設定，
# 直接編輯那個檔案就好，不用碰這支程式碼。
#
# 要換角色時，把這裡的 "sakiko" 換成你自己的角色資料夾名稱即可。
PERSONA_PATH = Path(__file__).resolve().parents[2] / "characters" / "sakiko" / "persona.md"


# ============================================================
# 提示詞（prompt）組裝
# ============================================================
#
# 注意：下面這幾份提示詞刻意寫成「函式」，而不是預先組好、存成模組層級
# 常數的字串。原因是 CHARACTER_SPEECH_LANGUAGE 有可能在程式執行期間
# 被改掉（例如桌寵的右鍵選單提供切換語言的功能），如果寫成函式、
# 每次呼叫時才現場組字串，下一次呼叫就會自動套用新的語言設定，
# 不需要重新啟動程式。
#
# 反之，如果寫成模組載入當下就固定好的字串常數，之後即使改掉
# CHARACTER_SPEECH_LANGUAGE 這個變數，也不會反映到提示詞裡——
# 因為字串在模組載入的那一刻，就已經把舊的語言值內插、寫死進去了。


def _reply_speech_field_instructions() -> str:
    """組出「reply_speech 這個欄位該怎麼寫」的提示詞片段。

    背景：qwen2.5 用中文下指令時，即使提示詞已經明確要求輸出
    {CHARACTER_SPEECH_LANGUAGE}，還是偶爾會有整個詞彙沒轉換、
    直接冒出中文字詞，或是照抄使用者輸入裡剛好出現的英文單字的狀況
    （這是模型訓練資料以中文為主的慣性，加上「偷懶照抄輸入」兩個
    因素疊加造成的）。

    只講「不要語言夾雜」這種籠統的要求並不夠，必須明確講「原文字母、
    漢字一個都不行出現」，並且提醒模型自我檢查一遍。

    這對 TTS（語音合成）來說不只是「聽起來好不好」的問題，而是實質
    會出錯：GPT-SoVITS 的 text_lang="auto" 模式，只要偵測到一段
    羅馬字母，就會切去用英文發音引擎去唸那一段，夾在其他語言的句子
    中間會唸得亂七八糟。所以就算是使用者聊天時隨口用的英文單字，
    也必須換成目標語言裡道地的外來語講法/發音寫法，不能照抄原文字母。
    """
    lang = CHARACTER_SPEECH_LANGUAGE
    return (
        f"用{lang}，以角色的口吻自然回覆——這是要餵給語音合成的，內容本身就要是完整、自然、"
        f"適合唸出來的{lang}。這欄位絕對不能出現任何一個非{lang}的原文文字（中文字、英文"
        f"字母都不行），即使使用者輸入裡剛好用了中文詞彙、英文單字/句子，也要換成{lang}裡"
        f"道地的講法——如果是{lang}沒有的外來語詞彙，要轉換成{lang}的外來語發音寫法，不能"
        f"照抄原文字母；寫完之後自己檢查一遍，確認整句都是{lang}才輸出"
        + expressions.instructions()
        + web_lookup.instructions()
    )


def _build_output_format_instructions() -> str:
    """組出完整版（含中文翻譯欄位）的輸出格式提示詞。"""
    return f"""
使用者會用中文輸入。使用者訊息開頭的 [YYYY-MM-DD HH:MM] 是對方說話的時間，給你參考現在幾點、
距離上一句話隔了多久，不要在回覆裡寫出這個時間標記。
你要回傳一個 JSON，只回傳 JSON，不要有其他文字，格式如下：
{{
  "reply_speech": "{_reply_speech_field_instructions()}",
  "reply_zh": "reply_speech 的中文翻譯，給使用者看。一定要用「繁體中文」書寫，且用詞、語感要符合台灣的日常用語習慣（例如：軟體不是软件、網路不是网络、隨身碟不是U盘、你好不是妳好），不要出現任何簡體字或中國大陸慣用詞彙",
  "tone": "{_TONE_FIELD_INSTRUCTIONS}"
}}
"""


def _build_speech_only_output_format_instructions() -> str:
    """組出精簡版（不含中文翻譯欄位）的輸出格式提示詞。

    給 chat_ja() 使用——只需要 reply_speech + tone 就能開始跑 TTS，
    不必等 LLM 連中文翻譯也一起生成完才回傳，藉此縮短第一段語音
    開始播放前的等待時間。
    """
    return f"""
使用者會用中文輸入。使用者訊息開頭的 [YYYY-MM-DD HH:MM] 是對方說話的時間，給你參考現在幾點、
距離上一句話隔了多久，不要在回覆裡寫出這個時間標記。
你要回傳一個 JSON，只回傳 JSON，不要有其他文字，格式如下：
{{
  "reply_speech": "{_reply_speech_field_instructions()}",
  "tone": "{_TONE_FIELD_INSTRUCTIONS}"
}}
"""


def _build_zh_translate_system_prompt() -> str:
    """組出「把角色說的話翻譯成台灣用語繁體中文」的系統提示詞。

    這份提示詞分成三段，各自的通用程度不同：
    - 第一段（語言、用詞規範）：通用邏輯，換角色、換語言都適用，不用改。
    - 第二段（貴族大小姐語尾的翻譯方式）：這是範例角色（豐川祥子）
      專屬的說話習慣，換成自己的角色或語言時，這一段要換成符合
      新角色設定的翻譯風格。
    - 第三段（專有名詞的翻譯規則）：同樣是範例角色專屬的設定，
      換角色時要一併更新成新角色相關的專有名詞清單。
    """
    return f"""你是專業的{CHARACTER_SPEECH_LANGUAGE}翻譯，把使用者提供的{CHARACTER_SPEECH_LANGUAGE}句子翻譯成繁體中文，且用詞、語感要符合台灣的日常用語習慣
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
    """讀取角色人設檔（persona.md）的完整內容。"""
    return PERSONA_PATH.read_text(encoding="utf-8").strip()


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
# 台灣用語事後校正（保險機制）
# ============================================================
#
# qwen2.5 就算提示詞已經要求繁體中文／台灣用語，還是偶爾會漏掉幾個
# 中國大陸慣用詞彙。這裡對最常見的幾個詞做「事後校正」當作最後一道
# 保險，純粹是字串取代，只挑不會有歧義、不會誤傷正常詞句的詞彙。
#
# 注意：這份字典的 key（左邊）本身就是要被抓出來替換掉的簡體字／
# 大陸用語，是「比對用的字面文字」，不是我們自己輸出的措辞，
# 所以這裡刻意保留原樣，不能改成繁體——改了就抓不到要取代的目標了。
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
    """依 _TW_TERM_FIXES 逐條做字串取代，把混進來的大陸用語校正成台灣用語。"""
    for wrong, right in _TW_TERM_FIXES.items():
        text = text.replace(wrong, right)
    return text


# ============================================================
# LLM 呼叫（依 provider 分流）
# ============================================================


class OllamaNotRunningError(RuntimeError):
    """連不上本地 Ollama 服務時拋出。"""


class OpenAIRequestError(RuntimeError):
    """呼叫 OpenAI API 失敗時拋出（缺 key、HTTP 錯誤、網路錯誤都算）。"""


def _call_ollama(messages: list[dict], *, json_format: bool, timeout: int = 120) -> str:
    """打一次 Ollama chat API，回傳 content 原始字串（尚未 parse 成物件）。"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        # gemma4 等新模型預設會先「思考」再回答，不關掉的話每句要多等 3～7 倍時間
        # （2026-10-10 實測 gemma4:12b：關掉約 6 秒、沒關 15～46 秒）。
        # 桌寵對話不需要推理，沒有思考功能的舊模型收到這個參數也會直接忽略。
        "think": False,
        "options": {"num_ctx": OLLAMA_NUM_CTX},
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
    """打一次 OpenAI Chat Completions API，回傳 content 原始字串（尚未 parse 成物件）。"""
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
    """依 LLM_PROVIDER 分流打本地 Ollama 或 OpenAI。

    兩邊的回傳格式在這一層已經對齊成同一種（純字串），呼叫端完全
    不用管實際是打去哪個供應商。同時會印一行訊息到主控台，方便切換
    provider 之後，直接用肉眼確認這次真的打去哪裡，不用另外寫測試腳本。
    """
    if LLM_PROVIDER == "openai":
        print(f"[chat] 對話走雲端 OpenAI（{OPENAI_CHAT_MODEL}）")
        return _call_openai(messages, json_format=json_format, timeout=timeout)

    print(f"[chat] 對話走本地 Ollama（{MODEL}）")
    return _call_ollama(messages, json_format=json_format, timeout=timeout)


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
