"""桌寵主動搭話用的提示詞模板：Claude 事件通知、螢幕讀取。

從 desktop_pet.py 原封不動搬出來的。
"""

from typing import Optional


_CLAUDE_EVENT_PROMPTS = {
    "waiting": (
        "（主人請來幫忙寫程式的 AI 助手 Claude 停下來了，在等主人按確認才能繼續，主人可能沒注意到。"
        "用你平常的口吻提醒主人一句，要短，像是順口提醒，不要提到「通知」這類字眼。）"
    ),
    "done": (
        "（主人請來幫忙寫程式的 AI 助手 Claude 剛把交辦的工作做完了，前後花了大約 {minutes} 分鐘。"
        "用你平常的口吻跟主人說一聲，可以順便誇一下或吐槽一下，要短。）"
    ),
}

_SCREEN_WATCH_PROMPT_TEMPLATE = (
    "（你不經意瞄了一眼主人的螢幕，看到的畫面大概是：{caption}。"
    "用你平常的口吻主動跟主人講一句話，針對畫面裡具體的東西（例如提到的標題、"
    "遊戲名稱、正在做的事）給出有內容的反應、關心或吐槽，不要生硬地複述看到的內容，"
    "也不要提到「螢幕」「截圖」這類字眼——就自然地像是瞄了一眼隨口說一句話。"
    "**絕對不要講「需要幫忙嗎」「有什麼可以幫你的」這種制式客套話**，沒有具體東西"
    "好反應的話寧可換個角度吐槽或關心，也不要落入這種空話。）"
)
# 前景視窗的標題和程式名稱，讓角色知道是哪款遊戲／哪個網站，用她知道的相關知識聊
_SCREEN_WATCH_WINDOW_HINT = (
    "（主人現在的視窗標題是「{title}」，程式是 {process}。如果認得出是哪款遊戲、哪部作品或哪個網站，"
    "可以用你知道的相關知識聊——角色、玩法、劇情、梗都行，像是陪在旁邊看的人；不確定是什麼就別亂猜，直接問主人。）"
)
# 畫面沒重看、沿用上次的描述時（遊戲模式最常見）：明講已經講過了，不然每 20 秒都會講同一句
_SCREEN_WATCH_REPEAT_HINT = (
    "（這是你剛才看過的同一個畫面，你已經針對它講過話了。這次**不要再講一樣的內容**，"
    "換個角度：聊這款遊戲／這個東西本身、問主人進展、分享你的看法或感想，或閒聊別的；"
    "句子開頭也不要跟你前幾句一樣。）"
)


# 自動查資料時跳過的一般程式：瀏覽器、編輯器、聊天軟體這類，標題一直變、也不是「一款遊戲／作品」
_LOOKUP_SKIP_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "explorer.exe", "code.exe", "cursor.exe",
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "notepad.exe", "notepad++.exe", "discord.exe",
    "line.exe", "telegram.exe", "slack.exe", "teams.exe", "ms-teams.exe", "obsidian.exe", "winword.exe",
    "excel.exe", "powerpnt.exe", "steam.exe", "steamwebhelper.exe", "spotify.exe", "claude.exe", "electron.exe",
    "python.exe", "pythonw.exe", "taskmgr.exe", "applicationframehost.exe", "vlc.exe", "potplayermini64.exe",
}
_GAME_INFO_HINT = "（關於這個遊戲／作品，網路上查到的資料（可以參考，但用聊天的口吻講，不要照念）：{info}）"


def _screen_watch_prompt(caption: str, look: dict, info: Optional[str] = None) -> str:
    prompt = _SCREEN_WATCH_PROMPT_TEMPLATE.format(caption=caption)
    if look.get("title") or look.get("process"):
        prompt += _SCREEN_WATCH_WINDOW_HINT.format(title=look.get("title") or "（無）", process=look.get("process") or "未知")
    if info:
        prompt += _GAME_INFO_HINT.format(info=info)
    if not look.get("fresh"):
        prompt += _SCREEN_WATCH_REPEAT_HINT
    return prompt
