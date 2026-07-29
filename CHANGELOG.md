# Changelog

版號規則：語意化版本（`vX.Y.Z`）——累積到一個功能完整的里程碑才打 tag，不是每個
commit 都算一版。日期為 GitHub 發布日期。

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
