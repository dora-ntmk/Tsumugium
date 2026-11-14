"""Discordメッセージを読み上げ対象として扱うか判定する。"""

import re

import discord

from services.error_notification_service import ensure_error_notifier


# `!t おはよう`・`!tts おはよう`: 読み上げて、その音声ファイルを送る
TTS_FILE_PREFIX_RE = re.compile(r"!(?:tts|t)\s+(?P<text>\S.*)", re.DOTALL)


def parse_tts_file_command(content: str) -> str | None:
  """`!t`・`!tts`のあとの文章を返す。当てはまらなければNoneを返す。"""
  match = TTS_FILE_PREFIX_RE.fullmatch(content.strip())
  if match is None:
    return None
  return match.group("text").strip()


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
    if message.content.startswith(("!s ", "!skip ")) or message.flags.silent:
      return
    content = message.content.strip()
    if content == "s":
      self.voice_service.skip_current(message.guild)
      return
    if content == "c":
      self.error_notifier.create_task(
        self.voice_service.clear(message.guild, instant=True),
        "message clear",
      )
      return
    tts_text = parse_tts_file_command(content)
    if tts_text is not None:
      self.error_notifier.create_task(
        self.speak_and_send_file(message, tts_text),
        "message tts file",
      )
      return
    self.error_notifier.create_task(
      self.speech_service.add_message(message),
      "message speech",
    )

  async def speak_and_send_file(self, message, text: str) -> None:
    """`!t`の文章を読み上げ、その音声ファイルをメッセージへの返信で送る。"""
    tts_file = await self.speech_service.speak_and_export(
      message.guild,
      text,
      message.id,
      author_id=message.author.id,
      mentions=message.mentions,
      attachments=(),
      created_at=message.created_at,
      message_id=message.id,
    )
    if tts_file is None:
      return
    try:
      await message.reply(
        file=tts_file.to_discord_file(),
        mention_author=False,
      )
    except discord.HTTPException as error:
      if getattr(error, "status", None) == 413:
        await message.channel.send(
          "音声ファイルが大きすぎて送れませんでした。文章を短くしてください。"
        )
        return
      # 返信先が消えたときなどは、返信にせずに送る
      await message.channel.send(file=tts_file.to_discord_file())
