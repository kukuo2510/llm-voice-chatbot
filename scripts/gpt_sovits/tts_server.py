"""確保本地 GPT-SoVITS 推理服務（api_v2.py）有在跑，沒有就自動啟動。

這支模組是跟「角色」完全無關的通用邏輯，desktop_pet.py 和 chat_cli.py
都會共用它——不管換成哪個角色，啟動/檢查推理服務的方式都一樣。
"""

import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from tts_infer import API_URL, PROJECT_ROOT  # noqa: E402

GPT_SOVITS_DIR = PROJECT_ROOT / "GPT-SoVITS"

# 這支腳本本來就是用目標 conda 環境的直譯器啟動的，
# 啟動子行程時直接沿用同一個直譯器路徑，確保套件版本一致。
PYTHON_EXE = sys.executable

# 從完整的推理 API 網址，反推出「服務根目錄」的健康檢查網址。
STATUS_URL = API_URL.rsplit("/", 1)[0] + "/"

# 從 API_URL 解析出主機／連接埠，啟動子行程時要用同一組設定，
# 確保「我們去檢查的網址」跟「我們啟動服務時指定的網址」是同一個。
_parsed_api_url = urlparse(API_URL)
_API_HOST = _parsed_api_url.hostname or "127.0.0.1"
_API_PORT = str(_parsed_api_url.port or 9880)


def is_server_ready() -> bool:
    """檢查本地推理服務是否已經啟動並且能回應請求。"""
    try:
        urllib.request.urlopen(STATUS_URL, timeout=2)
        return True
    except urllib.error.HTTPError:
        # 只要「有回應」就代表服務活著，就算回應內容是 404/405
        # 這種表示「路由對不上」的錯誤，也不代表服務沒啟動。
        return True
    except Exception:
        return False


def start_server():
    """啟動 GPT-SoVITS 的推理服務子行程，並等待它就緒。"""
    print("推理服務尚未啟動，正在自動啟動（第一次要載入模型，約需 30 秒～1 分鐘）...")

    # 開一個新的主控台視窗來跑服務，但一開始就把它縮到工作列，
    # 不會跳出來擋住桌面畫面；想看 log 的時候，點工作列上的視窗
    # 叫出來看就好，不會影響背景執行的服務進程本身。
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 2  # SW_SHOWMINIMIZED

    subprocess.Popen(
        [PYTHON_EXE, "api_v2.py", "-a", _API_HOST, "-p", _API_PORT],
        cwd=str(GPT_SOVITS_DIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startupinfo,
    )

    # 服務第一次啟動要載入模型，需要一段時間，這裡用輪詢的方式
    # 等到服務真的能回應請求為止，才把控制權交還給呼叫端。
    while not is_server_ready():
        time.sleep(2)

    print("服務啟動完成！\n")
