"""上網查資料（免費）：用維基百科的公開 API，不用金鑰、不花錢，查到的整理還是交給本機的對話模型。

兩種用法：
1. 自動：桌寵看到前景換成新的遊戲／程式，在背景查一次（desktop_pet.py 的 _game_info_for）。
2. 角色自己決定：她不確定答案時，回覆只寫〔查：關鍵字〕，桌寵查完再讓她根據資料回答（desktop_pet.py 的 _handle_message）。

只會送出「要查的關鍵字」（例如遊戲名稱），不會送截圖或對話內容。結果存在 .lookup_cache.json，同一個關鍵字不重查。
"""
import json
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent / ".lookup_cache.json"
MAX_CHARS = 900  # 給對話模型看的資料長度上限，太長會拖慢回應
LANGS = ("zh", "ja", "en")  # 依序試：中文（轉成台灣用字）、日文、英文
USER_AGENT = "DesktopPetLookup/1.0 (personal hobby project)"  # 維基百科要求要有 User-Agent

_REQUEST = re.compile(r"〔\s*查[:：]\s*([^〔〕]{1,40})〕")
_lock = threading.Lock()


def instructions() -> str:
    """給對話模型看的說明，接在 reply_speech 欄位的說明後面。"""
    return (
        "。如果主人問的事你不確定、或可能是你不知道的新資訊（新遊戲、新角色、改版、新聞、你沒聽過的東西），"
        "這次的 reply_speech 只寫〔查：要查的關鍵字〕，不要寫別的，程式會幫你查，查完會再請你回答；"
        "確定知道的就直接回答，不要沒事亂查"
    )


# 角色說自己不確定的講法。本機模型很少主動回〔查：…〕，但會老實說不清楚，所以改由程式抓這句話再問要不要查
_UNSURE = re.compile(
    r"不太清楚|不清楚|不確定|不太確定|沒聽過|沒聽說過|不太了解|不太熟|不知道|記不清|具體細節|"
    r"わからない|分からない|知らない|詳しくない|よく知り|確かではない|聞いたことがない"
)


# 主人明講要查的講法：這種一定查，不靠模型自己判斷
_ASKED = re.compile(r"查一下|查查|幫我查|上網查|去查|搜尋|搜一下|google|估狗|調べて|検索", re.I)


def sounds_unsure(text: str) -> bool:
    return bool(_UNSURE.search(text or ""))


def asked_to_search(text: str) -> bool:
    return bool(_ASKED.search(text or ""))


def query_prompt(question: str) -> str:
    """問角色要查什麼（她說不確定、或主人叫她查的時候）。查不到的私事、記憶、感受就回〔不用查〕。"""
    return (f"（主人剛才說：「{question}」。如果這需要上網查、而且是查得到的知識——作品、遊戲、人物、事件、名詞——"
            "只回〔查：要查的關鍵字〕，關鍵字用作品或人物的正式名稱；"
            "如果是私事、你的記憶、感受這種查不到的，只回〔不用查〕。）")


def find_request(text: str) -> str | None:
    """回覆裡有〔查：…〕就回傳關鍵字。"""
    match = _REQUEST.search(text or "")
    return match.group(1).strip() if match else None


def strip_requests(text: str) -> str:
    return _REQUEST.sub("", text or "").strip()


def followup_prompt(query: str, result: str | None) -> str:
    if result:
        return (f"（你剛剛查了「{query}」，查到的資料如下：\n{result}\n"
                "根據這些資料，用你平常的口吻回答主人剛才的問題，挑重點講、不要照念；資料沒提到的就說不確定，不要再查一次。）")
    return f"（你剛剛查了「{query}」，但什麼都沒查到。老實跟主人說你查不到、不確定，不要亂編，也不要再查一次。）"


def clean_title(title: str) -> str:
    """視窗標題整理成適合搜尋的名稱：去掉商標符號、Client、版本號這類雜訊。"""
    text = re.sub(r"\((TM|R)\)|[™®©]", "", title or "", flags=re.I)
    text = re.split(r"\s[-–—|]\s", text)[0]  # 「遊戲名 - 子標題」只留前面
    text = re.sub(r"\b(client|launcher|game|v?\d+(\.\d+)+)\b", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


def _wikipedia(query: str, lang: str) -> str | None:
    api = f"https://{lang}.wikipedia.org/w/api.php?"
    found = _get(api + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": query, "srlimit": 1, "format": "json"}))
    hits = found.get("query", {}).get("search", [])
    if not hits:
        return None
    params = {"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "redirects": 1,
              "titles": hits[0]["title"], "format": "json"}
    if lang == "zh":
        params["variant"] = "zh-tw"
    pages = _get(api + urllib.parse.urlencode(params)).get("query", {}).get("pages", {})
    extract = next(iter(pages.values()), {}).get("extract", "").strip()
    return f"【{hits[0]['title']}】{extract}" if extract else None


def search(query: str) -> str | None:
    """查一個關鍵字，回傳一段摘要（最多 MAX_CHARS 字），查不到或連不上回傳 None。"""
    query = (query or "").strip()
    if not query:
        return None
    with _lock:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    if query in cache:
        return cache[query]
    result = None
    for lang in LANGS:
        try:
            result = _wikipedia(query, lang)
        except Exception as e:
            print(f"[web_lookup] 查「{query}」（{lang}）失敗：{e}")
            continue
        if result:
            break
    if result:
        result = result[:MAX_CHARS]
        with _lock:
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
            cache[query] = result
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[web_lookup] 查「{query}」：{'有資料' if result else '沒查到'}")
    return result
