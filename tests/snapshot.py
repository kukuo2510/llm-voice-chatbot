"""重構前後的行為快照測試。

用法（在專案根目錄，用專案的 Python）：

    python tests/snapshot.py record   # 在「原始程式」上錄下 golden（tests/golden/snapshot.json）
    python tests/snapshot.py check    # 重構後重跑，和 golden 比對，一個 byte 都不能差

比對內容：
- 純函式輸出（提示詞組裝、表情標記、上網查資料的提示詞、TTS 切段、嘴型包絡等）。
- 公開介面：每個模組的 dir() 名稱、CharacterWindow 的方法名稱，重構後只能多、不能少。
- 模組層級的常數值（含 .env 載入順序造成的值，會用暫時的 .env 另跑一輪）。
- 執行期切換：chat.LLM_PROVIDER / chat.CHARACTER_SPEECH_LANGUAGE / chat.MODEL / stt.STT_PROVIDER /
  screen_watch.game_mode 改了之後，打出去的 HTTP 請求和提示詞要跟著變。
- CharacterWindow 的建構流程與各個事件處理函式：用假的 tkinter 元件把「呼叫順序」記成文字，
  選單順序、__init__ 的陳述順序、after()／Thread 排程順序都會被比到。

安全：全程不碰真實的 Ollama／OpenAI／TTS 服務、不開 GUI、不起 UDP；偏好檔、對話記憶、查詢快取、
outputs/ 都不會被寫入（偏好檔改指到暫存資料夾、HTTP 與 TTS 合成被假的取代）。
"""
import contextlib
import hashlib
import io
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "snapshot.json"
SCRIPTS = ROOT / "scripts"

# 會被 .env／環境影響的變數，worker 啟動前一律清掉，確保兩邊起點一致。
_ENV_KEYS = [
    "LLM_PROVIDER", "STT_PROVIDER", "CHARACTER_SPEECH_LANGUAGE", "OLLAMA_MODEL", "OLLAMA_URL",
    "OLLAMA_NUM_CTX", "OPENAI_API_KEY", "OPENAI_CHAT_MODEL", "DESKTOP_PET_AVATAR_WIDTH",
    "SCREEN_WATCH_INTERVAL_SEC", "CLAUDE_EVENT_PORT", "LIVLIV_STAGE", "LIVLIV_UDP_PORT",
    "TTS_API_URL", "CHAT_HISTORY_TURNS",
]

_DOTENV_TEXT = (
    "DESKTOP_PET_AVATAR_WIDTH=333\n"
    "SCREEN_WATCH_INTERVAL_SEC=77\n"
    "CLAUDE_EVENT_PORT=9999\n"
    "LLM_PROVIDER=openai\n"
    "CHARACTER_SPEECH_LANGUAGE=英文\n"
    "OLLAMA_MODEL=m-test\n"
    "STT_PROVIDER=openai\n"
)


# ============================================================
# 假的 tkinter 元件：把呼叫順序記成文字
# ============================================================

LOG: list = []
_COUNTERS: dict = {}


def _fmt(v):
    if isinstance(v, (Rec, FakeVar)):
        return v._name
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k!r}: {_fmt(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (str, int, float, bool, type(None))):
        return repr(v)
    if callable(v):
        name = getattr(v, "__name__", type(v).__name__)
        if name == "<lambda>":
            return f"<lambda defaults={_fmt(v.__defaults__)}>"
        return name
    return repr(v)


def _next(kind):
    _COUNTERS[kind] = _COUNTERS.get(kind, 0) + 1
    return f"{kind}#{_COUNTERS[kind]}"


_WINFO = {
    "winfo_x": 100, "winfo_y": 200, "winfo_width": 280, "winfo_height": 400,
    "winfo_screenwidth": 1920, "winfo_screenheight": 1080, "winfo_id": 999,
}


class Rec:
    def __init__(self, name):
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        return Rec(f"{self._name}.{attr}")

    def __call__(self, *args, **kwargs):
        parts = [_fmt(a) for a in args] + [f"{k}={_fmt(v)}" for k, v in kwargs.items()]
        LOG.append(f"{self._name}({', '.join(parts)})")
        last = self._name.rsplit(".", 1)[-1]
        if last in _WINFO:
            return _WINFO[last]
        if last == "get":
            return "typed text"
        return Rec(f"{self._name}()")

    def __setattr__(self, key, value):
        LOG.append(f"{self._name}.{key} = {_fmt(value)}")


class FakeVar:
    def __init__(self, kind, value=None, **kw):
        self._name = _next(kind)
        self.value = value
        LOG.append(f"{self._name} <- {_fmt(value)}")

    def get(self):
        return self.value

    def set(self, v):
        LOG.append(f"{self._name}.set({_fmt(v)})")
        self.value = v


def _widget_factory(kind):
    def make(*args, **kwargs):
        parts = [_fmt(a) for a in args] + [f"{k}={_fmt(v)}" for k, v in kwargs.items()]
        rec = Rec(_next(kind))
        LOG.append(f"tk.{kind}({', '.join(parts)}) -> {rec._name}")
        return rec
    return make


def _var_factory(kind):
    def make(*args, **kwargs):
        return FakeVar(kind, kwargs.get("value"))
    return make


class FakeThread:
    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, *, daemon=None):
        self.target, self.args, self.kwargs, self.daemon = target, args, kwargs or {}, daemon
        LOG.append(f"Thread({_fmt(target)}, args={_fmt(args)}, daemon={daemon})")

    def start(self):
        LOG.append(f"Thread.start({_fmt(self.target)})")

    def join(self, timeout=None):
        LOG.append(f"Thread.join({_fmt(self.target)})")


def _patch_everywhere(stack, name, replacement, only_if=None):
    """把所有已載入的專案模組裡叫這個名字的屬性換掉（重構後同一個名字可能被好幾個模組各自 import）。"""
    for modname, mod in list(sys.modules.items()):
        if mod is None or not hasattr(mod, "__dict__"):
            continue
        f = getattr(mod, "__file__", "") or ""
        if str(SCRIPTS) not in f:
            continue
        if name in mod.__dict__:
            stack.enter_context(mock.patch.object(mod, name, replacement))


# ============================================================
# worker：在乾淨的子行程裡跑所有檢查
# ============================================================

def _simple_attrs(mod):
    out = {}
    for name in sorted(dir(mod)):
        if name.startswith("__"):
            continue
        v = getattr(mod, name)
        if isinstance(v, Path):
            try:
                out[name] = "Path:" + v.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                out[name] = "Path:" + str(v)
        elif isinstance(v, (str, int, float, bool, type(None))):
            out[name] = repr(v)
        elif isinstance(v, (list, tuple, dict, set, frozenset)):
            try:
                out[name] = repr(sorted(v, key=repr) if isinstance(v, (set, frozenset)) else v)
            except Exception:
                pass
    return out


def worker(mode):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    if mode == "plain":
        os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    if mode == "plain":
        # 不管專案根目錄有沒有使用者真正的 .env，plain 模式一律當作沒有，
        # 這樣在哪個資料夾跑結果都一樣（common.env 只要標成已載入就不會去讀檔）。
        sys.path.insert(0, str(SCRIPTS))
        import common.env as _env  # noqa: E402
        _env._loaded = True

    sys.path.insert(0, str(SCRIPTS / "desktop_pet"))
    import desktop_pet  # noqa: E402  不能有副作用（不開 GUI、不啟動 TTS）
    import chat
    # 角色人設只放本機、不進 repo；測試改讀通用的測試人設，快照裡才不會出現真正的人設內容。
    chat.PERSONA_PATH = ROOT / "tests" / "fixtures" / "persona_test.md"
    import stt
    import screen_watch
    import expressions
    import web_lookup
    import tts_chunker
    import tts_infer
    import livliv_link
    import desktop_physics
    import numpy as np
    import tkinter as tk
    import threading

    R: dict = {}

    if mode == "dotenv":
        R["constants_dotenv"] = {
            "desktop_pet": _simple_attrs(desktop_pet),
            "chat": _simple_attrs(chat),
            "stt.STT_PROVIDER": stt.STT_PROVIDER,
        }
        return R

    # ---------- 公開介面（只能多、不能少）----------
    R["surface"] = {
        "chat": sorted(dir(chat)),
        "desktop_pet": sorted(dir(desktop_pet)),
        "CharacterWindow": sorted(dir(desktop_pet.CharacterWindow)),
        "expressions": sorted(dir(expressions)),
        "web_lookup": sorted(dir(web_lookup)),
    }
    R["constants"] = {"desktop_pet": _simple_attrs(desktop_pet), "chat": _simple_attrs(chat)}

    # ---------- 純函式 ----------
    pure = {}
    pure["chat._build_output_format_instructions"] = chat._build_output_format_instructions()
    pure["chat._build_speech_only_output_format_instructions"] = chat._build_speech_only_output_format_instructions()
    pure["chat._build_zh_translate_system_prompt"] = chat._build_zh_translate_system_prompt()
    pure["chat._reply_speech_field_instructions"] = chat._reply_speech_field_instructions()
    pure["chat._load_persona_text"] = chat._load_persona_text()
    langs = {}
    for lang in ["日文", "中文", "英文", "韓文", "西班牙文"]:
        with mock.patch.object(chat, "CHARACTER_SPEECH_LANGUAGE", lang):
            langs[lang] = [
                chat._build_output_format_instructions(),
                chat._build_speech_only_output_format_instructions(),
                chat._build_zh_translate_system_prompt(),
            ]
    pure["chat.by_language"] = langs
    pure["chat._fix_tw_terms"] = [
        chat._fix_tw_terms(s) for s in [
            "这个软件的网络视频质量很好", "軟件 網絡 視頻 信息 硬盘 U盘 u盘 打印 鼠标 服务器 服務器 程序 短信",
            "沒有需要取代的字", "", "硬碟盤 硬盘硬盘",
        ]
    ]
    pure["expressions.instructions"] = expressions.instructions()
    pure["expressions.tone_for"] = [
        expressions.tone_for(plan, d) for plan, d in [
            ([], "neutral"), ([{"at": 0, "expr": "開心"}], "serious"), ([{"at": 0, "expr": "生氣"}], "happy"),
            ([{"at": 0, "expr": "平常"}], "intense"), ([{"at": 0, "expr": "驚訝"}, {"at": 0.5, "expr": "難過"}], "x"),
        ]
    ]
    samples = ["〔害羞〕こんにちは。【開心】元気？", "沒有標記的句子", "〔不存在的表情〕あ〔生氣〕い", "前〔難過〕中〔得意〕後〔睏〕", ""]
    pure["expressions.split_tags"] = [expressions.split_tags(s) for s in samples]
    pure["expressions.split_chunks"] = [
        expressions.split_chunks(["〔害羞〕こんにちは。", "元気？〔開心〕", "", "〔生氣〕"]),
        expressions.split_chunks([]),
    ]
    ws = ["我不太清楚這個", "知っている", "わからない", "幫我查一下這個遊戲", "去查資料", "你好", "それは詳しくない"]
    pure["web_lookup.sounds_unsure"] = [web_lookup.sounds_unsure(s) for s in ws]
    pure["web_lookup.asked_to_search"] = [web_lookup.asked_to_search(s) for s in ws]
    pure["web_lookup.query_prompt"] = web_lookup.query_prompt("這款遊戲是什麼？")
    reqs = ["〔查：原神〕", "あ〔查: Ave Mujica 〕い", "沒有請求", "〔查：〕", "〔查：甲〕〔查：乙〕"]
    pure["web_lookup.find_request"] = [web_lookup.find_request(s) for s in reqs]
    pure["web_lookup.strip_requests"] = [web_lookup.strip_requests(s) for s in reqs]
    pure["web_lookup.followup_prompt"] = [web_lookup.followup_prompt("原神", "某資料"), web_lookup.followup_prompt("原神", None)]
    titles = ["原神 - Google Chrome", "Genshin Impact", "[Steam] 某遊戲 - 1.2.3", "", "A | B - C"]
    pure["web_lookup.clean_title"] = [web_lookup.clean_title(s) for s in titles]
    long_text = "今日はいい天気ですね。散歩に行きましょう！でも、雨が降るかもしれません。3.14という数字、" * 4
    pure["tts_chunker.split_for_tts"] = [tts_chunker.split_for_tts(s) for s in ["", "短い。", long_text, "A。B。C。D。E。F。"]]
    looks = [
        ({"fresh": True, "title": "原神", "process": "genshin.exe"}, "info 資料"),
        ({"fresh": False, "title": "", "process": ""}, None),
        ({"fresh": True}, None),
        ({"fresh": False, "title": "T", "process": ""}, "x"),
    ]
    pure["desktop_pet._screen_watch_prompt"] = [desktop_pet._screen_watch_prompt("一個遊戲畫面", lk, info) for lk, info in looks]
    rng = np.random.RandomState(0)
    wave = (np.sin(np.linspace(0, 200, 16000)) * np.repeat(rng.rand(100), 160)).astype(np.float32)
    pure["desktop_pet._mouth_envelope"] = [
        desktop_pet._mouth_envelope(wave, 16000),
        desktop_pet._mouth_envelope(np.stack([wave, wave * 0.5], axis=1), 16000),
        desktop_pet._mouth_envelope(np.zeros(4000, dtype=np.float32), 16000),
        desktop_pet._mouth_envelope(wave, 16000, hop_ms=50),
        desktop_pet._mouth_envelope(np.zeros(0, dtype=np.float32), 16000),
    ]
    R["pure"] = pure

    # ---------- 偏好檔（路徑要維持在 scripts/desktop_pet/.pet_prefs.json）----------
    prefs_mod = sys.modules.get("pet_prefs") or desktop_pet
    real_path = prefs_mod._PET_PREFS_FILE
    R["prefs_path"] = real_path.resolve().relative_to(ROOT).as_posix()
    tmp = Path(tempfile.mkdtemp(prefix="pet_snap_"))
    prefs_tests = []
    with contextlib.ExitStack() as st:
        for modname, mod in list(sys.modules.items()):
            if mod is not None and "_PET_PREFS_FILE" in getattr(mod, "__dict__", {}):
                st.enter_context(mock.patch.object(mod, "_PET_PREFS_FILE", tmp / "prefs.json"))
        prefs_tests.append(repr(prefs_mod._load_prefs()))
        prefs_mod._save_prefs(a=1, b="中")
        prefs_mod._save_prefs(a=2)
        prefs_tests.append(repr(prefs_mod._load_prefs()))
        prefs_tests.append((tmp / "prefs.json").read_text(encoding="utf-8"))
        (tmp / "prefs.json").write_text("{壞掉", encoding="utf-8")
        prefs_tests.append(repr(prefs_mod._load_prefs()))
    R["prefs"] = prefs_tests

    # ---------- LLM 供應商／語言執行期切換 ----------
    http_log = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        url = req.full_url
        http_log.append({"url": url, "payload": payload, "auth": req.get_header("Authorization")})
        is_json = payload.get("format") == "json" or "response_format" in payload
        content = (
            json.dumps({"reply_speech": "こんにちは", "tone": "happy"}, ensure_ascii=False) if is_json
            else '"你好，这个软件"'
        )

        class Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                if "openai" in url:
                    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()
                return json.dumps({"message": {"content": content}}).encode()
        return Resp()

    toggles = []
    saved = (chat.LLM_PROVIDER, chat.CHARACTER_SPEECH_LANGUAGE, chat.MODEL)
    with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("time.time", lambda: 1_700_000_000.0):
        for provider in ["local", "openai", "local"]:
            for lang in ["日文", "英文"]:
                chat.LLM_PROVIDER = provider
                chat.CHARACTER_SPEECH_LANGUAGE = lang
                http_log.clear()
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ja = chat.chat_ja("你好")
                    zh = chat.translate_zh("こんにちは")
                    full = chat.chat("你好")
                toggles.append({
                    "provider": provider, "lang": lang, "stdout": buf.getvalue(),
                    "results": [ja.model_dump(), zh, full.model_dump()],
                    "http": http_log[:],
                })
        # MODEL 也要即時反映（desktop_pet 會讀它）
        chat.LLM_PROVIDER = "local"
        chat.MODEL = "other-model"
        http_log.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            chat.chat_ja("hi")
        toggles.append({"model_switch": http_log[:]})
        chat.MODEL, chat.LLM_PROVIDER, chat.CHARACTER_SPEECH_LANGUAGE = saved[2], saved[0], saved[1]
    R["llm_toggles"] = toggles

    # ---------- stt / screen_watch 切換（桌寵選單改的那兩個變數）----------
    R["stt_screen_vars"] = {"stt.STT_PROVIDER": stt.STT_PROVIDER, "screen_watch.game_mode": screen_watch.game_mode}

    # ---------- CharacterWindow：建構流程與事件處理 ----------
    ev = types.SimpleNamespace(x_root=10, y_root=20, x=1, y=2, delta=120, keysym="a")
    ev_down = types.SimpleNamespace(x_root=10, y_root=20, x=1, y=2, delta=-120)
    ev_far = types.SimpleNamespace(x_root=50, y_root=60, x=1, y=2, delta=120)
    saves = []

    def fake_save_prefs(**kw):
        saves.append(kw)
        LOG.append(f"_save_prefs({_fmt(kw)})")

    fixed_prefs = {"llm_provider": "openai", "stt_provider": "openai", "character_speech_language": "韓文",
                   "game_mode": True, "screen_watch_enabled": True, "avatar_width": 250, "chunked_tts": False}

    def run_ctor(stage_enabled, prefs):
        LOG.clear()
        _COUNTERS.clear()
        with contextlib.ExitStack() as st:
            for name in ["Label", "Menu", "Toplevel", "Frame", "Text", "Entry", "Button", "Checkbutton"]:
                st.enter_context(mock.patch.object(tk, name, _widget_factory(name)))
            st.enter_context(mock.patch.object(tk, "StringVar", _var_factory("StringVar")))
            st.enter_context(mock.patch.object(tk, "BooleanVar", _var_factory("BooleanVar")))
            st.enter_context(mock.patch.object(threading, "Thread", FakeThread))
            import pynput.keyboard as pk
            st.enter_context(mock.patch.object(pk, "Listener", _widget_factory("Listener")))
            _patch_everywhere(st, "load_avatar_frames",
                              lambda *a, **k: (Rec("frame_closed"), Rec("frame_open"), 400))
            _patch_everywhere(st, "exclude_self_from_screen_capture", lambda hwnd: f"hwnd:{hwnd}")
            _patch_everywhere(st, "ConversationHistory", lambda *a, **k: Rec("ConversationHistory"))
            _patch_everywhere(st, "PushToTalkRecorder", lambda **k: Rec("PushToTalkRecorder"))
            _patch_everywhere(st, "_load_prefs", lambda: dict(prefs))
            _patch_everywhere(st, "_save_prefs", fake_save_prefs)
            st.enter_context(mock.patch("random.uniform", lambda a, b: a))
            st.enter_context(mock.patch("random.choice", lambda seq: seq[0]))
            st.enter_context(mock.patch("time.monotonic", lambda: 1000.0))
            st.enter_context(mock.patch.object(livliv_link, "ENABLED", stage_enabled))
            st.enter_context(mock.patch.object(livliv_link, "listen", lambda cb: LOG.append("livliv_link.listen")))
            st.enter_context(mock.patch.object(livliv_link, "_sock", None))
            root = Rec("root")
            saved_state = (chat.LLM_PROVIDER, chat.CHARACTER_SPEECH_LANGUAGE, stt.STT_PROVIDER, screen_watch.game_mode)
            try:
                win = desktop_pet.CharacterWindow(root)
            finally:
                ctor_state = (chat.LLM_PROVIDER, chat.CHARACTER_SPEECH_LANGUAGE, stt.STT_PROVIDER, screen_watch.game_mode)
                chat.LLM_PROVIDER, chat.CHARACTER_SPEECH_LANGUAGE, stt.STT_PROVIDER, screen_watch.game_mode = saved_state
        return win, list(LOG), ctor_state

    win0, log0, _ = run_ctor(False, {})
    R["ctor_default"] = "\n".join(log0)
    win1, log1, state1 = run_ctor(False, fixed_prefs)
    R["ctor_prefs"] = "\n".join(log1)
    R["ctor_prefs_state"] = list(map(repr, state1))
    win2, log2, _ = run_ctor(True, {})
    R["ctor_stage"] = "\n".join(log2)
    R["instance_attrs"] = sorted(vars(win0))
    R["instance_attrs_prefs"] = sorted(vars(win1))

    # 事件處理：用 win1（吃過偏好）操作，每個測項各自記錄 LOG。
    win = win1
    events = {}

    def case(name, fn):
        LOG.clear()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ret = fn()
            LOG.append(f"return {_fmt(ret) if not isinstance(ret, Rec) else 'Rec'}")
        except Exception as e:
            LOG.append(f"EXC {type(e).__name__}: {e}")
        events[name] = {"log": list(LOG), "stdout": buf.getvalue()}

    with contextlib.ExitStack() as st:
        for name in ["Label", "Menu", "Toplevel", "Frame", "Text", "Entry", "Button", "Checkbutton"]:
            st.enter_context(mock.patch.object(tk, name, _widget_factory(name)))
        st.enter_context(mock.patch.object(tk, "StringVar", _var_factory("StringVar")))
        st.enter_context(mock.patch.object(tk, "BooleanVar", _var_factory("BooleanVar")))
        st.enter_context(mock.patch.object(threading, "Thread", FakeThread))
        _patch_everywhere(st, "load_avatar_frames", lambda *a, **k: (Rec("frame_closed2"), Rec("frame_open2"), 300))
        _patch_everywhere(st, "_save_prefs", fake_save_prefs)
        _patch_everywhere(st, "look_at_screen", lambda: "一個遊戲畫面")
        st.enter_context(mock.patch("random.uniform", lambda a, b: a))
        st.enter_context(mock.patch("random.choice", lambda seq: seq[0]))
        st.enter_context(mock.patch.object(web_lookup, "search", lambda q: f"查到:{q}"))
        st.enter_context(mock.patch.object(livliv_link, "send_menu", lambda m: LOG.append(f"send_menu({_fmt(m)})")))
        st.enter_context(mock.patch.object(livliv_link, "describe_menu", lambda m: "described"))
        st.enter_context(mock.patch.object(livliv_link, "invoke_menu", lambda m, p: LOG.append(f"invoke_menu({p})")))
        st.enter_context(mock.patch.object(livliv_link, "send_quit", lambda: LOG.append("send_quit")))
        st.enter_context(mock.patch.object(screen_watch, "unload_models", lambda m: LOG.append(f"unload_models({m})")))
        saved_state = (chat.LLM_PROVIDER, chat.CHARACTER_SPEECH_LANGUAGE, stt.STT_PROVIDER, screen_watch.game_mode)

        case("press_motion_release_click", lambda: (win._on_avatar_press(ev), win._on_avatar_motion(ev), win._on_avatar_release(ev)))
        case("drag_release", lambda: (win._on_avatar_press(ev), win._on_avatar_motion(ev_far), win._on_avatar_release(ev_far)))
        case("popup_hide", win._hide_input_popup)
        case("popup_show_submit", lambda: (win._show_input_popup(), win._on_popup_submit()))
        win._busy = False
        case("right_click", lambda: win._on_avatar_right_click(ev))
        case("scroll_up_down_apply", lambda: (win._on_avatar_scroll(ev), win._on_avatar_scroll(ev_down),
                                              win._on_avatar_scroll(ev_down), win._apply_avatar_resize(), win._apply_avatar_resize()))
        with mock.patch.object(desktop_physics, "find_landing_y", lambda *a: 5000):
            case("physics_falling", lambda: (win._physics_tick(), win._physics_tick(), win._fall_velocity))
        with mock.patch.object(desktop_physics, "find_landing_y", lambda *a: 600):
            case("physics_landed_wander", lambda: (win._physics_tick(), win._start_wander(),
                                                   win._advance_wander(100, 280, 0.033), win._advance_wander(100, 280, 0.033)))
        with mock.patch("time.monotonic", lambda: 99999.0):
            case("wander_due", lambda: (win._advance_wander(100, 280, 0.033), win._walk_seconds_left, win._facing))
        case("current_frame", lambda: [_fmt(win._current_frame(a)) for a in (True, False)])
        win._facing = "left"
        case("current_frame_left", lambda: [_fmt(win._current_frame(a)) for a in (True, False)])
        win._facing = "right"
        case("history_toggle_twice", lambda: (win._toggle_history_window(), win._append_history("你", "哈囉"),
                                              win._append_history("祥子", "你好"), win._toggle_history_window()))
        case("history_text", lambda: win._history_text)
        case("dialogue", lambda: (win._set_dialogue("文字", False), win._set_dialogue("狀態", True), win._clear_status(),
                                  win._update_dialogue_visibility()))
        win._busy = False
        case("submit", lambda: (win._submit("哈囉"), win._submit("忙碌中被擋"), win._busy))
        win._busy = False
        case("trigger_proactive", lambda: (win._trigger_proactive("提示"), win._trigger_proactive("忙碌")))
        win._busy = False
        case("key_press_release", lambda: (win._on_key_press(desktop_pet.PUSH_TO_TALK_KEY), win._on_key_press("other"),
                                           win._on_key_release(desktop_pet.PUSH_TO_TALK_KEY), win._on_key_release("other")))
        win._busy = True
        case("key_press_busy", lambda: win._on_key_press(desktop_pet.PUSH_TO_TALK_KEY))
        win._busy = False
        case("ptt_state", lambda: (win._on_ptt_state_change(True), win._on_ptt_state_change(False), win._on_ptt_transcribed("轉出來")))
        case("game_mode_toggle", lambda: (win._game_mode_var.set(False), win._on_game_mode_toggle(), screen_watch.game_mode))
        case("screen_watch_toggle", lambda: (win._screen_watch_var.set(False), win._on_screen_watch_toggle(), win._screen_watch_enabled))
        case("llm_provider_change", lambda: (win._llm_provider_var.set("local"), win._on_llm_provider_change(), chat.LLM_PROVIDER))
        case("stt_provider_change", lambda: (win._stt_provider_var.set("local"), win._on_stt_provider_change(), stt.STT_PROVIDER))
        case("speech_language_change", lambda: (win._speech_language_var.set("中文"), win._on_speech_language_preset_change(),
                                                chat.CHARACTER_SPEECH_LANGUAGE))
        case("prompt_speech_language", win._prompt_speech_language)
        case("prompt_screen_watch_choice", win._prompt_screen_watch_choice)
        case("chunked_toggle", lambda: (win._chunked_tts_var.set(True), win._on_chunked_tts_toggle(), win._chunked_tts))
        case("clear_memory", win._on_clear_memory)
        case("game_info_for", lambda: (win._game_info_for({"process": "chrome.exe"}), win._game_info_for({}),
                                       win._game_info_for({"process": "Game.EXE", "title": "原神 - Chrome"}),
                                       win._game_info_for({"process": "Game.EXE", "title": "原神 - Chrome"}), dict(win._game_info)))
        win._busy = True
        case("claude_event_busy", lambda: win._on_claude_event({"event": "waiting"}))
        case("claude_event_busy_giveup", lambda: win._on_claude_event({"event": "waiting"}, tries=desktop_pet.CLAUDE_EVENT_MAX_TRIES))
        win._busy = False
        case("claude_event_done", lambda: win._on_claude_event({"event": "done", "seconds": 400}))
        win._busy = False
        case("claude_event_waiting", lambda: win._on_claude_event({"event": "waiting"}))
        win._busy = False
        case("stage_events", lambda: (win._on_stage_event({"event": "text", "text": " 打字 "}),
                                      win._on_stage_event({"event": "menu", "x": 5, "y": 6}),
                                      win._on_stage_event({"event": "menuitem", "path": [0]}),
                                      win._on_stage_event({"event": "other"})))
        win._busy = False

        def poll():
            for item in [("reply", "譯文"), ("mouth", True), ("stage", {"event": "menuitem", "path": [1]}), ("error", "壞了"), ("done", None)]:
                win._task_queue.put(item)
            win._busy = True
            win._poll_queue()
            return win._busy
        case("poll_queue", poll)
        case("queue_mouth_state", lambda: (win._queue_mouth_state(True), win._task_queue.get_nowait()))

        # 螢幕讀取迴圈：假時鐘，每次 sleep 前進該秒數，跑幾輪後丟例外跳出。
        class Stop(Exception):
            pass

        def run_screen_loop(enabled, busy, popup):
            clock = [0.0]
            sleeps = [0]

            def fake_sleep(s):
                sleeps[0] += 1
                clock[0] += s
                if sleeps[0] > 40:
                    raise Stop()
            win._screen_watch_enabled = enabled
            win._busy = busy
            win._input_popup = popup
            screen_watch.last_look = {"fresh": True, "title": "標題", "process": "game.exe"}
            win._game_info = {}
            with mock.patch("time.sleep", fake_sleep), mock.patch("time.monotonic", lambda: clock[0]), \
                    mock.patch.object(screen_watch, "last_look", {"fresh": True, "title": "標題", "process": "game.exe"}):
                try:
                    win._screen_watch_loop()
                except Stop:
                    pass
            win._input_popup = None
            win._busy = False
            return sleeps[0]
        case("screen_loop_enabled", lambda: run_screen_loop(True, False, None))
        case("screen_loop_disabled", lambda: run_screen_loop(False, False, None))
        case("screen_loop_busy", lambda: run_screen_loop(True, True, None))
        case("screen_loop_popup", lambda: run_screen_loop(True, False, Rec("popup")))

        # Claude 事件迴圈：假 socket
        class FakeSock:
            def __init__(self, *a):
                self.n = 0

            def bind(self, addr):
                LOG.append(f"sock.bind({addr})")
                if FakeSock.fail:
                    raise OSError("埠被佔用")

            def recvfrom(self, n):
                self.n += 1
                packets = [b'{"event": "waiting"}', b"\xff\xfe", b"not json", b'{"event": "nope"}', b'{"event": "done", "seconds": 120}']
                if self.n > len(packets):
                    raise Stop()
                return packets[self.n - 1], ("127.0.0.1", 1)
        FakeSock.fail = False
        import socket as socket_mod
        with mock.patch.object(socket_mod, "socket", FakeSock):
            case("claude_event_loop", win._claude_event_loop)
            FakeSock.fail = True
            case("claude_event_loop_bind_fail", win._claude_event_loop)
        case("on_close", win._on_close)
        chat.LLM_PROVIDER, chat.CHARACTER_SPEECH_LANGUAGE, stt.STT_PROVIDER, screen_watch.game_mode = saved_state
    R["events"] = events
    R["saves_total"] = saves

    # ---------- 對話主流程（真執行緒，假 HTTP／TTS／音訊）----------
    import soundfile as sf
    import sounddevice as sd
    wav = io.BytesIO()
    sf.write(wav, (np.sin(np.linspace(0, 400, 4800)) * 0.5).astype(np.float32), 16000, format="WAV", subtype="PCM_16")
    wav_bytes = wav.getvalue()
    flow = {}
    out_tmp = Path(tempfile.mkdtemp(prefix="pet_snap_out_"))
    synth_calls = []
    sd_events = []

    class FakeStream:
        def __init__(self, device=None, samplerate=None, channels=None):
            self.info = (device, samplerate, channels)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, data):
            sd_events.append(f"write{self.info}{data.shape}{data.dtype}")

    fake_devices = [
        {"name": "Speakers", "max_output_channels": 2, "hostapi": 0},
        {"name": "CABLE Input (VB-Audio)", "max_output_channels": 2, "hostapi": 0},
        {"name": "CABLE Input (VB-Audio)", "max_output_channels": 2, "hostapi": 1},
        {"name": "Mic", "max_output_channels": 0, "hostapi": 0},
    ]
    fake_default = types.SimpleNamespace(device=(0, 0), hostapi=0)
    pet_audio_mod = sys.modules.get("pet_audio") or desktop_pet

    def run_flow(name, chunked, reply_json, allow_lookup=True, text="你好"):
        synth_calls.clear()
        sd_events.clear()
        LOG.clear()

        class Conv:
            def to_messages(self):
                return []

            def add_turn(self, u, a, t):
                flow_conv.append((u, a))
        flow_conv = []
        w = object.__new__(desktop_pet.CharacterWindow)
        w.root = Rec("root")
        w._task_queue = queue.Queue()
        w._conversation = Conv()
        w._chunked_tts = chunked
        w.avatar_label = Rec("avatar_label")
        w.frame_open = Rec("fo")
        w.frame_closed = Rec("fc")
        w.frame_open_l = Rec("fol")
        w.frame_closed_l = Rec("fcl")
        w._facing = "right"
        buf = io.StringIO()
        http_log.clear()

        def fake_urlopen2(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            http_log.append({"url": req.full_url, "n_msgs": len(payload["messages"]),
                             "last": re.sub(r"\[\d{4}-\d\d-\d\d \d\d:\d\d\]", "[TS]", payload["messages"][-1]["content"][-120:])})
            is_json = payload.get("format") == "json" or "response_format" in payload
            content = reply_json(len(http_log)) if is_json else "翻譯結果"

            class Resp:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    return False

                def read(s):
                    return json.dumps({"message": {"content": content}}).encode()
            return Resp()

        with mock.patch("urllib.request.urlopen", fake_urlopen2), \
                mock.patch.object(tts_infer, "synthesize", lambda text, tone="neutral", **k: (synth_calls.append((text, tone)), wav_bytes)[1]), \
                mock.patch.object(tts_infer, "OUTPUT_DIR", out_tmp), \
                mock.patch.object(sd, "query_devices", lambda: fake_devices), \
                mock.patch.object(sd, "default", fake_default), \
                mock.patch.object(sd, "OutputStream", FakeStream), \
                mock.patch.object(web_lookup, "search", lambda q: f"查到:{q}"), \
                contextlib.redirect_stdout(buf):
            chat.LLM_PROVIDER = "local"
            w._handle_message(text, allow_lookup)
        items = []
        while not w._task_queue.empty():
            items.append(w._task_queue.get_nowait())
        kinds = [k for k, _ in items]
        flow[name] = {
            "synth": synth_calls[:],
            "sd": sorted(sd_events),
            "mouth_seq": [p for k, p in items if k == "mouth"],
            "others": sorted(repr((k, p)) for k, p in items if k != "mouth"),
            "last_kind": kinds[-1] if kinds else None,
            "http": http_log[:],
            "conv": flow_conv,
            "stdout": buf.getvalue(),
            "log": list(LOG),
        }

    plain = lambda n: json.dumps({"reply_speech": "〔開心〕こんにちは。元気ですか？今日もいい天気ですね。", "tone": "neutral"}, ensure_ascii=False)
    run_flow("plain_chunked", True, plain)
    run_flow("plain_unchunked", False, plain)
    lookup_reply = lambda n: json.dumps(
        {"reply_speech": "〔查：原神〕" if n == 1 else "調べました。〔生氣〕すごい。", "tone": "serious"}, ensure_ascii=False)
    run_flow("lookup", True, lookup_reply)
    unsure = lambda n: json.dumps(
        {"reply_speech": ("よく知りません。" if n == 1 else ("〔查：新遊戲〕" if n == 2 else "分かりました。")), "tone": "hesitant"},
        ensure_ascii=False)
    run_flow("unsure_lookup", True, unsure)
    run_flow("no_lookup_allowed", True, unsure, allow_lookup=False)
    run_flow("asked_to_search", True, plain, text="幫我查一下")
    run_flow("bad_json", True, lambda n: "not json")

    def tts_error_flow():
        with mock.patch.object(tts_infer, "synthesize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("合成壞了"))):
            q = queue.Queue()
            pet_audio_mod_local = desktop_pet.CharacterWindow._produce_audio
            pet_audio_mod_local(["一", "二"], ["neutral", "happy"], q)
            return [repr(q.get_nowait()[0]) for _ in range(q.qsize())]
    flow["produce_audio_error"] = tts_error_flow()
    R["flow"] = flow

    # CharacterWindow._produce_audio 要還在（staticmethod），而且也能從類別呼叫
    R["produce_audio_is_static"] = isinstance(desktop_pet.CharacterWindow.__dict__.get("_produce_audio"), staticmethod)
    return R


# ============================================================
# 主程式：叫 worker、錄／比對
# ============================================================

def _run_worker(mode):
    env = os.environ.copy()
    for k in _ENV_KEYS:
        env.pop(k, None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    out = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    out.close()
    code = subprocess.run(
        [sys.executable, "-I", "-c",
         f"import sys; sys.path.insert(0, {str(ROOT / 'tests')!r}); import snapshot, json; "
         f"r = snapshot.worker({mode!r}); open({out.name!r}, 'w', encoding='utf-8').write(json.dumps(r, ensure_ascii=False, sort_keys=True, indent=1))"],
        env=env, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    if code.returncode != 0:
        print(code.stdout)
        print(code.stderr, file=sys.stderr)
        raise SystemExit(f"worker({mode}) 失敗")
    data = json.loads(Path(out.name).read_text(encoding="utf-8"))
    os.unlink(out.name)
    return data


def collect():
    res = _run_worker("plain")
    env_file = ROOT / ".env"
    if env_file.exists():
        res["constants_dotenv"] = {"skipped": "已有真的 .env，不覆蓋"}
    else:
        env_file.write_text(_DOTENV_TEXT, encoding="utf-8")
        try:
            res.update(_run_worker("dotenv"))
        finally:
            env_file.unlink()
    return res


SURFACE_KEYS = {"surface"}


def compare(golden, now):
    problems = []
    for key in sorted(set(golden) | set(now)):
        if key not in golden:
            continue  # 新增的檢查項目不算差異
        if key not in now:
            problems.append(f"缺少區段：{key}")
            continue
        if key == "surface":
            for mod, names in golden[key].items():
                missing = sorted(set(names) - set(now[key].get(mod, [])))
                if missing:
                    problems.append(f"surface.{mod} 少了名稱：{missing}")
            continue
        if key == "instance_attrs" or key == "instance_attrs_prefs":
            if golden[key] != now[key]:
                problems.append(f"{key} 不同：少 {sorted(set(golden[key]) - set(now[key]))}，多 {sorted(set(now[key]) - set(golden[key]))}")
            continue
        if golden[key] != now[key]:
            problems.append(f"{key} 不同" + _detail(golden[key], now[key], key))
    return problems


def _detail(g, n, path):
    if isinstance(g, dict) and isinstance(n, dict):
        out = []
        for k in sorted(set(g) | set(n)):
            if g.get(k) != n.get(k):
                out.append(_detail(g.get(k), n.get(k), f"{path}.{k}"))
        return "".join(out[:8])
    if isinstance(g, str) and isinstance(n, str):
        gl, nl = g.splitlines(), n.splitlines()
        for i, (a, b) in enumerate(zip(gl, nl)):
            if a != b:
                return f"\n  [{path}] 第 {i + 1} 行\n    原: {a[:200]}\n    新: {b[:200]}"
        return f"\n  [{path}] 行數 {len(gl)} -> {len(nl)}"
    return f"\n  [{path}]\n    原: {str(g)[:300]}\n    新: {str(n)[:300]}"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2 or sys.argv[1] not in ("record", "check"):
        raise SystemExit(__doc__)
    now = collect()
    if sys.argv[1] == "record":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(now, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
        print(f"已錄下 golden：{GOLDEN}（{len(now)} 個區段）")
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    now = json.loads(json.dumps(now, ensure_ascii=False, sort_keys=True))
    skipped = []
    if "skipped" in (now.get("constants_dotenv") or {}):
        # 專案根目錄有使用者真正的 .env 時不會覆蓋它，這一段沒辦法測，略過不比。
        golden.pop("constants_dotenv", None)
        now.pop("constants_dotenv", None)
        skipped.append("constants_dotenv（已有真的 .env，無法用測試用 .env 驗證）")
    for s in skipped:
        print("略過：", s)
    problems = compare(golden, now)
    if problems:
        print("FAIL：行為和重構前不一樣")
        for p in problems:
            print(" -", p)
        raise SystemExit(1)
    print(f"PASS：{len(golden)} 個區段全部和 golden 一致")


if __name__ == "__main__":
    main()
