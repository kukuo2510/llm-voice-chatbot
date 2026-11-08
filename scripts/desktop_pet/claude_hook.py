"""Claude Code 的 hook 呼叫這支，把「Claude 在等確認」「Claude 做完了」通知桌寵。

設定在 ~/.claude/settings.json 的 hooks：
    UserPromptSubmit → claude_hook.py start   記下這輪開始的時間
    Stop             → claude_hook.py stop    做完了；這輪超過 DONE_MIN_SECONDS 才通知，免得每句都講
    Notification     → claude_hook.py notify  只轉送「等權限確認」那種通知

只傳事件種類和花了幾秒，不傳主人交辦的內容（不會進角色的對話記憶）。
用 UDP 丟了就走：桌寵沒開也不會卡住 Claude Code，這支永遠安靜地結束、不輸出任何東西。
只用標準函式庫，任何 Python 都能跑。
"""
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

PORT = int(os.environ.get("CLAUDE_EVENT_PORT", "9892"))  # 要跟 desktop_pet.py 一致
DONE_MIN_SECONDS = float(os.environ.get("CLAUDE_HOOK_DONE_MIN_SECONDS", "60"))
STATE_DIR = Path(tempfile.gettempdir()) / "claude_pet_hook"
LOG = STATE_DIR / "notify_types.log"  # 只記通知種類，用來確認有沒有抓對（不記內容）


def send(event: dict) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(json.dumps(event).encode("utf-8"), ("127.0.0.1", PORT))


def is_permission_prompt(payload: dict) -> bool:
    kind = str(payload.get("notification_type", "")).lower()
    message = str(payload.get("message", "")).lower()
    return "permission" in kind or "permission" in message


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    STATE_DIR.mkdir(exist_ok=True)
    start_file = STATE_DIR / f"{payload.get('session_id', 'default')}.start"

    if mode == "start":
        start_file.write_text(str(time.time()), encoding="utf-8")
    elif mode == "stop":
        if not start_file.exists():
            return
        seconds = time.time() - float(start_file.read_text(encoding="utf-8"))
        start_file.unlink(missing_ok=True)
        if seconds >= DONE_MIN_SECONDS:
            send({"event": "done", "seconds": round(seconds)})
    elif mode == "notify":
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} type={payload.get('notification_type')!r}\n")
        if is_permission_prompt(payload):
            send({"event": "waiting"})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # hook 出錯不能影響 Claude Code
