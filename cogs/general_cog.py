"""一般コマンドを登録する。"""

import discord

from presentation.embeds import make_embed
from presentation.error_handler import handle_internal_error
from services.error_notification_service import ensure_error_notifier


class GeneralCog:
  def __init__(self, tree, version: str, last_updated: str, error_notifier=None):
    self.tree = tree
    self.version = version
    self.last_updated = last_updated
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._register()

  def _register(self) -> None:
    @self.tree.command(
      name="version",
      description="バージョン情報を表示します",
    )
    async def version_cmd(ctx):
      try:
        await ctx.response.defer()
        embed = make_embed("バージョン情報")
        embed.add_field(
          name="Tsumugiumバージョン",
          value=self.version,
          inline=False,
        )
        embed.add_field(
          name="Tsumugium最終更新日",
          value=self.last_updated,
          inline=False,
        )
        await ctx.edit_original_response(embed=embed)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in version: {e}")
      except Exception as e:
        await handle_internal_error(ctx, e, "version", self.error_notifier)
