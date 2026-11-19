"""CLI出力と運営者へのDiscord DM通知を一元管理する。"""

import asyncio
import traceback
from collections.abc import Coroutine, Iterable
from typing import Any

from presentation.embeds import EmbedType, make_embed


_EMBED_DESCRIPTION_LIMIT = 4096
_CODE_BLOCK_PREFIX = "```\n"
_CODE_BLOCK_SUFFIX = "\n```"
_CHUNK_SIZE = (
  _EMBED_DESCRIPTION_LIMIT
  - len(_CODE_BLOCK_PREFIX)
  - len(_CODE_BLOCK_SUFFIX)
)


def operator_ids(value: int | Iterable[int] | None) -> tuple[int, ...]:
  """運営者のユーザーID（1人・複数人・なし）を、重なりのないタプルにそろえる。"""
  if value is None:
    return ()
  if isinstance(value, int):
    return (value,)
  return tuple(dict.fromkeys(value))


async def send_to_users(client, user_ids: Iterable[int], label: str, **kwargs) -> list[tuple]:
  """それぞれの人へDMを送り、送れた（ユーザーID, メッセージ）の一覧を返す。

  1人に送れなくても、ほかの人へは送る。
  """
  sent = []
  for user_id in user_ids:
    try:
      user = client.get_user(user_id)
      if user is None:
        user = await client.fetch_user(user_id)
      sent.append((user_id, await user.send(**kwargs)))
    except Exception as error:
      print(f"{label}を送れませんでした（Discord DM・{user_id}）: {type(error).__name__}: {error}")
  return sent


class ErrorNotificationService:
  """エラーをCLIへ出力し、設定済みなら同じ内容を運営者全員へDMで送る。"""

  def __init__(self, client=None, operator_user_ids: int | Iterable[int] | None = None):
    self.client = client
    self.operator_user_ids = operator_ids(operator_user_ids)
    self._tasks: set[asyncio.Task] = set()

  def report(self, message: str) -> None:
    """エラーを即時出力し、利用可能ならDM送信を予約する。"""
    text = str(message)
    print(text)
    if self.client is None or not self.operator_user_ids:
      return
    try:
      loop = asyncio.get_running_loop()
    except RuntimeError:
      return
    task = loop.create_task(self._send(text))
    self._tasks.add(task)
    task.add_done_callback(self._tasks.discard)

  def report_exception(
      self,
      error: BaseException,
      context: str,
      metadata: dict[str, object] | None = None,
  ) -> None:
    """例外のトレースバックと安全な実行コンテキストを報告する。"""
    details = [f"Exception in {context}: {type(error).__name__}: {error}"]
    if metadata:
      details.append("Context:")
      details.extend(
        f"  {key}={value}"
        for key, value in sorted(metadata.items())
      )
    details.append("Traceback:")
    details.extend(
      line.rstrip("\n")
      for line in traceback.format_exception(
        type(error), error, error.__traceback__
      )
    )
    self.report("\n".join(details))

  def create_task(
      self,
      coroutine: Coroutine[Any, Any, Any],
      context: str,
  ) -> asyncio.Task:
    """バックグラウンド処理を開始し、未捕捉例外を報告する。"""
    task = asyncio.create_task(coroutine)

    def report_failure(done_task: asyncio.Task) -> None:
      if done_task.cancelled():
        return
      error = done_task.exception()
      if error is not None:
        self.report_exception(error, context)

    task.add_done_callback(report_failure)
    return task

  async def _send(self, message: str) -> None:
    chunks = [
      message[index:index + _CHUNK_SIZE]
      for index in range(0, len(message), _CHUNK_SIZE)
    ] or [""]
    for user_id in self.operator_user_ids:
      try:
        user = self.client.get_user(user_id)
        if user is None:
          user = await self.client.fetch_user(user_id)
        for chunk in chunks:
          embed = make_embed(
            "エラーが発生しました",
            f"{_CODE_BLOCK_PREFIX}{chunk}{_CODE_BLOCK_SUFFIX}",
            embed_type=EmbedType.ERROR,
          )
          await user.send(embed=embed)
      except Exception as error:
        # 1人に送れなくても、ほかの運営者へは送る
        print(
          f"Failed to send error notification (Discord DM, {user_id}): "
          f"{type(error).__name__}: {error}"
        )


def ensure_error_notifier(error_notifier=None) -> ErrorNotificationService:
  """省略可能な依存をCLI専用の通知サービスへ正規化する。"""
  return error_notifier or ErrorNotificationService()
