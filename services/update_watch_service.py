"""インストーラーが data/state/ に置くファイルを見張り、更新の様子を知らせる。

- updating.json があるあいだは、Botのステータスを「取り込み中」と「アップデート中」の表示にする。
- last-update.json（更新・入れ直し・戻したときの結果）を読み、まだ知らせていなければ運営者へDMで知らせる。
  インストーラーは新しい版のBotの起動を確かめてから結果を書くので、起動したときだけでなく見張りのたびに読む。
- 自動更新が人の手を要る状態（前回の更新が途中で止まっているなど）になったら、運営者へDMで知らせる。
- 自動更新がオフのときにインストーラーが見つけた新しい版（available-update.json）を、
  「今すぐ更新する」「VCが空いたら更新する」のボタン付きのDMで運営者へ知らせる。
  ボタンが押されたら update-request.json に依頼を書き、インストーラーの path unit が更新を動かす。
  「VCが空いたら」のときは pending-update.json に残し、BotがどのVCにもつないでいないときに依頼を書く。
  押されたDMは、更新が始まったら「更新を開始しました」に書き換える。

UPDATE_STATE_DIR が空欄（インストーラーを使わずに動かしている）なら何もしない。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from typing import Callable

import discord

from presentation.embeds import EmbedType, make_embed
from services.error_notification_service import ensure_error_notifier


UPDATING_FILE = "updating.json"
LAST_UPDATE_FILE = "last-update.json"
AUTO_UPDATE_FILE = "auto-update.json"
NOTICE_FILE = "update-notice.json"
AVAILABLE_FILE = "available-update.json"
REQUEST_FILE = "update-request.json"
PENDING_FILE = "pending-update.json"
MODE_NOW = "now"
MODE_IDLE = "idle"

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


def available_embed(data: dict) -> discord.Embed:
  version = data.get("version")
  text = f"新しい版 {version} があります。更新すると、Botが数分止まります。"
  if data.get("skipped"):
    text += f"\n\n{version} は、前に起動を確かめられなかった（または戻した）版です。"
  return make_embed("新しい版があります", text)


def started_embed(version: str | None) -> discord.Embed:
  return make_embed(
    "更新を開始しました",
    f"{version} への更新を始めました。数分のあいだ、Botが止まります。終わったら、結果をお知らせします",
    embed_type=EmbedType.SUCCESS,
  )


def update_view(version: str, *, idle: bool = True) -> discord.ui.View:
  view = discord.ui.View(timeout=None)
  view.add_item(UpdateButton(MODE_NOW, version))
  if idle:
    view.add_item(UpdateButton(MODE_IDLE, version))
  return view


class UpdateButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"tsumugium:update:(?P<mode>now|idle):(?P<version>.+)",
):
  """運営者へのDMにつける「更新する」ボタン。Botを再起動しても押せるように、custom_id に版を入れる。

  押されたときは、client.update_watch（UpdateWatchService）に処理を任せる。
  """

  def __init__(self, mode: str, version: str):
    now = mode == MODE_NOW
    super().__init__(discord.ui.Button(
      label="今すぐ更新する" if now else "VCが空いたら更新する",
      style=discord.ButtonStyle.primary if now else discord.ButtonStyle.secondary,
      custom_id=f"tsumugium:update:{mode}:{version}",
    ))
    self.mode = mode
    self.version = version

  @classmethod
  async def from_custom_id(cls, interaction, item, match):
    return cls(match["mode"], match["version"])

  async def callback(self, interaction) -> None:
    watch = getattr(interaction.client, "update_watch", None)
    if watch is None or not watch.enabled:
      await interaction.response.send_message("今は更新を受け付けられません", ephemeral=True)
      return
    await watch.handle_button(interaction, self.mode, self.version)


class UpdateWatchService:
  def __init__(
      self,
      client,
      state_dir: str,
      status_service,
      error_notifier=None,
      *,
      operator_user_id: int | None = None,
      current_version: str | None = None,
      interval: float = CHECK_INTERVAL,
      process_exists: Callable[[int], bool] = process_exists,
  ):
    self.client = client
    self.current_version = current_version
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
    while True:
      try:
        await self.check()
      except Exception as error:  # 見張りは止めない
        self.error_notifier.report_exception(error, "update watch check")
      await asyncio.sleep(self.interval)

  async def check(self) -> None:
    await self.check_updating()
    await self.mark_update_started()
    await self.notify_last_update()
    await self.check_auto_update()
    await self.offer_available_update()
    self.check_pending_update()

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

  def _write(self, name: str, data: dict) -> None:
    # 一時ファイルからの置き換えではなく、直接書く（インストーラーの path unit が、書き終わりで動くため）
    with open(self._path(name), "w", encoding="utf-8") as file:
      json.dump(data, file, ensure_ascii=False)
      file.write("\n")

  def _remove(self, name: str) -> None:
    try:
      os.remove(self._path(name))
    except FileNotFoundError:
      pass

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

  async def _send_to_operator(self, embed: discord.Embed, *, view=None) -> bool:
    if self.operator_user_id is None:
      return False
    try:
      user = self.client.get_user(self.operator_user_id)
      if user is None:
        user = await self.client.fetch_user(self.operator_user_id)
      if view is None:
        await user.send(embed=embed)
      else:
        await user.send(embed=embed, view=view)
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

  # E 「更新する」ボタン

  async def offer_available_update(self) -> None:
    """新しい版があり、まだ知らせていなければ、ボタン付きのDMを送る。"""
    if self.operator_user_id is None:
      return
    data = self._read(AVAILABLE_FILE)
    version = (data or {}).get("version")
    if not isinstance(version, str) or version == self.current_version:
      return
    if (self._read(NOTICE_FILE) or {}).get("offered_version") == version:
      return
    if await self._send_to_operator(available_embed(data), view=update_view(version)):
      self._write_notice(offered_version=version)

  def _remember_button_message(self, interaction, version: str) -> None:
    """押されたDMを覚えておき、更新が始まったら書き換える。"""
    message = getattr(interaction, "message", None)
    if message is None:
      return
    self._write_notice(button_message={"message_id": message.id, "version": version})

  async def mark_update_started(self) -> None:
    """ボタンを押したDMを、更新が始まったら「更新を開始しました」に書き換える。

    更新中（updating.json がある）か、Botがすでにその版で動いている（見張りのすきまで更新が終わった）ときに書き換える。
    """
    if self.operator_user_id is None:
      return
    record = (self._read(NOTICE_FILE) or {}).get("button_message")
    if not isinstance(record, dict):
      return
    version = record.get("version")
    if not (self._showing_updating or version == self.current_version):
      return
    try:
      user = self.client.get_user(self.operator_user_id)
      if user is None:
        user = await self.client.fetch_user(self.operator_user_id)
      channel = user.dm_channel or await user.create_dm()
      await channel.get_partial_message(record.get("message_id")).edit(
        embed=started_embed(version), view=None
      )
    except discord.NotFound:
      pass  # DMが消されていれば、書き換えずに忘れる
    except Exception as error:
      print(f"更新のお知らせを書き換えられませんでした（Discord DM）: {type(error).__name__}: {error}")
      return  # 次の見張りでもう一度試す
    self._write_notice(button_message=None)

  def _request(self, version: str, mode: str, user_id: int | None) -> None:
    self._write(REQUEST_FILE, {
      "version": version,
      "mode": mode,
      "requested_by": user_id,
      "requested_at": time.time(),
    })

  def check_pending_update(self) -> None:
    """「VCが空いたら更新する」の依頼を、BotがどのVCにもつないでいないときに出す。"""
    pending = self._read(PENDING_FILE)
    if pending is None:
      return
    version = pending.get("version")
    if not isinstance(version, str) or version == self.current_version:
      self._remove(PENDING_FILE)
      return
    if any(voice_client.is_connected() for voice_client in self.client.voice_clients):
      return
    self._request(version, MODE_IDLE, pending.get("requested_by"))
    self._remove(PENDING_FILE)

  async def handle_button(self, interaction, mode: str, version: str) -> None:
    if self.operator_user_id is None or interaction.user.id != self.operator_user_id:
      await interaction.response.send_message(
        "この操作は、Botを運営している人だけができます", ephemeral=True
      )
      return
    if version == self.current_version:
      await interaction.response.edit_message(
        embed=make_embed("更新は済んでいます", f"すでに {version} です"), view=None
      )
      return
    available = (self._read(AVAILABLE_FILE) or {}).get("version")
    if available != version:
      if isinstance(available, str):
        text = f"新しい版 {available} があります。新しいほうのお知らせから更新してください"
      else:
        text = f"{version} への更新は、もう受け付けていません"
      await interaction.response.send_message(text, ephemeral=True)
      return
    if mode == MODE_NOW:
      self._remove(PENDING_FILE)
      self._request(version, MODE_NOW, interaction.user.id)
      self._remember_button_message(interaction, version)
      await interaction.response.edit_message(
        embed=make_embed(
          "更新を受け付けました",
          f"まもなく {version} への更新を始めます。数分のあいだ、Botが止まります",
          embed_type=EmbedType.SUCCESS,
        ),
        view=None,
      )
      return
    self._write(PENDING_FILE, {"version": version, "requested_by": interaction.user.id})
    self._remember_button_message(interaction, version)
    await interaction.response.edit_message(
      embed=make_embed(
        "VCが空いたら更新します",
        f"BotがどのVCにもつないでいないときに、{version} に更新します。待たずに更新するときは、下のボタンを押してください",
      ),
      view=update_view(version, idle=False),
    )
