"""把一整段台詞切成適合「逐段合成、邊合成邊播放」的小段落。

## 為什麼要切

原本的流程是整段台詞一次丟給 GPT-SoVITS，等整段都合成完才開始播放。
台詞一長，使用者就要乾等好幾秒才聽到第一個字。

改成切段之後：第一段合成完就先開始播，播放的同時在背景合成下一段，
使用者實際等待的時間只剩「第一段」的合成時間。

## 切法（參考 Project AIRI 的 tts-chunker）

AIRI 的 pipelines-audio/src/processors/tts-chunker.ts 做法重點：
- 硬標點（。！？…換行）一定切。
- 軟標點（、，：；）平常不切，但「開頭前幾段」可以提早在軟標點切
  （boost），讓第一段更早出來。
- 每段有最小長度：TTS 每次請求都有固定成本，切太碎反而更慢，
  而且太短的片段 GPT-SoVITS 念起來語調會怪。
- 小數點（3.5）、刪節號（...）不能當成句號切。

AIRI 是用「字數（word）」計算長度，那是給英文設計的；這裡的角色主要講
日文／中文，沒有空白分詞，所以改用「字元數」計算。
"""

# 一定會切的句尾標點。
HARD_PUNCTUATIONS = set("。．.？?！!…⋯～~\n")

# 平常不切、只有「開頭幾段」且長度夠時才會切的標點。
SOFT_PUNCTUATIONS = set("、，,：:；;")

# 緊接在句尾標點後面、要跟著前一段走的字元（右引號、右括號，以及連續的
# 標點，例如「本当に！？」「……」），不然會切出一段只有「」」的碎片。
TRAILING_CLOSERS = set("」』）)】\"'”’") | HARD_PUNCTUATIONS

# 每段至少要這麼多字元才會被切出來；不夠長就跟下一段合併。
DEFAULT_MIN_CHARS = 8

# 單段超過這麼多字元時，遇到軟標點就強制切開，避免單段太長又變回乾等。
DEFAULT_MAX_CHARS = 60

# 開頭幾段可以在軟標點提早切（AIRI 的 boost 預設是 2）。
DEFAULT_BOOST = 1


def _is_decimal_point(text: str, i: int) -> bool:
    """text[i] 是「.」或「,」，且前後都是數字（例如 3.5、1,000）時，不該當成標點切開。"""
    return (
        text[i] in ".,"
        and 0 < i < len(text) - 1
        and text[i - 1].isdigit()
        and text[i + 1].isdigit()
    )


def split_for_tts(
    text: str,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    boost: int = DEFAULT_BOOST,
) -> list[str]:
    """把台詞切成一段一段，回傳段落清單（每段都保留原本的標點）。

    範例：
        >>> split_for_tts("ごきげんよう。今日はいい天気ですわね、お散歩でもいかが？")
        ['ごきげんよう。今日はいい天気ですわね、', 'お散歩でもいかが？']

    （「ごきげんよう。」只有 7 個字元，不到 min_chars，所以跟後面合併；
    第一段有 boost，可以在軟標點「、」提早切。）
    """
    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    current = ""
    i = 0

    while i < len(text):
        ch = text[i]
        current += ch
        i += 1

        if _is_decimal_point(text, i - 1):
            continue

        is_hard = ch in HARD_PUNCTUATIONS
        is_soft = ch in SOFT_PUNCTUATIONS
        if not (is_hard or is_soft):
            continue

        # 把緊跟在後面的右引號／連續標點一起收進這一段。
        while i < len(text) and text[i] in TRAILING_CLOSERS:
            current += text[i]
            i += 1

        length = len(current.strip())
        should_cut = (
            (is_hard and length >= min_chars)
            or (is_soft and length >= min_chars and len(chunks) < boost)
            or (is_soft and length >= max_chars)
        )
        if should_cut:
            chunks.append(current.strip())
            current = ""

    # 最後剩下的尾巴：夠長就自成一段，太短就併進前一段，
    # 避免最後冒出一段只有兩三個字的碎片。
    tail = current.strip()
    if tail:
        if chunks and len(tail) < min_chars:
            chunks[-1] += tail
        else:
            chunks.append(tail)

    return chunks


if __name__ == "__main__":
    import sys

    sample = sys.argv[1] if len(sys.argv) > 1 else (
        "ごきげんよう。今日はいい天気ですわね、お散歩でもいかが？"
        "……べ、別にあなたのためではありませんわ！3.5時間も待ったのですから。"
    )
    for n, chunk in enumerate(split_for_tts(sample), 1):
        print(f"{n}: {chunk}")
