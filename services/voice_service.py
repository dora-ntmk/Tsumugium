"""ギルド別の音声キュー・再生・キープアライブを管理する。"""

import asyncio
import os

import discord

from models.audio_item import AudioItem, SoundboardItem, TTSItem
from models.guild_session import GuildSession
from services.error_notification_service import ensure_error_notifier


class VoiceService:
  def __init__(self, client, soundboard_client=None, error_notifier=None):
    self.client = client
    self.soundboard_client = soundboard_client
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.sessions: dict[int, GuildSession] = {}
    self._delayed_cleanup_paths: set[str] = set()

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
      session.player_task = self.error_notifier.create_task(
        self.play_loop(guild),
        f"play loop guild_id={guild.id}",
      )

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
      if session.current_tts_path is not None:
        session.expected_stop_paths.add(session.current_tts_path)
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
      session = self.get_session(guild.id)
      if session.current_tts_path is not None:
        session.expected_stop_paths.add(session.current_tts_path)
      guild.voice_client.stop()

  def connection_context(self, guild) -> dict[str, object]:
    session = self.get_session(guild.id)
    voice_client = guild.voice_client
    return {
      "guild_id": guild.id,
      "channel_id": getattr(getattr(voice_client, "channel", None), "id", None),
      "voice_client_present": voice_client is not None,
      "voice_connected": (
        voice_client.is_connected() if voice_client is not None else False
      ),
      "queue_size": session.queue.qsize(),
      "discord_py_version": discord.__version__,
    }

  async def discard_queue(self, guild_id: int) -> int:
    """切断時に待機アイテムを破棄し、生成済みWAVを削除する。"""
    session = self.get_session(guild_id)
    discarded = 0
    while True:
      try:
        item = session.queue.get_nowait()
      except asyncio.QueueEmpty:
        break
      try:
        discarded += 1
        if isinstance(item, TTSItem):
          await self.safe_remove(item.path)
      finally:
        session.queue.task_done()
    return discarded

  async def play_loop(self, guild):
    guild_id = guild.id
    session = self.get_session(guild_id)
    while True:
      item = None
      try:
        item = await asyncio.wait_for(session.queue.get(), timeout=300)
        voice_client = guild.voice_client
        if voice_client is None or not voice_client.is_connected():
          if isinstance(item, TTSItem):
            await self.safe_remove(item.path)
          continue
        if isinstance(item, SoundboardItem):
          while voice_client.is_connected() and voice_client.is_playing():
            await asyncio.sleep(0.1)
          if voice_client.is_connected():
            await self._play_soundboard(guild, item.sound_id)
        elif isinstance(item, TTSItem):
          await self.play(guild, item.path)
        else:
          raise TypeError(f"未対応の音声アイテムです: {type(item).__name__}")
      except asyncio.TimeoutError:
        break
      except asyncio.CancelledError:
        if isinstance(item, TTSItem):
          await self.safe_remove(item.path)
        raise
      except Exception as e:
        self.error_notifier.report_exception(
          e, "play loop", self.connection_context(guild)
        )
      finally:
        if item is not None:
          session.queue.task_done()

  async def _play_soundboard(self, guild, sound_id: str):
    try:
      if self.soundboard_client is None:
        raise RuntimeError("DiscordSoundboardClientが設定されていません")
      voice_client = guild.voice_client
      if voice_client is None or not voice_client.is_connected():
        return
      await self.soundboard_client.play(voice_client.channel.id, sound_id)
    except Exception as e:
      self.error_notifier.report_exception(
        e, "Discord Soundboard playback", self.connection_context(guild)
      )

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
          self.error_notifier.report_exception(
            e, "voice keepalive", self.connection_context(guild)
          )

  def start_keepalive(self, guild):
    self.stop_keepalive(guild.id)
    session = self.get_session(guild.id)
    session.keepalive_task = self.error_notifier.create_task(
      self._keepalive_loop(guild),
      f"keepalive loop guild_id={guild.id}",
    )

  def stop_keepalive(self, guild_id):
    session = self.get_session(guild_id)
    task = session.keepalive_task
    session.keepalive_task = None
    if task and not task.done():
      task.cancel()

  async def safe_remove(
      self,
      path: str,
      retries: int = 5,
      delay: float = 0.3,
      *,
      schedule_delayed_retry: bool = True,
  ) -> bool:
    """WAVを削除し、使用中ならFFmpeg終了後の再試行を予約する。"""
    for _ in range(retries):
      try:
        if os.path.exists(path):
          os.remove(path)
        return True
      except PermissionError:
        await asyncio.sleep(delay)
    if schedule_delayed_retry:
      if path not in self._delayed_cleanup_paths:
        self._delayed_cleanup_paths.add(path)
        self.error_notifier.create_task(
          self._delayed_remove(path),
          f"delayed audio cleanup path={path}",
        )
      return False
    self.error_notifier.report(f"ファイル削除失敗（時間差再試行後）: {path}")
    return False

  async def _delayed_remove(self, path: str) -> None:
    """FFmpegのファイルハンドル解放を待ってから削除を再試行する。"""
    try:
      await asyncio.sleep(5)
      await self.safe_remove(
        path,
        retries=10,
        delay=1,
        schedule_delayed_retry=False,
      )
    finally:
      self._delayed_cleanup_paths.discard(path)

  async def play(self, guild, path: str):
    playback_started = False
    try:
      session = self.get_session(guild.id)
      voice = await discord.FFmpegOpusAudio.from_probe(path)
      if session.skipping:
        session.skipping = False
        self.error_notifier.create_task(
          self.safe_remove(path),
          f"skipped audio cleanup path={path}",
        )
        return
      voice_client = guild.voice_client
      if voice_client is None or not voice_client.is_connected():
        return
      while voice_client.is_connected() and voice_client.is_playing():
        await asyncio.sleep(0.1)
      if session.skipping:
        session.skipping = False
        self.error_notifier.create_task(
          self.safe_remove(path),
          f"skipped audio cleanup path={path}",
        )
        return
      if not voice_client.is_connected():
        return
      session.current_tts_path = path
      voice_client.play(
        voice,
        after=lambda error: asyncio.run_coroutine_threadsafe(
          self._finish_playback(guild, path, error), self.client.loop
        ),
      )
      playback_started = True
    except Exception as e:
      voice_client = guild.voice_client
      disconnected = (
        voice_client is None or not voice_client.is_connected()
      )
      if not disconnected and "Not connected to voice" not in str(e):
        self.error_notifier.report_exception(
          e, "TTS playback start", self.connection_context(guild)
        )
    finally:
      if not playback_started:
        session = self.get_session(guild.id)
        if session.current_tts_path == path:
          session.current_tts_path = None
        await self.safe_remove(path)

  async def _finish_playback(self, guild, path: str, error) -> None:
    session = self.get_session(guild.id)
    expected_stop = path in session.expected_stop_paths
    session.expected_stop_paths.discard(path)
    if session.current_tts_path == path:
      session.current_tts_path = None
    await self.safe_remove(path)
    if error is not None and not expected_stop:
      voice_client = guild.voice_client
      disconnected = (
        voice_client is None or not voice_client.is_connected()
      )
      if not disconnected and "Not connected to voice" not in str(error):
        self.error_notifier.report_exception(
          error, "TTS audio player", self.connection_context(guild)
        )
