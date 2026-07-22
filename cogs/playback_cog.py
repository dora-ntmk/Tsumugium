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

  def _register(self) -> None:
    @self.tree.command(
      name="clear",
      description="読み上げキューをすべてクリアします。",
    )
    async def clear(ctx, instant: bool = True):
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
        self.error_notifier.report(f"HTTPException in clear: {e}")
      except Exception as e:
        self.error_notifier.report(f"Exception in clear: {e}")

    @self.client.event
    async def on_message(message):
      await self.message_service.handle(message)
