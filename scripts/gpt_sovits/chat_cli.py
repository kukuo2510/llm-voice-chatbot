"""互動式終端機聊天 demo。

流程：輸入中文文字 -> LLM 生成日文回覆＋中文翻譯＋語氣
     -> 自動生成語音並播放。

這支是範例角色「豐川祥子」的 CLI demo。換角色的話這支腳本本身不需要改，
角色相關的內容全部集中在 characters/sakiko/（persona.md、voice_config.py）
以及 tts_infer.py 裡 import 角色設定的那一行。

不用自己另外手動啟動推理服務——這支腳本會自動偵測服務是否已啟動，
沒啟動的話會自動幫你啟動。

用法：雙擊專案根目錄的 talk_to_sakiko.bat
"""

import os
import sys
from pathlib import Path

# 讓這支腳本能找到同一層的 tts_infer / tts_server，
# 以及上一層 llm/ 資料夾底下的 chat 模組。
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "llm"))

from tts_infer import generate  # noqa: E402
from tts_server import is_server_ready, start_server  # noqa: E402
from chat import chat  # noqa: E402


def main():
    # 先確保推理服務已經啟動，沒啟動的話就自動啟動並等它就緒，
    # 避免使用者第一句話就因為服務還沒起來而失敗。
    if not is_server_ready():
        start_server()

    print("========================================")
    print(" 跟豐川祥子對話（直接輸入空白或按 Ctrl+C 結束）")
    print("========================================\n")

    while True:
        try:
            text = input("你：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n結束")
            break

        if not text:
            print("結束")
            break

        # 第一步：呼叫 LLM 取得角色回覆。這一步失敗的話，
        # 代表這一輪對話整個沒有結果，直接跳過進入下一輪。
        try:
            reply = chat(text)
        except Exception as e:
            print(f"對話生成失敗：{e}\n")
            continue

        print(f"祥子：{reply.reply_zh}\n")

        # 第二步：把回覆拿去合成語音並播放。這一步就算失敗，
        # 文字回覆已經印出來了，所以只印錯誤訊息、不影響對話繼續進行。
        try:
            path = generate(reply.reply_speech, tone=reply.tone)
            print(f"（語氣：{reply.tone}，音檔：{path}）\n")
            os.startfile(path)
        except Exception as e:
            print(f"語音生成失敗：{e}\n")


if __name__ == "__main__":
    main()
