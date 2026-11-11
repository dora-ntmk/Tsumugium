"""読み上げコマンドとメッセージイベントをサービスへ委譲する。"""

import discord

from presentation.embeds import EmbedType, make_embed
from services.error_notification_service import ensure_error_notifier


class PlaybackCog:
  def __init__(
      self,
      client,
      tree,
      message_service,
      voice_service,
      error_notifier=None,
  ):
    self.client = client
    self.tree = tree
    self.message_service = message_service
    self.voice_service = voice_service
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._register()

  async def _run_clear(self, ctx, instant: bool, command_name: str) -> None:
    try:
      await ctx.response.defer()
      _, pending_files = self.voice_service.begin_clear(ctx.guild, instant)
      await ctx.edit_original_response(
        embed=make_embed(
          "削除中",
          "キューを削除しています　しばらくお待ちください",
        )
      )
      await self.voice_service.finish_clear(ctx.guild.id, pending_files)
      await ctx.edit_original_response(
        embed=make_embed(
          "キュークリア完了",
          "すべての読み上げをキャンセルしました",
          embed_type=EmbedType.SUCCESS,
        )
      )
    except discord.errors.InteractionResponded:
      return
    except discord.errors.HTTPException as e:
      self.error_notifier.report(f"HTTPException in {command_name}: {e}")
    except Exception as e:
      self.error_notifier.report(f"Exception in {command_name}: {e}")

  async def _run_skip(self, ctx, command_name: str) -> None:
    try:
      skipped = self.voice_service.skip_current(ctx.guild)
      if skipped:
        embed = make_embed(
          "スキップ",
          "再生中の読み上げをスキップしました",
          embed_type=EmbedType.SUCCESS,
        )
      else:
        embed = make_embed("スキップ", "再生中の読み上げはありません")
      await ctx.response.send_message(embed=embed, ephemeral=True)
    except discord.errors.InteractionResponded:
      return
    except discord.errors.HTTPException as e:
      self.error_notifier.report(f"HTTPException in {command_name}: {e}")
    except Exception as e:
      self.error_notifier.report(f"Exception in {command_name}: {e}")

  def _register(self) -> None:
    @self.tree.command(
      name="clear",
      description="読み上げキューをすべてクリアします。",
    )
    async def clear(ctx, instant: bool = True):
      await self._run_clear(ctx, instant, "clear")

    @self.tree.command(
      name="c",
      description="読み上げキューをすべてクリアします（/clear の短縮）。",
    )
    async def clear_short(ctx):
      await self._run_clear(ctx, True, "c")

    @self.tree.command(
      name="skip",
      description="再生中の読み上げをスキップします。",
    )
    async def skip(ctx):
      await self._run_skip(ctx, "skip")

    @self.tree.command(
      name="s",
      description="再生中の読み上げをスキップします（/skip の短縮）。",
    )
    async def skip_short(ctx):
      await self._run_skip(ctx, "s")

    @self.client.event
    async def on_message(message):
      await self.message_service.handle(message)

    @self.client.event
    async def on_raw_message_delete(payload):
      self._cancel_deleted_messages(payload.guild_id, [payload.message_id])

    @self.client.event
    async def on_raw_bulk_message_delete(payload):
      self._cancel_deleted_messages(payload.guild_id, payload.message_ids)

  def _cancel_deleted_messages(self, guild_id, message_ids) -> None:
    """削除されたメッセージの読み上げを停止・除外する。"""
    if guild_id is None:
      return
    guild = self.client.get_guild(guild_id)
    if guild is None:
      return
    try:
      for message_id in message_ids:
        self.voice_service.cancel_message(guild, message_id)
    except Exception as e:
      self.error_notifier.report_exception(e, "message delete cancel")
