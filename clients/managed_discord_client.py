"""外部HTTPクライアントも終了時に閉じるDiscord Client。"""

import inspect

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
          result = closeable.close()
          if inspect.isawaitable(result):
            await result
        except Exception as e:
          print(f"リソース終了エラー: {e}")
