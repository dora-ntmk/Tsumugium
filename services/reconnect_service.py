"""Botを止めるときにVCから抜け、再起動・更新のあとに同じVCへつなぎ直す。

- つないでいるVCと読み上げチャンネルを、VCにつなぐたび・抜けるたびと、1分おきに
  UPDATE_STATE_DIR/voice-sessions.json へ書き残す。異常で落ちたときも、最後に動いていた時刻がわかる。
- 止めるとき（ManagedDiscordClient.close() の最初）は、インストーラーが残した止める理由
  （restart-reason.json）を見て、読み上げチャンネルに知らせてからVCを抜ける。手で止めたときは黙って抜ける。
- 起動したときは、手で止めたのでなく、最後に動いていた時刻から3分以内なら、人がいるVCにつなぎ直す。

UPDATE_STATE_DIR が空欄（インストーラーを使わずに動かしている）なら何もしない。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from typing import Callable

from presentation.embeds import EmbedType, make_embed
from services.error_notification_service import ensure_error_notifier


SESSIONS_FILE = "voice-sessions.json"
RESTART_REASON_FILE = "restart-reason.json"
REASON_UPDATE = "update"
REASON_MANUAL_STOP = "manual_stop"

SAVE_INTERVAL = 60.0
# 最後に動いていた時刻から、これより長く止まっていたらつなぎ直さない
MAX_DOWNTIME = 180.0
# 止めるときの知らせと切断にかける時間の上限（systemd の停止を待たせすぎない）
SHUTDOWN_TIMEOUT = 10.0


def restart_embed(reason: str | None, bot_name: str):
  if reason == REASON_UPDATE:
    text = "アップデートのため再起動しています。再起動が完了したら自動で再接続されます。"
  else:
    text = f"{bot_name}は再起動しています。再起動が完了したら自動で再接続されます。"
  return make_embed("再起動します", text, embed_type=EmbedType.WARNING)


def reconnect_embed(voice_channel, text_channel):
  embed = make_embed(
    "再接続しました",
    "再起動が完了したため、ボイスチャンネルに再接続しました",
    embed_type=EmbedType.SUCCESS,
  )
  embed.add_field(
    name="接続情報",
    value=f"接続チャンネル：{voice_channel.mention}　読み上げチャンネル：{text_channel.mention}",
    inline=False,
  )
  return embed


class ReconnectService:
  def __init__(
      self,
      client,
      state_dir: str,
      connection_service,
      voice_service,
      error_notifier=None,
      *,
      interval: float = SAVE_INTERVAL,
      max_downtime: float = MAX_DOWNTIME,
      clock: Callable[[], float] = time.time,
  ):
    self.client = client
    self.state_dir = state_dir or ""
    self.connection_service = connection_service
    self.voice_service = voice_service
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.interval = interval
    self.max_downtime = max_downtime
    self.clock = clock
    self.shutting_down = False
    # 起動したときのつなぎ直しが終わるまでは、書き残さない（前回の記録を消さないため）
    self._restored = False
    self._task: asyncio.Task | None = None

  @property
  def enabled(self) -> bool:
    return bool(self.state_dir)

  # ファイル

  def _path(self, name: str) -> str:
    return os.path.join(self.state_dir, name)

  def _read(self, name: str) -> dict | None:
    try:
      with open(self._path(name), encoding="utf-8") as file:
        data = json.load(file)
    except (OSError, ValueError):
      return None
    return data if isinstance(data, dict) else None

  def _remove(self, name: str) -> None:
    try:
      os.remove(self._path(name))
    except FileNotFoundError:
      pass
    except OSError as error:
      print(f"{name} を消せませんでした: {error}")

  def read_reason(self) -> str | None:
    return (self._read(RESTART_REASON_FILE) or {}).get("reason")

  # 書き残す

  def snapshot(self) -> list[dict]:
    sessions = []
    for voice_client in self.client.voice_clients:
      if not voice_client.is_connected():
        continue
      guild = voice_client.guild
      sessions.append({
        "guild_id": guild.id,
        "voice_channel_id": voice_client.channel.id,
        "text_channel_id": self.voice_service.get_session(guild.id).temporary_text_channel_id,
      })
    return sessions

  def save(self) -> None:
    """今つないでいるVCを書き残す。止めている途中と、つなぎ直す前は書かない。"""
    if not self.enabled or self.shutting_down or not self._restored:
      return
    self._write({"alive_at": self.clock(), "sessions": self.snapshot()})

  def _write(self, data: dict) -> None:
    temp = None
    try:
      os.makedirs(self.state_dir, exist_ok=True)
      fd, temp = tempfile.mkstemp(dir=self.state_dir, prefix=".tmp-voice-", suffix=".json")
      with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(data, file)
        file.write("\n")
      os.replace(temp, self._path(SESSIONS_FILE))
      temp = None
    except OSError as error:
      print(f"{SESSIONS_FILE} を書けませんでした: {error}")
    finally:
      if temp is not None:
        try:
          os.remove(temp)
        except OSError:
          pass

  def start(self) -> None:
    """1分おきの書き残しを始める。"""
    if not self.enabled or (self._task is not None and not self._task.done()):
      return
    self._task = self.error_notifier.create_task(self._run(), "voice session save")

  async def _run(self) -> None:
    while True:
      self.save()
      await asyncio.sleep(self.interval)

  # 起動したとき

  async def restore(self) -> int:
    """前回つないでいたVCにつなぎ直し、つなぎ直した数を返す。"""
    if not self.enabled:
      return 0
    reason = self.read_reason()
    data = self._read(SESSIONS_FILE)
    self._remove(RESTART_REASON_FILE)
    self._restored = True
    restored = 0
    try:
      if reason == REASON_MANUAL_STOP or data is None:
        return 0
      alive_at = data.get("alive_at")
      if not isinstance(alive_at, (int, float)) or self.clock() - alive_at > self.max_downtime:
        return 0
      for session in data.get("sessions") or []:
        try:
          if await self._restore_one(session):
            restored += 1
        except Exception as error:
          self.error_notifier.report_exception(error, "voice reconnect", {"guild_id": session.get("guild_id")})
      return restored
    finally:
      self.save()

  async def _restore_one(self, session: dict) -> bool:
    guild = self.client.get_guild(session.get("guild_id"))
    if guild is None:
      return False
    voice_channel = guild.get_channel(session.get("voice_channel_id"))
    if voice_channel is None:
      return False
    if not any(not member.bot for member in voice_channel.members):
      return False
    if guild.voice_client is not None and guild.voice_client.is_connected():
      return False
    await self.connection_service.connect_voice(voice_channel)
    text_channel_id = session.get("text_channel_id")
    if text_channel_id is not None and guild.get_channel(text_channel_id) is not None:
      self.voice_service.get_session(guild.id).temporary_text_channel_id = text_channel_id
    notify_channel = self.connection_service.get_notify_channel(guild, voice_channel)
    if notify_channel is not None:
      await notify_channel.send(embed=reconnect_embed(voice_channel, notify_channel))
    return True

  # 止めるとき

  async def shutdown(self) -> None:
    """読み上げチャンネルに知らせてから、すべてのVCを抜ける。"""
    if self.shutting_down:
      return
    reason = self.read_reason() if self.enabled else None
    if self.enabled:
      if reason == REASON_MANUAL_STOP:
        self._remove(SESSIONS_FILE)
      else:
        # 抜ける前の状態を、止めた時刻で書き残す
        self.save()
    self.shutting_down = True
    if self._task is not None and not self._task.done():
      self._task.cancel()
    voice_clients = [vc for vc in self.client.voice_clients if vc.is_connected()]
    if not voice_clients:
      return
    try:
      await asyncio.wait_for(
        asyncio.gather(*(self._leave(vc, reason) for vc in voice_clients), return_exceptions=True),
        SHUTDOWN_TIMEOUT,
      )
    except asyncio.TimeoutError:
      print("VCから抜ける処理が時間内に終わりませんでした")

  async def _leave(self, voice_client, reason: str | None) -> None:
    guild = voice_client.guild
    if reason != REASON_MANUAL_STOP:
      notify_channel = self.connection_service.get_notify_channel(guild, voice_client.channel)
      if notify_channel is not None:
        try:
          await notify_channel.send(embed=restart_embed(reason, guild.me.display_name))
        except Exception as error:
          print(f"再起動の知らせを送れませんでした: {type(error).__name__}: {error}")
    # 止めるときの切断は、強制切断として扱わない（一時的な読み上げチャンネルを退避しない）
    self.connection_service.voluntary_disconnects.add(guild.id)
    await voice_client.disconnect(force=True)

  async def close(self) -> None:
    if self._task is not None and not self._task.done():
      self._task.cancel()
      await asyncio.gather(self._task, return_exceptions=True)
