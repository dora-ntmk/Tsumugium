"""Discordボイスチャンネルへの接続・切断と入退室処理を担当する。"""

import asyncio

import aiohttp
import discord

from models.audio_item import TTSItem
from presentation.embeds import EmbedType, make_embed
from presentation.announcement_layout import add_announcements
from presentation.layouts import LayoutMessage, make_layout
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
      user_reading_service=None,
      announcement_service=None,
  ):
    self.server_config = server_config
    self.dict_manager = dict_manager
    self.speech_service = speech_service
    self.voice_service = voice_service
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.user_reading_service = user_reading_service
    self.announcement_service = announcement_service
    self.voluntary_disconnects: set[int] = set()
    self._connection_locks: dict[int, asyncio.Lock] = {}
    self._voice_state_stages: dict[int, str] = {}

  def voice_state_error_context(self, member, before, after):
    context = self.voice_service.connection_context(member.guild)
    context.update({
      "member_id": member.id,
      "before_channel_id": getattr(before.channel, "id", None),
      "after_channel_id": getattr(after.channel, "id", None),
      "stage": self._voice_state_stages.get(member.guild.id, "unknown"),
    })
    return context

  @staticmethod
  def _is_transient_connection_error(error: BaseException) -> bool:
    if isinstance(error, discord.HTTPException):
      return error.status >= 500
    return isinstance(
      error,
      (
        asyncio.TimeoutError,
        aiohttp.ServerDisconnectedError,
        aiohttp.ClientConnectionError,
        ConnectionError,
      ),
    )

  async def _connect_voice(
      self,
      voice_channel,
      *,
      retry: bool = False,
      retry_validator=None,
  ):
    """ギルド単位で接続を直列化し、AutoJoinだけ一時障害を再試行する。"""
    guild = voice_channel.guild
    lock = self._connection_locks.setdefault(guild.id, asyncio.Lock())
    attempts = 3 if retry else 1
    async with lock:
      for attempt in range(attempts):
        if retry_validator is not None and not retry_validator():
          return None
        if guild.voice_client is not None:
          if guild.voice_client.is_connected():
            return guild.voice_client
          try:
            await guild.voice_client.disconnect(force=True)
          except Exception:
            pass
        try:
          return await voice_channel.connect(timeout=60)
        except Exception as error:
          last_attempt = attempt + 1 >= attempts
          if last_attempt or not self._is_transient_connection_error(error):
            raise
          await asyncio.sleep(2 ** attempt)
    return None

  @staticmethod
  def _has_humans(channel) -> bool:
    return any(not m.bot for m in getattr(channel, "members", []))

  async def connect_voice(self, voice_channel):
    """VCにつなぐ（再起動のあとのつなぎ直しで使う）。"""
    return await self._connect_voice(voice_channel, retry=True)

  def get_notify_channel(self, guild, voice_channel=None):
    session = self.voice_service.get_session(guild.id)
    text_target = session.temporary_text_channel_id
    if text_target is None:
      text_target = self.server_config.get(guild.id, "TextTarget")
    if text_target:
      return guild.get_channel(text_target)
    return voice_channel

  def connected_message(
      self,
      title: str,
      voice_channel,
      text_channel,
      layout_type: EmbedType = EmbedType.SUCCESS,
  ) -> LayoutMessage:
    """接続したときのメッセージ（Components v2）。運営者からのお知らせを下に載せる。"""
    announcements, hidden_count, now = [], 0, None
    if self.announcement_service is not None:
      try:
        announcements, hidden_count = self.announcement_service.display()
        now = self.announcement_service.now()
      except Exception as error:
        # お知らせを読めなくても、接続メッセージは出す
        self.error_notifier.report_exception(error, "announcement display")
        announcements, hidden_count = [], 0
    # ファイルを添付できないチャンネルでは、画像を省いて送る
    include_images = True
    guild = getattr(voice_channel, "guild", None)
    if guild is not None and text_channel is not None and hasattr(text_channel, "permissions_for"):
      include_images = bool(
        getattr(text_channel.permissions_for(guild.me), "attach_files", True)
      )
    layout = make_layout(
      title,
      f"{voice_channel.mention}に接続しました。",
      layout_type=layout_type,
    )
    return add_announcements(
      layout,
      announcements,
      hidden_count=hidden_count,
      include_images=include_images,
      now=now,
    )

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
    try:
      voice_client = guild.voice_client
      if voice_client is None:
        self.voluntary_disconnects.discard(guild.id)
        return
      await voice_client.disconnect()
    except Exception:
      self.voluntary_disconnects.discard(guild.id)
      raise

  async def enqueue_notice(self, guild, member, joined: bool) -> None:
    action = "入室" if joined else "退室"
    member_reading = member.display_name
    if self.user_reading_service is not None:
      member_reading = self.user_reading_service.get_reading(member)
    notice_text = f"{member_reading}さんが{action}しました"
    preprocess_result = self.dict_manager.preprocess_text(
      notice_text,
      guild.id,
      guild,
      [],
    )
    notice_text = preprocess_result.text
    speaker = self.server_config.get(guild.id, "Speaker")
    volume = self.server_config.volume_to_vvtts(guild.id)
    speed = self.server_config.speed_to_vvtts(guild.id)
    audio = await self.speech_service.generate(
      notice_text,
      guild.id,
      member.id,
      speaker,
      speed=speed,
      volume=volume,
    )
    if audio is not None:
      await self.voice_service.enqueue(guild, TTSItem(audio.path, audio=audio))

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

      await self._connect_voice(voice_channel)
      self.voice_service.get_session(
        message.guild.id
      ).temporary_text_channel_id = message.channel.id
      layout = self.connected_message("接続しました", voice_channel, message.channel)
      await message.channel.send(view=layout.view, files=layout.files)
    except Exception as e:
      self.error_notifier.report_exception(
        e,
        "mention join/leave",
        self.voice_service.connection_context(message.guild),
      )
      try:
        await message.channel.send(
          embed=make_embed(
            "接続失敗",
            "Discordとの音声接続に失敗しました。時間をおいて再試行してください。",
            embed_type=EmbedType.ERROR,
          )
        )
      except Exception as response_error:
        self.error_notifier.report_exception(
          response_error,
          "mention join/leave error response",
          self.voice_service.connection_context(message.guild),
        )

  async def handle_voice_state_update(self, member, before, after) -> None:
    guild = member.guild
    session = self.voice_service.get_session(guild.id)
    self._voice_state_stages[guild.id] = "classify_event"

    if member == guild.me:
      if before.channel is None and after.channel is not None:
        session.auto_join_suppressed_channel_id = None
        saved = session.pending_text_channel_id
        session.pending_text_channel_id = None
        if saved is not None:
          session.temporary_text_channel_id = saved
        self.voice_service.start_keepalive(guild)
        self.voice_service.reset_speaking_soon(guild)
      elif before.channel is not None and after.channel is None:
        self._voice_state_stages[guild.id] = "bot_disconnect_cleanup"
        # 人が残っているVCから抜けた（/leave・メンション・Discord上での切断など）なら、
        # そのVCから人がいなくなるまで、誰かが来てもAutoJoinでつなぎ直さない
        if self._has_humans(before.channel):
          session.auto_join_suppressed_channel_id = before.channel.id
        if guild.id in self.voluntary_disconnects:
          self.voluntary_disconnects.discard(guild.id)
          session.temporary_text_channel_id = None
        else:
          saved = session.temporary_text_channel_id
          session.temporary_text_channel_id = None
          if saved is not None:
            session.pending_text_channel_id = saved
        self.voice_service.stop_keepalive(guild.id)
        await self.voice_service.discard_queue(guild.id)
      return

    user_joined = before.channel is None and after.channel is not None
    user_left = before.channel is not None and after.channel != before.channel
    if (
        user_left
        and session.auto_join_suppressed_channel_id == before.channel.id
        and not self._has_humans(before.channel)
    ):
      session.auto_join_suppressed_channel_id = None
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
            self._voice_state_stages[guild.id] = "auto_leave_notification"
            await notify_channel.send(
              embed=make_embed(
                "自動退出",
                "ボイスチャンネルに誰もいなくなったため退出しました",
              )
            )
          return
        if self.server_config.get(guild.id, "AccessNotice"):
          self._voice_state_stages[guild.id] = "access_notice_leave"
          await self.enqueue_notice(guild, member, joined=False)

    if not user_joined:
      return

    voice_target = self.server_config.get(guild.id, "VoiceTarget")
    human_count = len([m for m in after.channel.members if not m.bot])
    auto_join = self.server_config.get(guild.id, "AutoJoin")
    if (
        auto_join
        and not member.bot
        and guild.voice_client is None
        and session.auto_join_suppressed_channel_id != voice_target
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
        def auto_join_still_valid():
          configured_target = self.server_config.get(guild.id, "VoiceTarget")
          humans = [m for m in target_channel.members if not m.bot]
          return (
            self.server_config.get(guild.id, "AutoJoin")
            and configured_target == target_channel.id
            and bool(humans)
            and session.auto_join_suppressed_channel_id != target_channel.id
            and (
              guild.voice_client is None
              or not guild.voice_client.is_connected()
            )
          )

        await asyncio.sleep(1)
        self._voice_state_stages[guild.id] = "auto_join_connect"
        connected = await self._connect_voice(
          target_channel,
          retry=True,
          retry_validator=auto_join_still_valid,
        )
        if connected is None:
          return
        if notify_channel:
          self._voice_state_stages[guild.id] = "auto_join_notification"
          layout = self.connected_message(
            "自動で接続しました",
            target_channel,
            notify_channel,
            layout_type=EmbedType.INFO,
          )
          await notify_channel.send(view=layout.view, files=layout.files)
        return

    if (
        self.server_config.get(guild.id, "AccessNotice")
        and guild.voice_client is not None
        and after.channel == guild.voice_client.channel
    ):
      self._voice_state_stages[guild.id] = "access_notice_join"
      await self.enqueue_notice(guild, member, joined=True)

  async def join(self, ctx) -> None:
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
      await self._connect_voice(voice_channel)
      self.voice_service.get_session(
        ctx.guild.id
      ).temporary_text_channel_id = text_channel.id
      layout = self.connected_message("接続しました", voice_channel, text_channel)
      await ctx.edit_original_response(view=layout.view, attachments=layout.files)
    except discord.errors.InteractionResponded:
      return
    except discord.errors.HTTPException as e:
      await handle_internal_error(ctx, e, "join HTTP", self.error_notifier)
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
