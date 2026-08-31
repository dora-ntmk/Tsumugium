"""ユーザー単位設定のコマンド登録層。"""

import discord

from presentation.embeds import EmbedType, make_embed
from presentation.error_handler import handle_internal_error, handle_os_error
from services.error_notification_service import ensure_error_notifier


class UserCog:
  def __init__(self, tree, user_config, error_notifier=None):
    self.tree = tree
    self.user_config = user_config
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._register()

  def _register(self) -> None:
    @self.tree.command(
      name="user-reading",
      description="あなたの名前の読み方を設定します",
    )
    @discord.app_commands.allowed_contexts(
      guilds=True,
      dms=True,
      private_channels=True,
    )
    @discord.app_commands.allowed_installs(guilds=True, users=True)
    @discord.app_commands.describe(reading="あなたの名前の読み方（50文字以内）")
    async def user_reading(ctx, reading: str):
      try:
        await ctx.response.defer(ephemeral=True)
        try:
          self.user_config.set_user_reading(ctx.user.id, reading)
        except ValueError as error:
          await ctx.edit_original_response(
            embed=make_embed(
              "設定失敗",
              str(error),
              embed_type=EmbedType.ERROR,
            )
          )
          return
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"あなたの読み方を「{reading}」に設定しました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as error:
        self.error_notifier.report_exception(error, "user-reading HTTP")
      except OSError as error:
        await handle_os_error(
          ctx,
          error,
          "user-reading",
          self.error_notifier,
        )
      except Exception as error:
        await handle_internal_error(
          ctx,
          error,
          "user-reading",
          self.error_notifier,
        )
