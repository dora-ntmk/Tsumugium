"""コマンド実行時の共通エラー応答。"""

from presentation.embeds import EmbedType, make_embed


async def handle_os_error(ctx, error: OSError, command_name: str) -> None:
  print(f"OSError in {command_name}: {error}")
  try:
    await ctx.edit_original_response(
      embed=make_embed(
        "システムエラー",
        "設定の読み書きに失敗しました",
        embed_type=EmbedType.ERROR,
      )
    )
  except Exception as inner:
    print(f"Failed to send OSError embed in {command_name}: {inner}")


async def handle_internal_error(ctx, error: Exception, command_name: str) -> None:
  print(f"Exception in {command_name}: {error}")
  try:
    await ctx.edit_original_response(
      embed=make_embed(
        "内部エラーが発生しました",
        type(error).__name__,
        embed_type=EmbedType.ERROR,
      )
    )
  except Exception as inner:
    print(f"Failed to send internal error embed in {command_name}: {inner}")
