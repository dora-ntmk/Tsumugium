"""Discordメッセージを読み上げ対象として扱うか判定する。"""

import asyncio

from presentation.embeds import EmbedType, make_embed


class MessageService:
  def __init__(
      self,
      client,
      server_config,
      speech_service,
      voice_service,
      leaving_guilds=None,
  ):
    self.client = client
    self.server_config = server_config
    self.speech_service = speech_service
    self.voice_service = voice_service
    self.leaving_guilds = leaving_guilds if leaving_guilds is not None else set()

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
      asyncio.create_task(
        self.speech_service.add_message(message, sounddict_only=True)
      )
      return

    if message.guild is not None:
      bot_id = self.client.user.id
      if message.content.strip() in (f'<@{bot_id}>', f'<@!{bot_id}>'):
        await self._handle_mention_toggle(message)
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
    asyncio.create_task(self.speech_service.add_message(message))

  async def _handle_mention_toggle(self, message) -> None:
    try:
      if message.guild.voice_client is not None:
        if message.author.voice:
          self.leaving_guilds.add(message.guild.id)
          await message.guild.voice_client.disconnect()
          await message.channel.send(
            embed=make_embed(
              "切断完了",
              "ボイスチャンネルから切断しました",
              embed_type=EmbedType.SUCCESS,
            )
          )
        else:
          await message.channel.send(
            embed=make_embed(
              "切断失敗",
              "ボイスチャンネルから切断できませんでした",
              embed_type=EmbedType.ERROR,
            )
          )
        return

      if not message.author.voice:
        await message.channel.send(
          embed=make_embed(
            "接続失敗",
            "ボイスチャンネルに接続できませんでした",
            embed_type=EmbedType.ERROR,
          )
        )
        return

      voice_channel = message.author.voice.channel
      bot_member = message.guild.me
      voice_permissions = voice_channel.permissions_for(bot_member)
      text_permissions = message.channel.permissions_for(bot_member)
      issues = []
      if not (voice_permissions.connect and voice_permissions.speak):
        issues.append(f"{voice_channel.mention} への接続権限がありません")
      if not (text_permissions.view_channel and text_permissions.send_messages):
        issues.append(f"{message.channel.mention} の表示権限がありません")
      if issues:
        await message.channel.send(
          embed=make_embed(
            "権限エラー",
            "\n".join(issues),
            embed_type=EmbedType.ERROR,
          )
        )
        return

      await voice_channel.connect(timeout=60)
      self.voice_service.get_session(
        message.guild.id
      ).temporary_text_channel_id = message.channel.id
      embed = make_embed(
        "接続完了",
        f"ボイスチャンネルに接続しました。\n今回の通話に限り {message.channel.mention} のメッセージも読み上げます。",
        embed_type=EmbedType.SUCCESS,
      )
      embed.add_field(
        name="接続情報",
        value=f"接続チャンネル：{voice_channel.mention}　読み上げチャンネル：{message.channel.mention}",
        inline=False,
      )
      await message.channel.send(embed=embed)
    except Exception as e:
      print(f"Exception in mention join/leave: {e}")
