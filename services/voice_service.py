"""ギルド別の音声キュー・再生・キープアライブを管理する。"""

import asyncio
import os

import aiohttp
import discord

from config import DISCORD_BOT_TOKEN
from models.audio_item import AudioItem, SoundboardItem, TTSItem
from models.guild_session import GuildSession


class VoiceService:
  def __init__(self, client):
    self.client = client
    self.sessions: dict[int, GuildSession] = {}

  def get_session(self, guild_id: int) -> GuildSession:
    """ギルドの実行状態を取得し、存在しなければ初期化する。"""
    session = self.sessions.get(guild_id)
    if session is None:
      session = GuildSession()
      self.sessions[guild_id] = session
    return session

  async def enqueue(self, guild, item: AudioItem) -> None:
    """音声アイテムをキューへ追加し、必要なら再生ループを開始する。"""
    session = self.get_session(guild.id)
    await session.queue.put(item)
    if session.player_task is None or session.player_task.done():
      session.player_task = asyncio.create_task(self.play_loop(guild))

  def begin_clear(self, guild, instant: bool) -> tuple[int, list[str]]:
    """キューをドレインし、削除対象のTTSファイルを返す。"""
    session = self.get_session(guild.id)
    queue = session.queue
    cleared = queue.qsize()
    pending_files = []
    while not queue.empty():
      try:
        item = queue.get_nowait()
        if isinstance(item, TTSItem):
          pending_files.append(item.path)
        queue.task_done()
      except asyncio.QueueEmpty:
        break
    session.skipping = True
    if instant and guild.voice_client and guild.voice_client.is_playing():
      guild.voice_client.stop()
    session.clearing = True
    return cleared, pending_files

  async def finish_clear(self, guild_id: int, pending_files: list[str]) -> None:
    """待機中のTTSファイルを削除してクリア状態を解除する。"""
    session = self.get_session(guild_id)
    try:
      await asyncio.sleep(1)
      for path in pending_files:
        await self.safe_remove(path)
    finally:
      session.clearing = False

  def skip_current(self, guild) -> None:
    if guild.voice_client and guild.voice_client.is_playing():
      guild.voice_client.stop()

  async def play_loop(self, guild):
    guild_id = guild.id
    session = self.get_session(guild_id)
    while True:
      item = None
      try:
        item = await asyncio.wait_for(session.queue.get(), timeout=300)
        if guild.voice_client is None:
          session.queue.task_done()
          item = None
          continue
        if isinstance(item, SoundboardItem):
          while guild.voice_client is not None and guild.voice_client.is_playing():
            await asyncio.sleep(0.1)
          if guild.voice_client is not None:
            await self._play_soundboard(guild, item.sound_id)
        elif isinstance(item, TTSItem):
          await self.play(guild, item.path)
        else:
          raise TypeError(f"未対応の音声アイテムです: {type(item).__name__}")
        session.queue.task_done()
        item = None
      except asyncio.TimeoutError:
        break
      except asyncio.CancelledError:
        if isinstance(item, TTSItem):
          asyncio.create_task(self.safe_remove(item.path))
        raise
      except Exception as e:
        print(f"再生エラー: {e}")
        if item is not None:
          session.queue.task_done()

  async def _play_soundboard(self, guild, sound_id: str):
    try:
      async with aiohttp.ClientSession() as session:
        async with session.post(
          f'https://discord.com/api/v10/channels/{guild.voice_client.channel.id}/send-soundboard-sound',
          headers={
            'Authorization': f'Bot {DISCORD_BOT_TOKEN}',
            'Content-Type': 'application/json',
          },
          json={'sound_id': f'{sound_id}'},
        ):
          pass
    except Exception as e:
      print(f'サウンドボード再生エラー：{e}')

  async def _keepalive_loop(self, guild):
    silence = b'\xF8\xFF\xFE'
    while True:
      await asyncio.sleep(270)
      voice_client = guild.voice_client
      if voice_client is None or not voice_client.is_connected():
        break
      if not voice_client.is_playing():
        try:
          voice_client.send_audio_packet(silence, encode=False)
        except Exception as e:
          print(f"keepalive error: {e}")

  def start_keepalive(self, guild):
    self.stop_keepalive(guild.id)
    session = self.get_session(guild.id)
    session.keepalive_task = asyncio.create_task(self._keepalive_loop(guild))

  def stop_keepalive(self, guild_id):
    session = self.get_session(guild_id)
    task = session.keepalive_task
    session.keepalive_task = None
    if task and not task.done():
      task.cancel()

  async def safe_remove(self, path: str, retries: int = 5, delay: float = 0.3):
    for _ in range(retries):
      try:
        if os.path.exists(path):
          os.remove(path)
        return
      except PermissionError:
        await asyncio.sleep(delay)
    print(f"ファイル削除失敗（使用中）: {path}")

  async def play(self, guild, path: str):
    try:
      session = self.get_session(guild.id)
      voice = await discord.FFmpegOpusAudio.from_probe(path)
      if session.skipping:
        session.skipping = False
        asyncio.create_task(self.safe_remove(path))
        return
      while guild.voice_client is not None and guild.voice_client.is_playing():
        await asyncio.sleep(0.1)
      if session.skipping:
        session.skipping = False
        asyncio.create_task(self.safe_remove(path))
        return
      guild.voice_client.play(
        voice,
        after=lambda _: asyncio.run_coroutine_threadsafe(
          self.safe_remove(path), self.client.loop
        ),
      )
    except Exception as e:
      print(f"音声再生エラー: {e}")

