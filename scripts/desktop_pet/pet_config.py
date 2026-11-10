"""桌寵的常數設定：配色、熱鍵、立繪、物理、螢幕讀取／Claude 通知的參數。

從 desktop_pet.py 原封不動搬出來的。AVATAR_DISPLAY_WIDTH、SCREEN_WATCH_INTERVAL_SEC、
CLAUDE_EVENT_PORT 在 import 當下就會讀環境變數，所以這裡自己先 load_dotenv()，
不依賴別的模組（原本是 import chat 時順便載入）有沒有先跑。
"""

import os
import sys
from pathlib import Path

from pynput import keyboard as pynput_keyboard

# 讓 pet_* 模組不管從哪裡被 import，都找得到 common/、llm/、gpt_sovits/ 底下的模組。
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gpt_sovits"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "llm"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.env import load_dotenv  # noqa: E402

load_dotenv()


# ============================================================
# 視覺樣式
# ============================================================
#
# Ave Mujica 氛圍配色（哥德紫＋金色點綴）——不是查證過的官方角色色票，
# 是照劇中面具／薔薇／歌德風美術方向抓的氣氛色，想換成別的顏色跟維護者說就好。

COLOR_BG = "#15101c"
COLOR_PANEL = "#241a30"
COLOR_ACCENT = "#7c4dbd"
COLOR_ACCENT_HOVER = "#5e3690"
COLOR_GOLD = "#c9a86a"
COLOR_TEXT = "#eee6f5"

# 狀態文字（錄音中／生成中）用比正式台詞暗一點的顏色，讓使用者一眼就能
# 分辨目前看到的是「角色真的在講話」還是「系統狀態提示」。
COLOR_STATUS_TEXT = "#b8a8c8"


# ============================================================
# 熱鍵 / 裝置設定
# ============================================================

# 全域熱鍵：按住講話、放開結束。
PUSH_TO_TALK_KEY = pynput_keyboard.Key.f9
PUSH_TO_TALK_KEY_LABEL = "F9"

CABLE_DEVICE_NAME_HINT = "CABLE Input"


# ============================================================
# 立繪相關設定
# ============================================================

# 立繪素材放在 characters/<角色>/avatar/，跟角色人設、語音設定同一套規則；
# 要換角色，把這裡的 "sakiko" 換成自己的角色資料夾名稱。
ASSETS_DIR = Path(__file__).resolve().parents[2] / "characters" / "sakiko" / "avatar"

# 立繪顯示寬度（像素）的預設值，高度依素材比例算出來。可以用滑鼠滾輪
# 即時縮放（見 _on_avatar_scroll），縮放後的大小會被記住，下次啟動時
# 優先於這個預設值使用。
AVATAR_DISPLAY_WIDTH = int(os.environ.get("DESKTOP_PET_AVATAR_WIDTH", "280"))
AVATAR_MIN_WIDTH = 120
AVATAR_MAX_WIDTH = 600

# 滾輪每格縮放的像素量。
AVATAR_SCROLL_STEP = 20

# 滾輪停下來這麼久（毫秒）才真的重繪，避免連續滾動時每一格都觸發一次
# 重繪造成畫面卡頓。
_AVATAR_RESIZE_DEBOUNCE_MS = 60

# 角色說話語言的常見選項，給右鍵選單快速切換用；不在這份清單裡的語言，
# 走選單裡的「自訂...」，並不是只能從這幾個裡面選。
SPEECH_LANGUAGE_PRESETS = ["日文", "中文", "英文", "韓文"]

# 音量驅動開合嘴的取樣間隔（毫秒）。這不是真的音素對齊，只是每隔這麼久
# 取樣一次目前音量，決定嘴巴該開還該合。
MOUTH_HOP_MS = 90

# 這段音量要超過整句最大音量的這個比例，才會被判定為「張嘴」。
MOUTH_THRESHOLD_RATIO = 0.3

# 滑鼠按下去之後，移動距離要超過這個門檻（像素）才算是「拖曳」，
# 沒超過的話就當成單純的「點擊」。
DRAG_THRESHOLD_PX = 3


# ============================================================
# 桌寵物理設定（重力／落地，詳見 desktop_physics.py）
# ============================================================
#
# 這些數值是純手感調出來的，不是物理正確的單位換算，想調快／調慢
# 直接改這幾個數字就好。

# 重力／落地判定的更新頻率，約 30fps。
PHYSICS_TICK_MS = 33
GRAVITY_PX_S2 = 2200.0
FALL_TERMINAL_VELOCITY_PX_S = 1400.0

# 落地後的自主漫遊：左右滑動現有靜態立繪（翻轉表示方向），不是真的走路
# 動畫，同樣是純手感調出來的值。
WANDER_SPEED_PX_S = 110.0
WANDER_MIN_INTERVAL_SEC = 5.0  # 兩次漫遊之間至少等這麼久。
WANDER_MAX_INTERVAL_SEC = 15.0
WANDER_MIN_DURATION_SEC = 1.0  # 一次漫遊走多久。
WANDER_MAX_DURATION_SEC = 3.0


# ============================================================
# 螢幕讀取主動搭話（規劃見 TTS通用平台_專案規劃.md 10.5）
# ============================================================
#
# 這個功能的開關，刻意不是靠改 .env 這種要開檔案編輯的方式來決定
# （試用門檻太高，等於變相勸退使用者），而是程式啟動時跳出詢問視窗、
# 加上右鍵選單隨時可以切換——「要不要記住這次選擇」才是使用者真正
# 要做的決定，而不是「要不要開這個功能」本身。
#
# 基於同一個理由，「對話／語音辨識要走本地還是雲端」也一併做成右鍵選單
# 可以直接切換，不用進 .env 改。所有記住的偏好統一存進下面這個本機
# 小檔案（_PET_PREFS_FILE）；不記住的話，下次開程式還是照 .env 的預設值。

SCREEN_WATCH_INTERVAL_SEC = int(os.environ.get("SCREEN_WATCH_INTERVAL_SEC", "45"))  # 2026-11-08 使用者覺得 20 秒太密，改 45

# Claude Code 的 hook（scripts/desktop_pet/claude_hook.py）透過這個 UDP 埠通知桌寵：
# Claude 在等主人確認權限、或做完一件比較久的工作。只聽本機。
CLAUDE_EVENT_PORT = int(os.environ.get("CLAUDE_EVENT_PORT", "9892"))
CLAUDE_EVENT_RETRY_MS = 2000  # 角色正在講話時，隔多久再試一次
CLAUDE_EVENT_MAX_TRIES = 30  # 最多等一分鐘，還在忙就放棄這次通知
