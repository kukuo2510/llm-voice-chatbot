"""「工作」功能的常數表與純邏輯（不碰 Tk、不碰真實動畫控制器，方便單元測試）。

動畫名稱要跟角色資料夾 animations.json 的 sequences 名稱一致；沒定義的項目選單會灰掉。
"""

import random

# 選單項目（顯示名稱, sequence 名稱），順序就是選單順序。
WORK_JOBS = (
    ("打工", "work"),
    ("外送", "delivery"),
    ("廚師", "chef"),
    ("股票", "stock"),
)
WORK_SEQUENCES = dict(WORK_JOBS)
WORK_SEQUENCE_NAMES = frozenset(WORK_SEQUENCES.values())
STOCK_SEQUENCE = "stock"

# 股票：三支虛構股票（純動畫，不計錢）。
STOCK_NAMES = ("晨星集團", "月光科技", "海音娛樂")

# 結果 sequence 與抽中的權重（賺 45%、賠 55%，大賺大賠都偏少）。
STOCK_OUTCOMES = (
    ("stock-win-light", 26),
    ("stock-win-mid", 14),
    ("stock-win-great", 5),
    ("stock-lose-light", 26),
    ("stock-lose-mid", 20),
    ("stock-lose-great", 9),
)

# 每種結果的台詞（高傲大小姐口吻，要短）。
STOCK_LINES = {
    "stock-win-light": ("哼，小賺一筆而已。", "這點收益，不值得大驚小怪。", "嗯，在預料之中。"),
    "stock-win-mid": ("不錯，眼光果然沒有錯。", "哼，這是我應得的。", "賺到了呢，當然是我選得好。"),
    "stock-win-great": ("哼，這是當然的結果。", "大賺一筆……這種程度，我早就料到了。", "看見了嗎？這就是實力。"),
    "stock-lose-light": ("……只是小小的失誤而已。", "哼，這點損失根本不算什麼。", "暫時的，很快就會漲回來。"),
    "stock-lose-mid": ("怎麼會這樣……再觀察一下好了。", "市場太不識貨了。", "……不許笑，這只是一時的波動。"),
    "stock-lose-great": ("怎、怎麼可能……！", "……今天的事，不准告訴任何人。", "這、這一定是市場有問題！"),
}


def pick_outcome(rng=random) -> str:
    """依權重抽一個股票結果的 sequence 名稱。"""
    names = [n for n, _ in STOCK_OUTCOMES]
    weights = [w for _, w in STOCK_OUTCOMES]
    return rng.choices(names, weights=weights, k=1)[0]


def pick_line(outcome: str, rng=random) -> str:
    """該結果的隨機一句台詞；不認得的結果回傳空字串。"""
    lines = STOCK_LINES.get(outcome)
    return rng.choice(lines) if lines else ""


def defined_sequences(ctrl) -> dict:
    """動畫控制器目前定義的 sequences（沒有控制器就是空的）。"""
    if ctrl is None:
        return {}
    return getattr(ctrl.config, "sequences", None) or {}


def job_enabled(ctrl, sequence: str) -> bool:
    """這個工作能不能點：要有動畫控制器，而且 animations.json 有定義這個 sequence。"""
    return sequence in defined_sequences(ctrl)


def current_job(ctrl):
    """正在做的工作 sequence 名稱；沒有在工作回傳 None。"""
    if ctrl is None:
        return None
    cur = ctrl.current_sequence
    return cur if cur in WORK_SEQUENCE_NAMES else None


def start_job(ctrl, sequence: str) -> bool:
    """開始工作：正在做別的工作就先停掉。成功開始（或已排隊）回傳 True。"""
    if not job_enabled(ctrl, sequence):
        return False
    cur = current_job(ctrl)
    if cur == sequence:
        return False  # 已經在做同一件事
    if cur is not None:
        ctrl.stop(cur)
    return bool(ctrl.start(sequence))


def finish_job(ctrl) -> bool:
    """收工：只有目前在做工作才有效，回傳有沒有真的停。"""
    cur = current_job(ctrl)
    if cur is None:
        return False
    return bool(ctrl.stop(cur))


def settle_stock(ctrl, chosen: bool, rng=random):
    """股票選完（或放棄）：停掉 stock；選了就抽結果並播結果動畫。

    回傳 (結果名稱或 None, 台詞)。結果 sequence 沒定義就只回傳台詞、不播動畫。
    """
    if ctrl is not None:
        ctrl.stop(STOCK_SEQUENCE)
    if not chosen:
        return None, ""
    outcome = pick_outcome(rng)
    if ctrl is not None and outcome in defined_sequences(ctrl):
        ctrl.start(outcome)
    return outcome, pick_line(outcome, rng)
