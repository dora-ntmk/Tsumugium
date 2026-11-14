"""外部HTTPクライアントも終了時に閉じるDiscord Client。"""

import asyncio
import inspect
import signal

import discord


class ManagedDiscordClient(discord.Client):
  def __init__(self, *args, error_notifier=None, **kwargs):
    super().__init__(*args, **kwargs)
    self._closeables = []
    self._startup_checks = []
    self._shutdown_hooks = []
    self._resources_closed = False
    self.error_notifier = error_notifier

  def set_error_notifier(self, error_notifier) -> None:
    self.error_notifier = error_notifier

  def register_closeable(self, closeable) -> None:
    self._closeables.append(closeable)

  def register_shutdown_hook(self, hook) -> None:
    """Discordとの接続を閉じる前に実行する非同期処理を登録する（VCから抜けるなど）。"""
    self._shutdown_hooks.append(hook)

  def register_startup_check(self, startup_check) -> None:
    """Discord Gateway接続前に実行する非同期チェックを登録する。"""
    self._startup_checks.append(startup_check)

  async def setup_hook(self) -> None:
    await super().setup_hook()
    for startup_check in self._startup_checks:
      await startup_check()

  async def run_until_stopped(self, token: str) -> None:
    """Botを動かし、SIGTERM・SIGINT を受けたら close() でDiscordとの接続を閉じてから終わる。

    discord.Client.run() は SIGTERM で接続を閉じずに終わるため、systemctl stop のあとも
    古い接続がしばらく残り、ステータスが混ざったり、止めるときの処理が動かなかったりした。
    """
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
      try:
        loop.add_signal_handler(signal_number, lambda: asyncio.ensure_future(self.close()))
      except (NotImplementedError, RuntimeError):
        pass
    async with self:
      await self.start(token)

  async def _run_shutdown_hooks(self) -> None:
    hooks, self._shutdown_hooks = self._shutdown_hooks, []
    for hook in hooks:
      try:
        await hook()
      except Exception as e:
        self._report(f"終了前の処理でエラー: {type(e).__name__}: {e}")

  def _report(self, message: str) -> None:
    if self.error_notifier is None:
      print(message)
    else:
      self.error_notifier.report(message)

  async def close(self) -> None:
    await self._run_shutdown_hooks()
    try:
      await super().close()
    finally:
      if self._resources_closed:
        return
      self._resources_closed = True
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
