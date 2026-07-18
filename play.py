"""
ファイル名：play.py
作者：どら
説明：音声関連のDiscordイベント・コマンド登録モジュール。
      実処理は MessageService / VoiceService へ委譲する。
依存関係：discord.py
"""

import discord

from presentation.embeds import EmbedType, make_embed


class Play:
  """Phase 6でCogへ移行するまでの薄いDiscord登録アダプター。"""

  def __init__(self, client, tree, message_service, voice_service):
    self.client = client
    self.tree = tree
    self.message_service = message_service
    self.voice_service = voice_service
    self._register()

  def _register(self):
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
        print(f"HTTPException in clear: {e}")
      except Exception as e:
        print(f"Exception in clear: {e}")

    @self.client.event
    async def on_message(message):
      await self.message_service.handle(message)
