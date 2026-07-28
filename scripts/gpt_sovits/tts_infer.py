"""通用 GPT-SoVITS 語音推理腳本：呼叫本地 api_v2.py 服務生成語音。語氣參考音檔、
發音修正表、輸出資料夾名稱這些角色專屬設定，從 characters/<角色>/voice_config.py
讀——換角色只要複製一份 characters/<新角色>/voice_config.py、改下面 import 那一行
指到新角色，這支腳本本身的邏輯不用動。

前置需求：api_v2.py 要先啟動並載入對應角色的權重（或直接雙擊 start_tts_inference.bat）
    cd GPT-SoVITS
    python api_v2.py -a 127.0.0.1 -p 9880

用法：
    python scripts/gpt_sovits/tts_infer.py "みんな、今日から一緒に頑張りましょう。"
    python scripts/gpt_sovits/tts_infer.py "みんな、今日から一緒に頑張りましょう。" output.wav
    python scripts/gpt_sovits/tts_infer.py "みんな、今日から一緒に頑張りましょう。" output.wav happy

不指定輸出檔名時，會自動存到 outputs/<speaker>/ 底下（依時間戳命名），
並自動清掉舊檔案只保留最新 MAX_KEEP_FILES 筆——這裡是給 LLM 對話用的暫時語音輸出，
不是訓練資料，不需要長期保留每一句。

語氣（tone）是「指定切換」，不是模型自動判斷/學習：GPT-SoVITS 每次生成只吃一段
參考音檔，語氣完全由這段參考音檔的原始語調決定，這裡只是把幾種語氣的參考音檔
包成一個對照表，由呼叫端指定要用哪一種。
"""
import os
import sys
import json
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# 要換角色，把這裡的 "sakiko" 換成你自己的 characters/ 底下的資料夾名稱
from characters.sakiko.voice_config import (  # noqa: E402
    TONE_REFS, DEFAULT_TONE, SPEAKER_NAME, fix_pronunciation,
)

API_URL = os.environ.get("TTS_API_URL", "http://127.0.0.1:9880/tts")

OUTPUT_DIR = PROJECT_ROOT / "outputs" / SPEAKER_NAME
MAX_KEEP_FILES = 20  # 只保留最新幾筆，避免之後接 LLM 長時間跑下來塞爆磁碟


def synthesize(text: str, tone: str = DEFAULT_TONE, text_lang: str = "auto", text_split_method: str = "cut3") -> bytes:
    # cut3 只在句號（。）切段；GPT-SoVITS 內建的 cut5 連逗號、頓號、刪節號都會切，
    # 對多句、逗號多的回覆會切出過多段落，段落間的銜接空隙聽起來像講到一半拖了一下
    if tone not in TONE_REFS:
        raise ValueError(f"未知的語氣：{tone}，可用選項：{list(TONE_REFS)}")
    ref_audio_path, ref_prompt_text = TONE_REFS[tone]

    payload = {
        "text": fix_pronunciation(text),
        "text_lang": text_lang,
        "ref_audio_path": ref_audio_path,
        "prompt_text": ref_prompt_text,
        "prompt_lang": "ja",
        "text_split_method": text_split_method,
        # 官方預設 top_p=1／temperature=1 幾乎不收斂取樣範圍，同一句話每次生成
        # 品質落差很大（語氣忽然變怪、聲音發虛都跟這個有關）。收緊一點讓生成
        # 結果穩定，代價是語氣的隨機變化會少一些，聽起來略保守但比較不會爛
        "top_k": 8,
        "top_p": 0.7,
        "temperature": 0.7,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _cleanup_old_outputs():
    """只保留 outputs/{speaker}/ 底下最新 MAX_KEEP_FILES 筆，其餘刪除"""
    files = sorted(OUTPUT_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_file in files[MAX_KEEP_FILES:]:
        old_file.unlink()


def generate(text: str, output_path: str | None = None, tone: str = DEFAULT_TONE) -> Path:
    """生成語音並存檔，回傳實際存檔路徑。不指定 output_path 時自動存到 outputs/<speaker>/ 並自動清理舊檔"""
    audio_bytes = synthesize(text, tone=tone)

    if output_path:
        out = Path(output_path)
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUTPUT_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}.wav"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio_bytes)

    if not output_path:
        _cleanup_old_outputs()

    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('用法: python tts_infer.py "要說的話" [輸出檔名] [語氣：neutral/happy/serious/hesitant]')
        sys.exit(1)

    text = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    tone = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_TONE
    saved_path = generate(text, out_path, tone)
    print(f"已產生（語氣：{tone}）：{saved_path}")
