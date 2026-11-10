"""把桌寵的「角色說話語言」（右鍵選單／.env 的 CHARACTER_SPEECH_LANGUAGE）
換成 GPT-SoVITS 的 text_lang 代碼。

背景（2026-11-10）：原本合成時一律用 text_lang="auto"。中文和日文共用漢字，
自動偵測常把一段純中文誤判成日文，用日文發音去唸，聽起來就像中日文混在一起。
說話語言是已知的，直接明確指定就不會猜錯。

- "zh"：中文，夾雜的英文單字仍用英文唸（比 "all_zh" 自然）
- 其他沒有對應的語言（例如使用者自訂的語言）退回 "auto"
"""

TEXT_LANG_BY_SPEECH_LANGUAGE = {
    "中文": "zh",
    "日文": "ja",
    "英文": "en",
    "韓文": "ko",
    "粵語": "yue",
}


def text_lang_for(speech_language: str) -> str:
    """回傳 GPT-SoVITS 的 text_lang；認不得的語言用 "auto"。"""
    return TEXT_LANG_BY_SPEECH_LANGUAGE.get((speech_language or "").strip(), "auto")
