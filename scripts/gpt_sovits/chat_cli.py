"""互動式終端機聊天 demo：輸入中文文字 → LLM 生成日文回覆＋中文翻譯＋語氣 → 自動生成
語音並播放。這支是範例角色「豐川祥子」的 CLI demo，換角色的話這支腳本本身不用改，
角色內容都在 characters/sakiko/（persona.md、voice_config.py）和 tts_infer.py 的
import 那一行。不用自己另外啟動推理服務，會自動偵測、需要的話自動啟動。

用法：雙擊專案根目錄的 talk_to_sakiko.bat
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "llm"))
from tts_infer import generate  # noqa: E402
from tts_server import is_server_ready, start_server  # noqa: E402
from chat import chat  # noqa: E402


def main():
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

        try:
            reply = chat(text)
        except Exception as e:
            print(f"對話生成失敗：{e}\n")
            continue

        print(f"祥子：{reply.reply_zh}\n")

        try:
            path = generate(reply.reply_ja, tone=reply.tone)
            print(f"（語氣：{reply.tone}，音檔：{path}）\n")
            os.startfile(path)
        except Exception as e:
            print(f"語音生成失敗：{e}\n")


if __name__ == "__main__":
    main()
