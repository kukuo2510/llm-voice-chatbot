"""表情標記：對話模型在台詞裡插〔害羞〕這種標記，桌寵唸之前拿掉，並算出它在句子裡的位置。

做法參考「表情別讓模型自由輸出參數，給一張封閉詞表就夠」：模型只能從 EXPRESSIONS 裡挑，
不會輸出奇怪的值把角色弄壞；鮮活感靠標記插在句子裡的位置（句首和句尾看起來是不同的人）。
每個詞實際長什麼樣子定義在 ../../livliv-stage/stage.js 的 EXPRESSIONS，兩邊的詞要一致。
"""
import re

EXPRESSIONS = ["平常", "開心", "得意", "害羞", "生氣", "不屑", "驚訝", "難過", "無奈", "想事情", "睏"]

# 表情 → 語音的語氣（characters/<角色>/voice_config.py 的 TONE_REFS）。一段語音用它的第一個表情決定語氣，
# 沒有標記或表情不在這裡的，用模型自己選的 tone。serious 最低沉，只給生氣。
TONE_FOR = {
    "開心": "happy", "得意": "happy",
    "害羞": "hesitant", "難過": "hesitant", "無奈": "hesitant", "睏": "hesitant",
    "生氣": "serious",
    "驚訝": "intense",
}

# 〔〕是指定的格式；模型偶爾寫成【】也收。括號內最多 6 個字，避免誤吃一般的括號內容
_TAG = re.compile(r"[〔【]([^〔〕【】]{1,6})[〕】]")


def instructions() -> str:
    """給對話模型看的說明，接在 reply_speech 欄位的說明後面。"""
    words = "、".join(EXPRESSIONS)
    return (
        f"。可以在 reply_speech 裡插表情標記，格式是〔詞〕，詞只能從這些選：{words}。"
        "標記放在表情改變的那個位置——例如句首定調、或講到一半語氣轉折的地方，同一個表情放的位置不同，"
        "看起來就是不同的人，所以要放準。一則回覆通常 1～3 個，不需要每句都加。"
        "標記不會被唸出來，也不算違反上面的語言規定。"
        "台詞本身也要跟表情一致，用說話語言裡自然的語氣詞帶出情緒，例如不屑時「ふん」／「哼」、"
        "驚訝時「えっ」／「欸」、無奈時「はぁ……」／「唉……」、想事情時「うーん」／「嗯……」，"
        "不要每句都用，語氣詞也算在台詞裡，會被唸出來"
    )


def tone_for(plan: list[dict], default: str) -> str:
    """這段語音該用的語氣：第一個有對應語氣的表情，沒有就用模型選的。"""
    for item in plan:
        if item["expr"] in TONE_FOR:
            return TONE_FOR[item["expr"]]
    return default


def split_tags(text: str) -> tuple[str, list[dict]]:
    """拿掉標記，回傳 (乾淨的台詞, 排程)。排程的 at 是標記在乾淨台詞裡的位置（0～1）。不認得的詞直接丟掉。"""
    clean, plan, last = [], [], 0
    length = 0
    for match in _TAG.finditer(text):
        piece = text[last:match.start()]
        clean.append(piece)
        length += len(piece)
        if match.group(1).strip() in EXPRESSIONS:
            plan.append({"at": length, "expr": match.group(1).strip()})
        last = match.end()
    clean.append(text[last:])
    result = "".join(clean).strip()
    total = max(1, len(result))
    for item in plan:
        item["at"] = round(min(1.0, item["at"] / total), 3)
    return result, plan


def split_chunks(chunks: list[str]) -> list[tuple[str, list[dict]]]:
    """每一段都拿掉標記。只剩標記、沒有台詞的段落，把它的表情移到下一段開頭。"""
    out, carry = [], []
    for chunk in chunks:
        clean, plan = split_tags(chunk)
        if not clean:
            carry += [{"at": 0.0, "expr": p["expr"]} for p in plan]
            continue
        out.append((clean, carry + plan))
        carry = []
    if carry and out:
        out[-1][1].extend({"at": 1.0, "expr": p["expr"]} for p in carry)
    return out
