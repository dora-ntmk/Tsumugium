"""音声生成の記録を、運営者へ自動でDMする。

- まだ送っていない記録が500件（GenerationLogService の持てる件数）たまったとき
- 正常な再起動・停止・更新で止める直前（ManagedDiscordClient.close() の終了前の処理）

送るのは前回送ったあとの記録だけで、同じ記録を二度送らない。送ったあともメモリには残すので、
/synthesis log は今までどおり使える。異常で落ちたときは送れない。
OPERATOR_USER_ID が空欄なら何もしない。
"""

from __future__ import annotations

import asyncio
from typing import Callable

from presentation.generation_log_format import LOG_TITLE, make_log_layout
from services.error_notification_service import ensure_error_notifier, operator_ids, send_to_users


# 500件たまってから送るまで待つ時間。最新の数件は再生が始まってから
# first・wait が書き込まれるので、少し待ってから一覧にする
FULL_SEND_DELAY = 15.0
# 止めるときに送る処理にかける時間の上限（systemd の停止を待たせすぎない）
SHUTDOWN_TIMEOUT = 10.0

REASON_UPDATE = "update"
REASON_MANUAL_STOP = "manual_stop"


def shutdown_description(reason: str | None, has_state: bool) -> str:
  if not has_state:
    when = "Botを終了する前に"
  elif reason == REASON_UPDATE:
    when = "アップデートの前に"
  elif reason == REASON_MANUAL_STOP:
    when = "Botを停止する前に"
  else:
    when = "Botを再起動する前に"
  return f"{when}、まだ送っていない記録を送ります"


def full_description(count: int) -> str:
  return f"記録が{count}件たまったため送ります"


class GenerationLogReportService:
  def __init__(
      self,
      client,
      generation_log,
      operator_user_ids,
      error_notifier=None,
      *,
      reason_reader: Callable[[], str | None] | None = None,
      delay: float = FULL_SEND_DELAY,
      shutdown_timeout: float = SHUTDOWN_TIMEOUT,
  ):
    self.client = client
    self.generation_log = generation_log
    self.operator_user_ids = operator_ids(operator_user_ids)
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.reason_reader = reason_reader
    self.delay = delay
    self.shutdown_timeout = shutdown_timeout
    # 取り出したが、まだ送っていない記録のまとまり（古い順）
    self._pending: list[list] = []
    self._tasks: set[asyncio.Task] = set()
    self._shutting_down = False
    if self.enabled:
      generation_log.set_full_listener(self._on_full)

  @property
  def enabled(self) -> bool:
    return bool(self.operator_user_ids)

  def _on_full(self) -> None:
    if self._shutting_down:
      return
    records = self.generation_log.take_unsent()
    if not records:
      return
    self._pending.append(records)
    task = self.error_notifier.create_task(self._send_later(records), "synthesis log auto send")
    self._tasks.add(task)
    task.add_done_callback(self._tasks.discard)

  async def _send_later(self, records: list) -> None:
    await asyncio.sleep(self.delay)
    # 待っているあいだに止め始めたときは、shutdown() が送る
    remaining = [batch for batch in self._pending if batch is not records]
    if len(remaining) == len(self._pending):
      return
    self._pending = remaining
    await self.send(records, full_description(len(records)))

  async def send(self, records: list, description: str) -> int:
    """記録（古い順）を運営者全員へ送り、送れた人数を返す。"""
    newest_first = records[::-1]
    summary = self.generation_log.summarize(records)
    sent = 0
    for user_id in self.operator_user_ids:
      # discord.File は一度しか送れないので、人ごとに作り直す
      layout = make_log_layout(summary, newest_first, description)
      sent += len(await send_to_users(
        self.client, [user_id], LOG_TITLE, view=layout.view, files=layout.files,
      ))
    if sent == 0:
      print(f"{LOG_TITLE}（{len(records)}件）を運営者へ送れませんでした")
    return sent

  def _shutdown_reason(self) -> tuple[str | None, bool]:
    if self.reason_reader is None:
      return None, False
    try:
      return self.reason_reader(), True
    except Exception:
      return None, True

  async def shutdown(self) -> None:
    """止める直前に、まだ送っていない記録をすぐ送る。"""
    if not self.enabled or self._shutting_down:
      return
    self._shutting_down = True
    tasks = list(self._tasks)
    for task in tasks:
      task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    batches = self._pending
    self._pending = []
    rest = self.generation_log.take_unsent()
    jobs = [(batch, full_description(len(batch))) for batch in batches]
    if rest:
      reason, has_state = self._shutdown_reason()
      jobs.append((rest, shutdown_description(reason, has_state)))
    if not jobs:
      return
    try:
      await asyncio.wait_for(self._send_all(jobs), self.shutdown_timeout)
    except asyncio.TimeoutError:
      print(f"{LOG_TITLE}を送る処理が時間内に終わりませんでした")

  async def _send_all(self, jobs) -> None:
    for records, description in jobs:
      await self.send(records, description)
