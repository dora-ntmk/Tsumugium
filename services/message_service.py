"""Discordメッセージを読み上げ対象として扱うか判定する。"""

from services.error_notification_service import ensure_error_notifier


class MessageService:
  def __init__(
      self,
      client,
      server_config,
      speech_service,
      voice_service,
      connection_service=None,
      error_notifier=None,
  ):
    self.client = client
    self.server_config = server_config
    self.speech_service = speech_service
    self.voice_service = voice_service
    self.connection_service = connection_service
    self.error_notifier = ensure_error_notifier(error_notifier)

  def is_target_channel(self, message) -> bool:
    session = self.voice_service.get_session(message.guild.id)
    text_target = session.temporary_text_channel_id
    if text_target is None:
      text_target = self.server_config.get(message.guild.id, "TextTarget")
    voice_channel_id = message.guild.voice_client.channel.id
    if text_target is None:
      return message.channel.id == voice_channel_id
    return message.channel.id in (text_target, voice_channel_id)

  async def handle(self, message) -> None:
    if message.author.bot:
      if message.guild is None or message.guild.voice_client is None:
        return
      if not self.is_target_channel(message):
        return
      self.error_notifier.create_task(
        self.speech_service.add_message(message, sounddict_only=True),
        "bot message speech",
      )
      return

    if message.guild is not None:
      bot_id = self.client.user.id
      if message.content.strip() in (f'<@{bot_id}>', f'<@!{bot_id}>'):
        if self.connection_service is not None:
          await self.connection_service.handle_mention_toggle(message)
        return

    if message.guild is None or message.guild.voice_client is None:
      return
    if not self.is_target_channel(message):
      return
    if message.content.startswith("!s ") or message.flags.silent:
      return
    if message.content.strip() == "s":
      self.voice_service.skip_current(message.guild)
      return
    self.error_notifier.create_task(
      self.speech_service.add_message(message),
      "message speech",
    )
