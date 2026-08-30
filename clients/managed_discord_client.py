"""外部HTTPクライアントも終了時に閉じるDiscord Client。"""

import inspect

import discord


class ManagedDiscordClient(discord.Client):
  def __init__(self, *args, error_notifier=None, **kwargs):
    super().__init__(*args, **kwargs)
    self._closeables = []
    self._startup_checks = []
    self.error_notifier = error_notifier

  def set_error_notifier(self, error_notifier) -> None:
    self.error_notifier = error_notifier

  def register_closeable(self, closeable) -> None:
    self._closeables.append(closeable)

  def register_startup_check(self, startup_check) -> None:
    """Discord Gateway接続前に実行する非同期チェックを登録する。"""
    self._startup_checks.append(startup_check)

  async def setup_hook(self) -> None:
    await super().setup_hook()
    for startup_check in self._startup_checks:
      await startup_check()

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
          message = f"リソース終了エラー: {e}"
          if self.error_notifier is None:
            print(message)
          else:
            self.error_notifier.report(message)

  async def on_error(self, event_method: str, *args, **kwargs) -> None:
    import sys

    error = sys.exc_info()[1]
    if error is None:
      message = f"Exception in {event_method}: unknown error"
    else:
      message = f"Exception in {event_method}: {type(error).__name__}: {error}"
    if self.error_notifier is None:
      print(message)
    else:
      self.error_notifier.report(message)
