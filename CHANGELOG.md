# Changelog

版號規則：語意化版本（`vX.Y.Z`）——累積到一個功能完整的里程碑才打 tag，不是每個
commit 都算一版。日期為 GitHub 發布日期。

## [未發布] - 2026-11-08

- 啟動方式整併成 `start_desktop_pet_hidden.vbs`（背景啟動、無黑視窗、輸出寫進 `logs/`、結束桌寵時連舞台和 TTS 一起關）＋ `stop_desktop_pet_hidden.bat`（強制關閉）
- 移除用不到的入口：`start_desktop_pet.bat`、`start_desktop_pet_livliv.bat`、`start_desktop_pet_reactive.bat`＋互動反應型原型 `desktop_pet_reactive.py`、`start_tts_inference.bat`、終端機 demo `talk_to_sakiko.bat`＋`chat_cli.py`
- 螢幕讀取省資源：截圖縮到長邊 1280、vision context 4096（顯示記憶體 5.4 → 2.0 GB）、描述改短、換視窗或隔一段時間才真的看圖，其他時候沿用上次描述；新增右鍵「遊戲模式（少看螢幕）」，同一視窗改成 2 分鐘才重看
- 人設加入說話規則（口癖頻率、禁止重複開頭、不寫動作描述）、喜好與小缺點，口癖依說話語言套用
- 每則訊息最後補「只能用某語言」的提醒，修正切成中文還是講日文、日文夾中文詞
- serious 語氣限定在真的生氣、訓人時使用（原本一半以上的句子被判成 serious，聲音整天很低沉）
- 平常語氣（neutral）的參考音檔換成較明亮的台詞
- 上網查資料（`scripts/llm/web_lookup.py`，維基百科公開 API，免費）：開新遊戲／程式時自動在背景查；她說不確定、或主人說「查一下」時查完再回答
- 看螢幕：告訴她視窗標題和程式名稱；沿用舊描述時提醒她換話題，修正遊戲模式下一直講同一句；間隔改 45 秒
- 人設：有現實世界的知識（遊戲、科技等），用角色的口吻講
- 看圖模型閒置 1 分鐘就卸載，關閉桌寵時對話和看圖模型立刻卸載（不再讓 llama-server 多佔 5 分鐘）
- 表情封閉詞表（`scripts/llm/expressions.py`）：模型在台詞裡插〔害羞〕等標記，唸之前拿掉，舞台照標記在句子裡的位置換表情
- 人設改成「以原作角色為原型、住在電腦裡的 AI」
- 修正背景啟動檔：`timeout` 在沒有主控台時會立刻結束，改用 `ping` 等待語音服務
- 接 Claude Code hook（`claude_hook.py`）：Claude 等權限確認、或做完超過 1 分鐘的工作時，角色會主動提醒

Launchers consolidated into a hidden, console-free `start_desktop_pet_hidden.vbs`
(plus an emergency `stop_desktop_pet_hidden.bat`); unused entry points and the
reactive prototype removed. Screen watching uses far less VRAM and GPU time and
gains a "game mode". Persona rules, a per-message speech-language reminder, a
stricter "serious" tone and a brighter neutral voice reference.

## [0.1.1] - 2026-07-29

- README 補齊剩下幾段的英文版本(先前只有開頭跟版權聲明有雙語),移除瑣碎的「Windows only」提示行

README is now fully bilingual throughout (previously only the intro and
copyright sections were); dropped a trivial "Windows only" line.

## [0.1.0] - 2026-07-29

第一個公開版本的完整功能快照。

- 打字 或 按住熱鍵語音輸入（SenseVoice/Whisper），角色回覆語音＋文字翻譯＋語氣標籤，TTS 依語氣切換聲音表現
- 桌面上飄浮的透明立繪視窗（「桌寵」），無邊框、只有角色本人；滾輪即時縮放大小
- LLM/STT 可選本地（Ollama/SenseVoice）或雲端（OpenAI），右鍵選單即時切換，不用改 `.env`、不用重開程式
- 角色說話語言可設定（右鍵選單常見語言 + 自訂輸入），預設日文，換角色不用改程式碼
- 選用功能：螢幕讀取主動搭話——定期截圖、`qwen3-vl:2b` 看圖轉文字、角色自己決定要不要開口；預設關閉、有排除清單（隱私黑名單 + 桌寵本體/桌面殼視窗）
- GPT-SoVITS 斷句方法改用 `cut3`，減少句中拖拍
- 姊妹專案 [voice-dataset-toolkit](https://github.com/kukuo2510/voice-dataset-toolkit) 負責訓練新角色聲音

First public feature-complete snapshot: text/voice input, floating desktop-pet
avatar, switchable local/cloud LLM+STT, configurable speech language,
optional screen-watch proactive speech (privacy-gated, off by default), and
a smoother TTS sentence-splitting default.
