"""Discordステータスのテンプレート展開と動的更新を担当する。"""

import asyncio
import string

import discord

from services.error_notification_service import ensure_error_notifier


class StatusTemplateError(ValueError):
  """STATUS_MESSAGEのテンプレートが不正な場合に送出する。"""


class StatusService:
  ALLOWED_FIELDS = frozenset({
    "voice_connections",
    "voice_users",
    "guilds",
  })

  def __init__(
      self,
      client,
      template: str,
      error_notifier=None,
      *,
      debounce_seconds: float = 1.0,
  ):
    self.client = client
    self.template = template
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.debounce_seconds = debounce_seconds
    self._formatter = string.Formatter()
    self._update_task: asyncio.Task | None = None
    self._last_message: str | None = None
    self._validate_template()

  def _validate_template(self) -> None:
    try:
      parsed = self._formatter.parse(self.template)
      for _, field_name, format_spec, conversion in parsed:
        if field_name is None:
          continue
        if field_name not in self.ALLOWED_FIELDS:
          allowed = ", ".join(sorted(self.ALLOWED_FIELDS))
          raise StatusTemplateError(
            f"STATUS_MESSAGEに未知の変数 {{{field_name}}} があります。"
            f"利用可能な変数: {allowed}"
          )
        if format_spec or conversion:
          raise StatusTemplateError(
            f"STATUS_MESSAGEの変数 {{{field_name}}} では"
            "書式指定と変換指定を利用できません。"
          )
    except ValueError as error:
      if isinstance(error, StatusTemplateError):
        raise
      raise StatusTemplateError(
        f"STATUS_MESSAGEのテンプレート構文が不正です: {error}"
      ) from error

  def values(self) -> dict[str, int]:
    voice_clients = [
      voice_client
      for voice_client in self.client.voice_clients
      if voice_client.is_connected()
    ]
    voice_users = sum(
      1
      for voice_client in voice_clients
      for member in voice_client.channel.members
      if not member.bot
    )
    return {
      "voice_connections": len(voice_clients),
      "voice_users": voice_users,
      "guilds": len(self.client.guilds),
    }

  def render(self) -> str:
    return self.template.format(**self.values())

  async def update(self) -> None:
    """現在値を反映する。APIエラーは通知し、呼び出し元へ伝播しない。"""
    message = self.render()
    if message == self._last_message:
      return
    try:
      await self.client.change_presence(
        status=discord.Status.online,
        activity=discord.Game(name=message),
      )
    except Exception as error:
      self.error_notifier.report_exception(error, "status update")
      return
    self._last_message = message

  def schedule_update(self) -> None:
    """連続イベントをまとめてステータス更新を予約する。"""
    if self._update_task is not None and not self._update_task.done():
      self._update_task.cancel()
    self._update_task = asyncio.create_task(self._delayed_update())

  async def _delayed_update(self) -> None:
    try:
      await asyncio.sleep(self.debounce_seconds)
      await self.update()
    except asyncio.CancelledError:
      return

  async def close(self) -> None:
    if self._update_task is None or self._update_task.done():
      return
    self._update_task.cancel()
    await asyncio.gather(self._update_task, return_exceptions=True)

