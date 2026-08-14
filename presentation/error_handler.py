"""コマンド実行時の共通エラー応答。"""

from presentation.embeds import EmbedType, make_embed
from services.error_notification_service import ensure_error_notifier


async def handle_os_error(
    ctx,
    error: OSError,
    command_name: str,
    error_notifier=None,
) -> None:
  notifier = ensure_error_notifier(error_notifier)
  notifier.report_exception(error, f"{command_name} OS operation")
  try:
    await ctx.edit_original_response(
      embed=make_embed(
        "システムエラー",
        "設定の読み書きに失敗しました",
        embed_type=EmbedType.ERROR,
      )
    )
  except Exception as inner:
    notifier.report_exception(inner, f"{command_name} OSError response")


async def handle_internal_error(
    ctx,
    error: Exception,
    command_name: str,
    error_notifier=None,
) -> None:
  notifier = ensure_error_notifier(error_notifier)
  notifier.report_exception(error, command_name)
  try:
    await ctx.edit_original_response(
      embed=make_embed(
        "内部エラーが発生しました",
        type(error).__name__,
        embed_type=EmbedType.ERROR,
      )
    )
  except Exception as inner:
    notifier.report_exception(inner, f"{command_name} internal error response")
