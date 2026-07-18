"""外部HTTPクライアントも終了時に閉じるDiscord Client。"""

import discord


class ManagedDiscordClient(discord.Client):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._closeables = []

  def register_closeable(self, closeable) -> None:
    self._closeables.append(closeable)

  async def close(self) -> None:
    try:
      await super().close()
    finally:
      for closeable in reversed(self._closeables):
        try:
          await closeable.close()
        except Exception as e:
          print(f"HTTPクライアント終了エラー: {e}")
