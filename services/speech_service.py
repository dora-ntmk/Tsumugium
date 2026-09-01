"""メッセージの前処理、音声種別の選択、TTS生成を担当する。"""

import discord

from models.audio_item import SoundboardItem, TTSItem


class SpeechService:
  def __init__(
      self,
      vvtts,
      server_config,
      dict_manager,
      voice_service,
      user_reading_service=None,
  ):
    self.vvtts = vvtts
    self.server_config = server_config
    self.dict_manager = dict_manager
    self.voice_service = voice_service
    self.user_reading_service = user_reading_service

  async def add_message(self, message, *, sounddict_only: bool = False) -> None:
    guild_id = message.guild.id
    session = self.voice_service.get_session(guild_id)
    if session.clearing:
      return
    speaker = self.server_config.get(guild_id, "Speaker")
    volume = self.server_config.volume_to_vvtts(guild_id)
    reference = getattr(message, "reference", None)
    if reference is not None and reference.type is discord.MessageReferenceType.forward:
      text = "転送済みメッセージ"
    else:
      text = message.content
    if message.stickers:
      for i in range(len(message.stickers)):
        text += f" {message.stickers[i].name}"
    replaced_ranges = []
    spaced_out = False
    if self.dict_manager is not None:
      user_readings = {}
      if self.user_reading_service is not None:
        for user in message.mentions:
          user_readings[str(user.id)] = (
            self.user_reading_service.get_reading(user)
          )
      result = self.dict_manager.preprocess_text(
        text,
        guild_id,
        message.guild,
        message.attachments,
        message.mentions,
        author_id=message.author.id,
        user_readings=user_readings,
      )
      text = result.text
      replaced_ranges = result.replaced_ranges
      spaced_out = result.spaced_out
      if result.sound_id is not None:
        await self.voice_service.enqueue(
          message.guild, SoundboardItem(result.sound_id)
        )
        return
    if sounddict_only:
      return
    max_char = self.server_config.get(guild_id, "MaxChar")
    if 0 < max_char < len(text):
      cut = max_char
      for start, end in replaced_ranges:
        if start < cut < end:
          cut = end
          break
      text = text[:cut] + ",以下省略"
    speed = self.server_config.speed_to_vvtts(
      guild_id,
      spaced_out=spaced_out,
    )
    path = await self.generate(
      text,
      guild_id,
      message.id,
      speaker,
      speed=speed,
      volume=volume,
    )
    if path is not None:
      await self.voice_service.enqueue(message.guild, TTSItem(path))

  async def generate(self, message, guild_id, message_id, speaker, speed=1.0, volume=1.0):
    return await self.vvtts.generate(
      message,
      guild_id,
      message_id,
      speaker,
      speed=speed,
      volume=volume,
    )
