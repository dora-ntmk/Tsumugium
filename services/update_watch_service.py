"""インストーラーが data/state/ に置くファイルを見張り、更新の様子を知らせる。

- updating.json があるあいだは、Botのステータスを「取り込み中」と「アップデート中」の表示にする。
- 起動したときに last-update.json（更新・入れ直し・戻したときの結果）を読み、運営者へDMで知らせる。
- 自動更新が人の手を要る状態（前回の更新が途中で止まっているなど）になったら、運営者へDMで知らせる。

UPDATE_STATE_DIR が空欄（インストーラーを使わずに動かしている）なら何もしない。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Callable

import discord

from presentation.embeds import EmbedType, make_embed
from services.error_notification_service import ensure_error_notifier


UPDATING_FILE = "updating.json"
LAST_UPDATE_FILE = "last-update.json"
AUTO_UPDATE_FILE = "auto-update.json"
NOTICE_FILE = "update-notice.json"

UPDATING_TEXT = "📥アップデート中…　操作を受け付けない場合があります"
CHECK_INTERVAL = 5.0
# 自動更新の結果のうち、人の手が要るので運営者へ知らせるもの
ATTENTION_RESULTS = frozenset({"unfinished"})


def process_exists(pid: int) -> bool:
  return os.path.exists(f"/proc/{pid}")


def last_update_embed(data: dict) -> discord.Embed:
  """last-update.json の中身から、運営者へ送るEmbedを作る。"""
  result = data.get("result")
  before = data.get("from")
  after = data.get("to")
  by = "（自動更新）" if data.get("auto") else ""
  if result == "updated":
    return make_embed("更新しました", f"{before} から {after} に更新しました{by}", embed_type=EmbedType.SUCCESS)
  if result == "repaired":
    return make_embed("入れ直しました", f"{after} を入れ直しました{by}", embed_type=EmbedType.SUCCESS)
  if result == "rolled_back":
    if data.get("mode") == "repair":
      text = f"{after} を入れ直せなかったため、元の状態に戻しました{by}"
    else:
      text = (
        f"{after} に更新できなかったため、{before} に戻しました{by}\n"
        f"{after} は、自動更新では入れません。手で入れるときは `sudo tsumugium-setup upgrade` を実行してください"
      )
    reason = data.get("reason")
    if reason:
      text += f"\n\n理由: {reason}"
    return make_embed("更新できませんでした", text, embed_type=EmbedType.ERROR)
  if result == "rollback":
    return make_embed(
      "1つ前の版に戻しました",
      f"{before} から {after} に戻しました。{before} は、自動更新では入れません",
      embed_type=EmbedType.WARNING,
    )
  return make_embed("更新の結果", f"{before} → {after}（{result}）")


def attention_embed(data: dict) -> discord.Embed:
  return make_embed(
    "自動更新を止めています",
    f"{data.get('message')}\n\n`sudo tsumugium-setup status` で状態を確かめてください",
    embed_type=EmbedType.WARNING,
  )


class UpdateWatchService:
  def __init__(
      self,
      client,
      state_dir: str,
      status_service,
      error_notifier=None,
      *,
      operator_user_id: int | None = None,
      interval: float = CHECK_INTERVAL,
      process_exists: Callable[[int], bool] = process_exists,
  ):
    self.client = client
    self.state_dir = state_dir or ""
    self.status_service = status_service
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.operator_user_id = operator_user_id
    self.interval = interval
    self.process_exists = process_exists
    self._task: asyncio.Task | None = None
    # このサービスが「アップデート中」の表示を出しているか
    self._showing_updating = False

  @property
  def enabled(self) -> bool:
    return bool(self.state_dir)

  def start(self) -> None:
    """見張りを始める。on_ready が何度呼ばれても1つだけ動かす。"""
    if not self.enabled or (self._task is not None and not self._task.done()):
      return
    self._task = self.error_notifier.create_task(self._run(), "update watch")

  async def close(self) -> None:
    """Bot終了時に呼ばれる。見張りを止める。"""
    if self._task is None or self._task.done():
      return
    self._task.cancel()
    await asyncio.gather(self._task, return_exceptions=True)

  async def _run(self) -> None:
    await self.notify_last_update()
    while True:
      try:
        await self.check()
      except Exception as error:  # 見張りは止めない
        self.error_notifier.report_exception(error, "update watch check")
      await asyncio.sleep(self.interval)

  async def check(self) -> None:
    await self.check_updating()
    await self.check_auto_update()

  # ファイル

  def _path(self, name: str) -> str:
    return os.path.join(self.state_dir, name)

  def _read(self, name: str) -> dict | None:
    try:
      with open(self._path(name), encoding="utf-8") as file:
        data = json.load(file)
    except (OSError, ValueError):
      return None
    return data if isinstance(data, dict) else None

  def _write_notice(self, **changes) -> None:
    notice = {**(self._read(NOTICE_FILE) or {}), **changes}
    temp = None
    try:
      fd, temp = tempfile.mkstemp(dir=self.state_dir, prefix=".tmp-notice-", suffix=".json")
      with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(notice, file, ensure_ascii=False)
        file.write("\n")
      os.replace(temp, self._path(NOTICE_FILE))
      temp = None
    except OSError as error:
      print(f"{NOTICE_FILE} を書けませんでした: {error}")
    finally:
      if temp is not None:
        try:
          os.remove(temp)
        except OSError:
          pass

  # ① 更新中のステータス

  def is_updating(self) -> bool:
    data = self._read(UPDATING_FILE)
    if data is None:
      return False
    pid = data.get("pid")
    # 書いたインストーラーがもういなければ、残ったファイルなので無視する
    if isinstance(pid, int) and pid > 0:
      return self.process_exists(pid)
    return True

  async def check_updating(self) -> None:
    updating = self.is_updating()
    if updating and not self._showing_updating:
      self._showing_updating = True
      await self.status_service.set_override(discord.Status.dnd, UPDATING_TEXT)
    elif not updating and self._showing_updating:
      self._showing_updating = False
      await self.status_service.clear_override()

  # D 運営者へのDM

  async def _send_to_operator(self, embed: discord.Embed) -> bool:
    if self.operator_user_id is None:
      return False
    try:
      user = self.client.get_user(self.operator_user_id)
      if user is None:
        user = await self.client.fetch_user(self.operator_user_id)
      await user.send(embed=embed)
    except Exception as error:
      print(f"更新の知らせを送れませんでした（Discord DM）: {type(error).__name__}: {error}")
      return False
    return True

  async def notify_last_update(self) -> None:
    """まだ知らせていない更新の結果があれば、運営者へ送る。"""
    data = self._read(LAST_UPDATE_FILE)
    if data is None or self.operator_user_id is None:
      return
    at = data.get("at")
    if (self._read(NOTICE_FILE) or {}).get("last_update_at") == at:
      return
    if await self._send_to_operator(last_update_embed(data)):
      self._write_notice(last_update_at=at)

  async def check_auto_update(self) -> None:
    """自動更新が人の手を要る状態になったら、その状態が続くあいだに1回だけ知らせる。"""
    if self.operator_user_id is None:
      return
    data = self._read(AUTO_UPDATE_FILE) or {}
    notice = self._read(NOTICE_FILE) or {}
    if data.get("result") not in ATTENTION_RESULTS:
      if notice.get("auto_update_attention") is not None:
        self._write_notice(auto_update_attention=None)
      return
    key = f"{data.get('result')}|{data.get('message')}"
    if notice.get("auto_update_attention") == key:
      return
    if await self._send_to_operator(attention_embed(data)):
      self._write_notice(auto_update_attention=key)
