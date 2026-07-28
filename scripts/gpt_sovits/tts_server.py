"""確保本地 GPT-SoVITS 推理服務（api_v2.py）有在跑，沒有就自動啟動——跟角色無關的
通用邏輯，desktop_pet.py / discord_bot/bot.py / chat_cli.py 共用。
"""
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from tts_infer import API_URL, PROJECT_ROOT  # noqa: E402

GPT_SOVITS_DIR = PROJECT_ROOT / "GPT-SoVITS"
PYTHON_EXE = sys.executable  # 這支腳本本來就是用目標 conda 環境的直譯器啟動的，直接沿用同一個
STATUS_URL = API_URL.rsplit("/", 1)[0] + "/"

_parsed_api_url = urlparse(API_URL)
_API_HOST = _parsed_api_url.hostname or "127.0.0.1"
_API_PORT = str(_parsed_api_url.port or 9880)


def is_server_ready() -> bool:
    try:
        urllib.request.urlopen(STATUS_URL, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # 有回應（就算是 404/405）就代表服務活著
    except Exception:
        return False


def start_server():
    print("推理服務尚未啟動，正在自動啟動（第一次要載入模型，約需 30 秒～1 分鐘）...")
    # 開新主控台視窗，但一開始就縮到工作列，不會跳出來擋畫面；
    # 想看 log 的時候點工作列上的視窗叫出來看就好，不影響背景執行的進程本身。
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 2  # SW_SHOWMINIMIZED
    subprocess.Popen(
        [PYTHON_EXE, "api_v2.py", "-a", _API_HOST, "-p", _API_PORT],
        cwd=str(GPT_SOVITS_DIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startupinfo,
    )
    while not is_server_ready():
        time.sleep(2)
    print("服務啟動完成！\n")
