"""桌寵和 LivLiv 舞台視窗（../../../livliv-stage）之間的連線。

預設關閉：只有環境變數 LIVLIV_STAGE=1 才會啟用，由 scripts/launch_hidden.bat（start_desktop_pet_hidden.vbs 啟動）設定。

桌寵 -> 舞台（UDP，LIVLIV_UDP_PORT，預設 9890）
    語音（舞台只拿來算嘴型，不發聲，聲音還是 play_for_avatar 在播）、台詞、關閉指令。
    語音太大塞不進 UDP，所以寫成暫存檔、只傳路徑。
舞台 -> 桌寵（UDP，上面的埠號 +1）
    使用者打的字、右鍵。

UDP 是丟了就走，舞台沒開也不影響桌寵，所以送出時的錯誤一律吞掉，
不能讓畫面同步的問題中斷語音播放。
"""
import json
import os
import socket
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable

import soundfile as sf

ENABLED = os.environ.get("LIVLIV_STAGE") == "1"
PORT = int(os.environ.get("LIVLIV_UDP_PORT", "9890"))
ADDRESS = ("127.0.0.1", PORT)
CLIP_DIR = Path(tempfile.gettempdir()) / "livliv-stage-clips"  # 資料夾名稱要跟 main.cjs 一致
MAX_SAY_CHARS = 2000  # 遠低於 UDP 單一封包上限

_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if ENABLED else None


def _send(message: dict) -> None:
    if _sock is None:
        return
    try:
        _sock.sendto(json.dumps(message, ensure_ascii=False).encode("utf-8"), ADDRESS)
    except OSError:
        pass


def send_clip(data, samplerate: int) -> None:
    if _sock is None:
        return
    try:
        CLIP_DIR.mkdir(exist_ok=True)
        path = CLIP_DIR / f"{uuid.uuid4().hex}.wav"
        sf.write(path, data, samplerate, subtype="PCM_16")
        _send({"clip": str(path)})
    except Exception as e:
        print(f"[livliv_link] 送語音失敗（不影響播放）：{e}")


def send_say(text: str, is_status: bool) -> None:
    _send({"say": text[:MAX_SAY_CHARS], "status": is_status})


def send_expr_plan(plan: list) -> None:
    """下一段語音的表情排程：[{"at": 0～1, "expr": "害羞"}]，舞台在那段語音開始播時照位置換表情。
    要在 send_clip 之前送，空的也要送（舞台會在講完後回到平常）。"""
    _send({"exprPlan": plan})


def send_quit() -> None:
    _send({"quit": True})


def send_menu(items: list) -> None:
    _send({"menu": items})


def describe_menu(menu) -> list:
    """把 tk.Menu 轉成舞台能畫的資料。

    選單由舞台用原生方式畫：點舞台時桌寵不是前景程式，Windows 不一定讓 tk 的選單
    拿到焦點，點選單外面可能關不掉。實際動作還是用 invoke_menu 交回 tk 選單執行。
    """
    items = []
    last = menu.index("end")
    for index in range(0 if last is None else last + 1):
        kind = menu.type(index)
        if kind == "separator":
            items.append({"type": "separator"})
            continue
        if kind == "tearoff":
            continue
        item = {
            "index": index,
            "type": kind,
            "label": menu.entrycget(index, "label"),
            "enabled": menu.entrycget(index, "state") != "disabled",
        }
        if kind == "cascade":
            item["items"] = describe_menu(menu.nametowidget(menu.entrycget(index, "menu")))
        elif kind in ("checkbutton", "radiobutton"):
            on_value = menu.entrycget(index, "onvalue" if kind == "checkbutton" else "value")
            value = menu.getvar(str(menu.entrycget(index, "variable")))
            if isinstance(value, bool):  # BooleanVar 讀出來是 True／False，onvalue 是 "1"
                value = int(value)
            item["checked"] = str(value) == str(on_value)
        items.append(item)
    return items


def invoke_menu(menu, path: list) -> None:
    """path 是 describe_menu 給的 index，從最外層一路往下，最後一個是要執行的項目。"""
    for index in path[:-1]:
        menu = menu.nametowidget(menu.entrycget(index, "menu"))
    menu.invoke(path[-1])


def listen(on_event: Callable[[dict], None]) -> None:
    """在背景執行緒收舞台傳來的事件。on_event 會在背景執行緒被呼叫。"""
    if not ENABLED:
        return

    def _loop() -> None:
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            rx.bind(("127.0.0.1", PORT + 1))
        except OSError as e:
            print(f"[livliv_link] 收不到舞台事件（埠號 {PORT + 1} 被占用）：{e}")
            return
        while True:
            try:
                event = json.loads(rx.recv(65535).decode("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(event, dict):
                on_event(event)

    threading.Thread(target=_loop, daemon=True).start()
