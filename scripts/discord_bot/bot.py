"""
Discord bot：在文字頻道打字，或直接在語音頻道講話，bot 進入你所在的語音頻道，
直接用語音回覆（不是丟檔案）。

前置需求：
    - .env 裡要有 DISCORD_BOT_TOKEN（見 .env.example）
    - 本地 Ollama 服務要在跑（chat.py 依賴的部分）
    - GPT-SoVITS 推理服務會自動偵測、需要的話自動啟動（跟 talk_to_sakiko.bat 共用邏輯）
    - ffmpeg 要在系統 PATH 裡（discord.py 語音播放需要）
    - 語音輸入依賴 discord-ext-voice-recv + faster-whisper（見 voice_stt.py）

用法：
    1. 雙擊專案根目錄的 start_discord_bot.bat
    2. 自己先加入一個語音頻道
    3. 在文字頻道打字，或直接在語音頻道講話，bot 都會回覆
       （語音輸入要 bot 已經在頻道裡才聽得到，所以第一次互動還是要先打字把 bot 叫進頻道）
"""
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import discord
from discord.ext import voice_recv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gpt_sovits"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "llm"))

from common.env import load_dotenv  # noqa: E402
from chat import chat_ja, translate_zh  # noqa: E402
from tts_infer import generate  # noqa: E402
from tts_server import is_server_ready, start_server  # noqa: E402
import voice_stt  # noqa: E402

load_dotenv()
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@dataclass
class Incoming:
    """一次要回應的請求，不管是打字還是講話觸發的，進到這裡之後走同一條處理路徑"""

    guild: discord.Guild
    voice_channel: discord.VoiceChannel
    reply_channel: discord.abc.Messageable
    text: str


# 每個伺服器一條隊伍＋一個背景 worker，同一伺服器的請求（不管文字還是語音觸發）
# 依序處理，避免多人同時講話/打字互相搶著操作同一個 voice_client
# （打斷彼此的語音、回覆順序錯亂）。不同伺服器之間彼此獨立，不會互相卡住。
_guild_queues: dict[int, asyncio.Queue] = {}
_guild_workers: dict[int, asyncio.Task] = {}

# 語音輸入觸發時沒有現成的文字頻道可以回覆，記錄每個伺服器最近一次打字的頻道當備援
_last_text_channel: dict[int, discord.abc.Messageable] = {}


def _get_guild_queue(guild_id: int) -> asyncio.Queue:
    queue = _guild_queues.get(guild_id)
    if queue is None:
        queue = asyncio.Queue()
        _guild_queues[guild_id] = queue
        _guild_workers[guild_id] = asyncio.create_task(_guild_worker(guild_id, queue))
    return queue


def _pick_reply_channel(guild: discord.Guild) -> discord.abc.Messageable | None:
    channel = _last_text_channel.get(guild.id)
    if channel is not None:
        return channel
    for c in guild.text_channels:
        if c.permissions_for(guild.me).send_messages:
            return c
    return None


async def _guild_worker(guild_id: int, queue: asyncio.Queue):
    while True:
        item: Incoming = await queue.get()
        try:
            await _handle_incoming(item)
        except Exception as e:
            print(f"[guild {guild_id}] 處理訊息時發生未預期錯誤：{e}")
        finally:
            queue.task_done()


async def _play_audio(voice_client: discord.VoiceClient, audio_path) -> None:
    """播放並等到播完才回傳。新訊息不會打斷正在播的語音，只會排在後面等。"""
    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    def _after(err):
        if err:
            print(f"播放發生錯誤：{err}")
        loop.call_soon_threadsafe(done.set)

    voice_client.play(discord.FFmpegPCMAudio(str(audio_path)), after=_after)
    await done.wait()


async def _timed(coro):
    """包一層計時，量的是這個 task 自己實際跑多久，跟外面什麼時候去 await 它無關"""
    t0 = time.monotonic()
    result = await coro
    return result, time.monotonic() - t0


async def _ensure_voice_connected(guild: discord.Guild, voice_channel: discord.VoiceChannel) -> voice_recv.VoiceRecvClient:
    """連進（或換到）目標語音頻道，並確保正在監聽語音輸入。回傳可以拿來播放/監聽的 voice_client。"""
    voice_client = guild.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
    elif voice_client.channel.id != voice_channel.id:
        await voice_client.move_to(voice_channel)

    if not voice_client.is_listening():
        voice_stt.start_listening(voice_client, _on_voice_utterance)

    return voice_client


async def _handle_incoming(item: Incoming):
    t_start = time.monotonic()

    async with item.reply_channel.typing():
        try:
            t0 = time.monotonic()
            reply = await asyncio.to_thread(chat_ja, item.text)
            t_chat_ja = time.monotonic() - t0
        except Exception as e:
            await item.reply_channel.send(f"對話生成失敗：{e}")
            return

        # 語音生成跟中文翻譯平行跑：TTS 只需要 reply_ja/tone，不必等翻譯完成
        audio_task = asyncio.create_task(_timed(asyncio.to_thread(generate, reply.reply_ja, None, reply.tone)))
        zh_task = asyncio.create_task(_timed(asyncio.to_thread(translate_zh, reply.reply_ja)))

        try:
            audio_path, t_generate = await audio_task
        except Exception as e:
            zh_task.cancel()
            await item.reply_channel.send(f"語音生成失敗：{e}")
            return

        try:
            reply_zh, t_translate = await zh_task
        except Exception as e:
            reply_zh = f"（翻譯失敗：{e}）"
            t_translate = -1.0

    t_total = time.monotonic() - t_start
    print(
        f"[timing] chat_ja={t_chat_ja:.2f}s generate={t_generate:.2f}s "
        f"translate_zh={t_translate:.2f}s total={t_total:.2f}s"
    )

    voice_client = await _ensure_voice_connected(item.guild, item.voice_channel)

    # 同一伺服器的訊息已經由隊伍序列處理，理論上不會有別的播放在進行；
    # 保險起見還是等前一段播完，確保新訊息絕對不會打斷正在播的語音。
    while voice_client.is_playing():
        await asyncio.sleep(0.1)

    await item.reply_channel.send(reply_zh)
    await _play_audio(voice_client, audio_path)


async def _on_voice_utterance(member: discord.Member, text: str):
    """語音輸入斷句、轉文字完成後的 callback（voice_stt 從背景執行緒丟回事件迴圈呼叫）"""
    voice_state = member.voice
    if voice_state is None or voice_state.channel is None:
        return  # 講完話當下已經離開語音頻道了，忽略

    reply_channel = _pick_reply_channel(member.guild)
    if reply_channel is None:
        print(f"[guild {member.guild.id}] 語音輸入「{text}」沒有可以回覆的文字頻道，忽略")
        return

    print(f"[voice_stt] {member.display_name}：{text}")
    queue = _get_guild_queue(member.guild.id)
    await queue.put(Incoming(member.guild, voice_state.channel, reply_channel, text))


@client.event
async def on_ready():
    print(f"已登入：{client.user}")
    if not is_server_ready():
        print("TTS 推理服務尚未啟動，正在自動啟動（第一次要載入模型，約需 30 秒～1 分鐘）...")
        await asyncio.to_thread(start_server)
        print("TTS 服務啟動完成")
    print("待命中，加入一個語音頻道後在文字頻道打字就可以了")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    if not message.content.strip():
        return

    _last_text_channel[message.guild.id] = message.channel

    voice_state = message.author.voice
    if voice_state is None or voice_state.channel is None:
        await message.channel.send("要先加入一個語音頻道，我才能進去講話喔。")
        return

    queue = _get_guild_queue(message.guild.id)
    await queue.put(Incoming(message.guild, voice_state.channel, message.channel, message.content))


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("找不到 DISCORD_BOT_TOKEN，請在專案根目錄的 .env 裡設定（可參考 .env.example）。")
        sys.exit(1)
    client.run(DISCORD_BOT_TOKEN)
