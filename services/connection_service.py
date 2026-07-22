"""Discordボイスチャンネルへの接続・切断と入退室処理を担当する。"""

import asyncio

import discord

from models.audio_item import TTSItem
from presentation.embeds import EmbedType, make_embed
from presentation.error_handler import handle_internal_error, handle_os_error
from services.error_notification_service import ensure_error_notifier


class ConnectionService:
  def __init__(
      self,
      server_config,
      dict_manager,
      speech_service,
      voice_service,
      error_notifier=None,
  ):
    self.server_config = server_config
    self.dict_manager = dict_manager
    self.speech_service = speech_service
    self.voice_service = voice_service
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.voluntary_disconnects: set[int] = set()

  def get_notify_channel(self, guild, voice_channel=None):
    session = self.voice_service.get_session(guild.id)
    text_target = session.temporary_text_channel_id
    if text_target is None:
      text_target = self.server_config.get(guild.id, "TextTarget")
    if text_target:
      return guild.get_channel(text_target)
    return voice_channel

  def permission_issues(self, guild, voice_channel, text_channel) -> list[str]:
    bot_member = guild.me
    voice_permissions = voice_channel.permissions_for(bot_member)
    text_permissions = text_channel.permissions_for(bot_member)
    issues = []
    if not (voice_permissions.connect and voice_permissions.speak):
      issues.append(f"{voice_channel.mention} への接続権限がありません")
    if not (text_permissions.view_channel and text_permissions.send_messages):
      issues.append(f"{text_channel.mention} の表示権限がありません")
    return issues

  async def disconnect(self, guild) -> None:
    self.voluntary_disconnects.add(guild.id)
    await guild.voice_client.disconnect()

  async def enqueue_notice(self, guild, member, joined: bool) -> None:
    action = "入室" if joined else "退室"
    notice_text = f"{member.display_name}さんが{action}しました"
    notice_text, _, _ = self.dict_manager.preprocess_text(
      notice_text,
      guild.id,
      guild,
      [],
    )
    speaker = self.server_config.get(guild.id, "Speaker")
    volume = self.server_config.volume_to_vvtts(guild.id)
    speed = self.server_config.speed_to_vvtts(guild.id)
    src = await self.speech_service.generate(
      notice_text,
      guild.id,
      member.id,
      speaker,
      speed=speed,
      volume=volume,
    )
    if src is not None:
      await self.voice_service.enqueue(guild, TTSItem(src))

  async def handle_mention_toggle(self, message) -> None:
    try:
      if message.guild.voice_client is not None:
        if message.author.voice:
          await self.disconnect(message.guild)
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
      issues = self.permission_issues(
        message.guild,
        voice_channel,
        message.channel,
      )
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
      self.error_notifier.report(f"Exception in mention join/leave: {e}")

  async def handle_voice_state_update(self, member, before, after) -> None:
    guild = member.guild
    session = self.voice_service.get_session(guild.id)

    if member == guild.me:
      if before.channel is None and after.channel is not None:
        saved = session.pending_text_channel_id
        session.pending_text_channel_id = None
        if saved is not None:
          session.temporary_text_channel_id = saved
        self.voice_service.start_keepalive(guild)
      elif before.channel is not None and after.channel is None:
        if guild.id in self.voluntary_disconnects:
          self.voluntary_disconnects.discard(guild.id)
          session.temporary_text_channel_id = None
        else:
          saved = session.temporary_text_channel_id
          session.temporary_text_channel_id = None
          if saved is not None:
            session.pending_text_channel_id = saved
        self.voice_service.stop_keepalive(guild.id)
      return

    user_joined = before.channel is None and after.channel is not None
    user_left = before.channel is not None and after.channel != before.channel
    if user_left and guild.voice_client is not None:
      bot_channel = guild.voice_client.channel
      if before.channel == bot_channel:
        human_members = [m for m in bot_channel.members if not m.bot]
        if len(human_members) == 0:
          voice_target = self.server_config.get(guild.id, "VoiceTarget")
          if voice_target is not None and bot_channel.id == voice_target:
            notify_channel = self.get_notify_channel(guild, bot_channel)
          else:
            temp_channel_id = session.temporary_text_channel_id
            notify_channel = (
              guild.get_channel(temp_channel_id)
              if temp_channel_id
              else bot_channel
            )
          await asyncio.sleep(0.5)
          if guild.voice_client is None:
            return
          await self.disconnect(guild)
          if notify_channel:
            await notify_channel.send(
              embed=make_embed(
                "自動退出",
                "ボイスチャンネルに誰もいなくなったため退出しました",
              )
            )
          return
        if self.server_config.get(guild.id, "AccessNotice"):
          await self.enqueue_notice(guild, member, joined=False)

    if not user_joined:
      return

    voice_target = self.server_config.get(guild.id, "VoiceTarget")
    human_count = len([m for m in after.channel.members if not m.bot])
    auto_join = self.server_config.get(guild.id, "AutoJoin")
    if (
        auto_join
        and guild.voice_client is None
        and voice_target is not None
        and after.channel.id == voice_target
        and human_count == 1
    ):
      target_channel = guild.get_channel(voice_target)
      if target_channel is not None:
        notify_channel = self.get_notify_channel(guild, target_channel)
        bot_member = guild.me
        voice_permissions = target_channel.permissions_for(bot_member)
        voice_ok = voice_permissions.connect and voice_permissions.speak
        text_ok = True
        if notify_channel is not None and notify_channel != target_channel:
          text_permissions = notify_channel.permissions_for(bot_member)
          text_ok = (
            text_permissions.view_channel and text_permissions.send_messages
          )
        issues = []
        if not voice_ok:
          issues.append(f"{target_channel.mention} への接続権限がありません")
        if not text_ok:
          issues.append(f"{notify_channel.mention} の表示権限がありません")
        if issues:
          if not text_ok:
            await member.send(
              "あなたが接続したテキストチャンネルに権限がないため、自動接続が失敗しました"
            )
          elif notify_channel:
            await notify_channel.send(
              embed=make_embed(
                "権限エラー",
                "\n".join(issues),
                embed_type=EmbedType.ERROR,
              )
            )
          return
        await asyncio.sleep(1)
        await target_channel.connect(timeout=60)
        if notify_channel:
          embed = make_embed(
            "自動入室",
            "ボイスチャンネルに自動で接続しました",
          )
          embed.add_field(
            name="接続情報",
            value=f"接続チャンネル：{target_channel.mention}　読み上げチャンネル：{notify_channel.mention}",
            inline=False,
          )
          await notify_channel.send(embed=embed)
        return

    if (
        self.server_config.get(guild.id, "AccessNotice")
        and guild.voice_client is not None
        and after.channel == guild.voice_client.channel
    ):
      await self.enqueue_notice(guild, member, joined=True)

  async def join(self, ctx, change_channel: bool = False) -> None:
    try:
      await ctx.response.defer()
      if not ctx.user.voice:
        await ctx.edit_original_response(
          embed=make_embed(
            "接続失敗",
            "ボイスチャンネルに接続できませんでした",
            embed_type=EmbedType.ERROR,
          )
        )
        return

      voice_channel = ctx.user.voice.channel
      text_channel = ctx.channel
      issues = self.permission_issues(ctx.guild, voice_channel, text_channel)
      if issues:
        await ctx.edit_original_response(
          embed=make_embed(
            "権限エラー",
            "\n".join(issues),
            embed_type=EmbedType.ERROR,
          )
        )
        return
      if ctx.guild.voice_client is not None:
        await self.disconnect(ctx.guild)
        await asyncio.sleep(0.5)
      await voice_channel.connect(timeout=60)
      if change_channel:
        if not ctx.user.guild_permissions.manage_guild:
          await ctx.edit_original_response(
            embed=make_embed(
              "接続完了",
              "ボイスチャンネルへの接続は完了しましたが、チャンネルの設定変更にはサーバーの管理権限が必要です。",
              embed_type=EmbedType.WARNING,
            )
          )
        else:
          try:
            self.server_config.set(ctx.guild.id, "TextTarget", ctx.channel.id)
            self.server_config.set(
              ctx.guild.id,
              "VoiceTarget",
              ctx.user.voice.channel.id,
            )
            embed = make_embed(
              "接続完了",
              f"ボイスチャンネルに接続しました。\nTextTargetは {ctx.channel.mention}、VoiceTargetは {ctx.user.voice.channel.mention} に設定されました。",
              embed_type=EmbedType.SUCCESS,
            )
            embed.add_field(
              name="接続情報",
              value=f"接続チャンネル：{ctx.user.voice.channel.mention}　読み上げチャンネル：{ctx.channel.mention}",
              inline=False,
            )
            await ctx.edit_original_response(embed=embed)
          except OSError:
            await ctx.edit_original_response(
              embed=make_embed(
                "設定失敗",
                "チャンネルの設定に失敗しました",
                embed_type=EmbedType.ERROR,
              )
            )
      else:
        self.voice_service.get_session(
          ctx.guild.id
        ).temporary_text_channel_id = ctx.channel.id
        embed = make_embed(
          "接続完了",
          f"ボイスチャンネルに接続しました。\n今回の通話に限り {ctx.channel.mention} のメッセージも読み上げます。",
          embed_type=EmbedType.SUCCESS,
        )
        embed.add_field(
          name="接続情報",
          value=f"接続チャンネル：{ctx.user.voice.channel.mention}　読み上げチャンネル：{ctx.channel.mention}",
          inline=False,
        )
        await ctx.edit_original_response(embed=embed)
    except discord.errors.InteractionResponded:
      return
    except discord.errors.HTTPException as e:
      self.error_notifier.report(f"HTTPException in join: {e}")
    except OSError as e:
      await handle_os_error(ctx, e, "join", self.error_notifier)
    except Exception as e:
      await handle_internal_error(ctx, e, "join", self.error_notifier)

  async def leave(self, ctx) -> None:
    try:
      await ctx.response.defer()
      if ctx.user.voice:
        await self.disconnect(ctx.guild)
        await ctx.edit_original_response(
          embed=make_embed(
            "切断完了",
            "ボイスチャンネルから切断しました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      else:
        await ctx.edit_original_response(
          embed=make_embed(
            "切断失敗",
            "ボイスチャンネルから切断できませんでした",
            embed_type=EmbedType.ERROR,
          )
        )
    except discord.errors.InteractionResponded:
      return
    except discord.errors.HTTPException as e:
      self.error_notifier.report(f"HTTPException in leave: {e}")
    except OSError as e:
      await handle_os_error(ctx, e, "leave", self.error_notifier)
    except Exception as e:
      await handle_internal_error(ctx, e, "leave", self.error_notifier)
