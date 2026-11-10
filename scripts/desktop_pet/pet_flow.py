"""對話主流程：送出訊息、按住說話、LLM+TTS 背景處理、事件佇列輪詢。

從 desktop_pet.py 的 CharacterWindow 原封不動搬出來的方法（self.* 全部照舊）。
不能 import desktop_pet（會循環），要用的常數／函式從 pet_config 等模組拿。
"""

import queue
import threading

from pet_config import PUSH_TO_TALK_KEY, PUSH_TO_TALK_KEY_LABEL
from pet_audio import play_for_avatar
from chat import chat_ja
from chat import translate_zh
from tts_chunker import split_for_tts
import expressions
import livliv_link
import web_lookup


class ConversationFlowMixin:
    """對話主流程：送出訊息、按住說話、LLM+TTS 背景處理、事件佇列輪詢。"""

    # ============================================================
    # 送出訊息 / LLM + TTS 呼叫
    # ============================================================

    def _submit(self, text: str) -> None:
        """送出一句使用者輸入：記錄歷史、標記忙碌狀態，並在背景執行緒處理後續流程。"""
        if self._busy:
            return

        self._append_history("你", text)
        self._busy = True
        self._set_dialogue("...", is_status=True)
        threading.Thread(target=self._handle_message, args=(text,), daemon=True).start()

    def _on_key_press(self, key) -> None:
        """全域鍵盤事件：按下 push-to-talk 熱鍵時開始錄音（忙碌中不接受新錄音）。"""
        if key != PUSH_TO_TALK_KEY:
            return
        if self._busy:
            print(f"[ptt] 按下 {PUSH_TO_TALK_KEY_LABEL}，但還在處理上一句，忽略")
            return
        # pynput 的 callback 丟出例外時監聽會默默結束，之後熱鍵就完全沒反應，所以這裡要接住。
        try:
            self._ptt_recorder.start()
        except Exception as e:
            print(f"[ptt] 開始錄音失敗：{e!r}")

    def _on_key_release(self, key) -> None:
        """全域鍵盤事件：放開 push-to-talk 熱鍵時停止錄音。"""
        if key == PUSH_TO_TALK_KEY:
            try:
                self._ptt_recorder.stop()
            except Exception as e:
                print(f"[ptt] 停止錄音失敗：{e!r}")

    def _on_ptt_state_change(self, recording: bool) -> None:
        """錄音狀態改變時，更新對話框顯示的狀態文字。"""

        def _update():
            if recording:
                self._set_dialogue(f"錄音中...放開 {PUSH_TO_TALK_KEY_LABEL} 結束", is_status=True)
            elif not self._busy:
                self._clear_status()

        self.root.after(0, _update)

    def _on_ptt_transcribed(self, text: str) -> None:
        """語音轉文字完成：直接送出（取捨說明見檔案開頭 docstring）。"""
        self.root.after(0, lambda: self._submit(text))

    def _handle_message(self, text: str, allow_lookup: bool = True) -> None:
        """在背景執行緒裡處理一輪完整的對話：LLM 生成 -> 分段語音合成＋翻譯 -> 邊合成邊播放。

        所有結果都透過 self._task_queue 傳回主執行緒（見 _poll_queue），
        因為 Tkinter 的元件只能在主執行緒操作，背景執行緒不能直接更新畫面。

        ## 分段合成、邊合成邊播放（參考 Project AIRI 的 speech pipeline）

        以前是整段台詞合成完才開始播，台詞一長就要乾等。現在先用
        split_for_tts() 把台詞切成幾段，由 _produce_audio 在背景一段一段
        合成、丟進 audio_queue；這裡一拿到第一段就開始播放，播放的同時
        下一段已經在合成了。使用者實際等待的時間只剩「第一段」的合成時間。

        中文翻譯另外開一條執行緒跑，翻好就先顯示在對話框，不用等語音。
        """
        try:
            reply = chat_ja(text, history=self._conversation)
            # 要查資料的兩種情況（一則訊息最多查一次）：
            # 1. 她自己只回了〔查：關鍵字〕
            # 2. 她回答裡說不確定（本機模型很少主動查，但會老實說不清楚）→ 另外問一次要查什麼，
            #    這一問不帶對話紀錄、也不存進去；她說〔不用查〕就照原本的回答講
            # 3. 主人明講「查一下」「幫我查」：一定查
            query = web_lookup.find_request(reply.reply_speech)
            if not query and allow_lookup and (web_lookup.sounds_unsure(reply.reply_speech)
                                               or web_lookup.asked_to_search(text)):
                query = web_lookup.find_request(chat_ja(web_lookup.query_prompt(text)).reply_speech)
            if query:
                self.root.after(0, lambda q=query: self._set_dialogue(f"（查資料中：{q}）", is_status=True))
                result = web_lookup.search(query)
                reply = chat_ja(web_lookup.followup_prompt(query, result), history=self._conversation)
                reply.reply_speech = web_lookup.strip_requests(reply.reply_speech)
        except Exception as e:
            self._task_queue.put(("error", f"對話生成失敗：{e}"))
            return

        # 台詞裡的〔害羞〕這種表情標記不能唸、也不能翻譯，先拿掉；
        # 每一段的表情排程在播放那一段之前傳給舞台（見 expressions.py）。
        spoken, _ = expressions.split_tags(reply.reply_speech)

        # 翻譯跟語音合成平行跑：播放／對嘴只需要 reply_speech 跟 tone。
        t_zh = threading.Thread(target=self._translate_and_show, args=(spoken,), daemon=True)
        t_zh.start()

        # 分段播放關閉時，整段當成「只有一段」走同一套流程，行為就跟以前一樣。
        if self._chunked_tts:
            raw_chunks = split_for_tts(reply.reply_speech) or [reply.reply_speech]
        else:
            raw_chunks = [reply.reply_speech]
        segments = expressions.split_chunks(raw_chunks) or [(spoken, [])]
        chunks = [text for text, _ in segments]
        plans = [plan for _, plan in segments]
        # 每段的語氣跟著那段的表情走（例如〔難過〕用 hesitant），沒有標記才用模型選的 tone
        tones = [expressions.tone_for(plan, reply.tone) for plan in plans]
        audio_queue: "queue.Queue[tuple]" = queue.Queue()
        threading.Thread(
            target=self._produce_audio, args=(chunks, tones, audio_queue), daemon=True
        ).start()

        # 依序播放每一段：queue.get() 會等到那一段合成完成才返回。
        segment = 0
        while True:
            kind, payload = audio_queue.get()
            if kind == "end":
                break
            if kind == "error":
                self._task_queue.put(("error", f"語音生成失敗：{payload}"))
                t_zh.join()
                return

            data, samplerate = payload
            livliv_link.send_expr_plan(plans[segment] if segment < len(plans) else [])
            segment += 1
            # 只有真的在播語音時才算「說話中」：動畫暫停、交給嘴型換圖（見 pet_anim_window）。
            # 想回覆、合成、翻譯的空檔動畫照常播，不會整段卡在靜態圖。
            self._speaking = True
            try:
                play_for_avatar(data, samplerate, on_mouth=self._queue_mouth_state)
            except Exception as e:
                print(f"[desktop_pet] 播放失敗：{e}")
            finally:
                self._speaking = False

        # 等翻譯也結束才解除忙碌，避免下一輪對話開始後，上一輪的翻譯才姍姍來遲
        # 蓋掉新的對話框內容。
        t_zh.join()
        self._task_queue.put(("done", None))

    def _translate_and_show(self, reply_speech: str) -> None:
        """背景執行緒：把角色台詞翻成中文，翻好就送去主執行緒顯示並記進歷史紀錄。"""
        try:
            reply_zh = translate_zh(reply_speech)
        except Exception as e:
            reply_zh = f"（翻譯失敗：{e}）"
        self._task_queue.put(("reply", reply_zh))

    def _queue_mouth_state(self, is_open: bool) -> None:
        """播放執行緒呼叫：把嘴巴開合狀態送進佇列，交給主執行緒更新畫面。"""
        self._task_queue.put(("mouth", is_open))

    def _on_stage_event(self, event: dict) -> None:
        """處理舞台視窗傳來的操作：打字送出、右鍵開選單、點選單項目。"""
        if event.get("event") == "text":
            self._submit(str(event.get("text", "")).strip())
        elif event.get("event") == "menu":
            # 看不見的主視窗移到游標旁，語言設定這類對話框才會開在角色附近。
            self.root.geometry(f"1x1+{int(event['x'])}+{int(event['y'])}")
            livliv_link.send_menu(livliv_link.describe_menu(self._context_menu))
        elif event.get("event") == "menuitem":
            livliv_link.invoke_menu(self._context_menu, event["path"])

    def _poll_queue(self) -> None:
        """主執行緒定時輪詢背景執行緒送來的事件，並依種類更新畫面或狀態。"""
        try:
            while True:
                kind, payload = self._task_queue.get_nowait()

                if kind == "reply":
                    self._append_history("祥子", payload)
                elif kind == "error":
                    self._append_history("系統", payload)
                    self._set_dialogue(payload, is_status=True)
                elif kind == "mouth":
                    self.avatar_label.configure(image=self._current_frame(is_open=payload))
                    self._anim_last = None  # 嘴型蓋掉了動畫幀，動畫下一個 tick 要重畫
                elif kind == "stage":
                    self._on_stage_event(payload)

                if kind in ("error", "done"):
                    self._busy = False
        except queue.Empty:
            pass

        self.root.after(50, self._poll_queue)
