"""接続コマンドとVCイベントをConnectionServiceへ委譲する。"""

import discord


class ConnectionCog:
  def __init__(self, client, tree, connection_service):
    self.client = client
    self.tree = tree
    self.connection_service = connection_service
    self._register()

  def _register(self) -> None:
    @self.tree.command(
      name="join",
      description="ボイスチャンネルに接続します。",
    )
    @discord.app_commands.describe(
      change_channel="TrueにするとTextTarget・VoiceTargetをサーバー設定に適用します"
    )
    async def join(ctx, change_channel: bool = False):
      await self.connection_service.join(ctx, change_channel)

    @self.tree.command(
      name="leave",
      description="ボイスチャンネルから切断します。",
    )
    async def leave(ctx):
      await self.connection_service.leave(ctx)

    @self.client.event
    async def on_voice_state_update(member, before, after):
      try:
        await self.connection_service.handle_voice_state_update(
          member,
          before,
          after,
        )
      except Exception as error:
        self.connection_service.error_notifier.report_exception(
          error,
          "on_voice_state_update",
          self.connection_service.voice_state_error_context(
            member, before, after
          ),
        )
