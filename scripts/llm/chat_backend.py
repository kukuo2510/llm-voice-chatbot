"""LLM 呼叫：依 provider 分流打本地 Ollama 或 OpenAI（從 chat.py 搬出來）。

供應商、模型、網址這些設定的「本尊」留在 chat.py，這裡每次呼叫都去 chat 模組拿當下的值，
所以桌寵執行期改 chat.LLM_PROVIDER 會立刻影響這裡的分流。
"""

import json
import urllib.error
import urllib.request


def _chat():
    """回傳 chat 模組，讀設定值一律走它。

    CHARACTER_SPEECH_LANGUAGE、LLM_PROVIDER、MODEL 這些設定的「本尊」留在 chat.py：
    桌寵的右鍵選單會在執行期直接改 chat.LLM_PROVIDER / chat.CHARACTER_SPEECH_LANGUAGE，
    所以這裡不能在 import 時複製一份值，每次呼叫都要現場去 chat 模組拿當下的值。
    （放在函式裡才 import，是為了避開 chat <-> 這個模組的循環 import。）
    """
    import chat

    return chat


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
        "model": _chat().MODEL,
        "messages": messages,
        "stream": False,
        # gemma4 等新模型預設會先「思考」再回答，不關掉的話每句要多等 3～7 倍時間
        # （2026-10-10 實測 gemma4:12b：關掉約 6 秒、沒關 15～46 秒）。
        # 桌寵對話不需要推理，沒有思考功能的舊模型收到這個參數也會直接忽略。
        "think": False,
        "options": {"num_ctx": _chat().OLLAMA_NUM_CTX},
    }
    if json_format:
        payload["format"] = "json"

    req = urllib.request.Request(
        _chat().OLLAMA_URL,
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
    if not _chat().OPENAI_API_KEY:
        raise OpenAIRequestError(
            "LLM_PROVIDER=openai 但沒有設定 OPENAI_API_KEY，"
            "請在 .env 裡填入你自己的 OpenAI API key。"
        )

    payload: dict = {
        "model": _chat().OPENAI_CHAT_MODEL,
        "messages": messages,
    }
    if json_format:
        payload["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        _chat().OPENAI_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_chat().OPENAI_API_KEY}",
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
    if _chat().LLM_PROVIDER == "openai":
        print(f"[chat] 對話走雲端 OpenAI（{_chat().OPENAI_CHAT_MODEL}）")
        return _call_openai(messages, json_format=json_format, timeout=timeout)

    print(f"[chat] 對話走本地 Ollama（{_chat().MODEL}）")
    return _call_ollama(messages, json_format=json_format, timeout=timeout)
