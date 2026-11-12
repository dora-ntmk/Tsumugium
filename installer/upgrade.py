"""upgrade モード。

更新は2つの部分に分かれる。
- 今の版のインストーラー（run_upgrade）: ロック、版の選択、取得・照合・展開までを行い、
  新しい版のインストーラーを呼ぶ。ロックは新しい版が終わるまで持ち続ける。
- 新しい版のインストーラー（continue_upgrade）: venv の作成、.env への書き足し、
  切り替え、起動の確認、失敗したときのロールバックを行う。

2つの間は data/state/handoff.json で引き継ぐ。形式には番号（HANDOFF_VERSION）を付け、
知らない番号を受け取ったら何も変えずに止める。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from installer import HANDOFF_VERSION
from installer.checks import CheckError, probe_voicevox
from installer.deploy import build_venv
from installer.entry import install_entry
from installer.envfile import upgrade_env
from installer.instance import (
  InstanceError,
  InstanceLock,
  InstanceRecord,
  Layout,
  load_record,
  save_record,
  select_instance,
)
from installer.readiness import READY_TIMEOUT, ReadyError, clear_ready, wait_ready
from installer.release import (
  Fetched,
  GitHubClient,
  ReleaseError,
  Version,
  check_direction,
  extract_from_file,
  fetch_release,
  select_release,
)
from installer.service import Systemctl, unit_name, unit_path, write_unit
from installer.system import System
from installer.ui import UI, UserAbort


EXIT_OK = 0
EXIT_ERROR = 1
HANDOFF_FILE = "handoff.json"
UPGRADE_BACKUP_PREFIX = "upgrade-"
UPGRADE_BACKUP_KEEP = 5


def add_repair_arguments(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("--from-file", help="GitHubから取得せず、手元のzipを使う（隣に .sha256 が必要）")


def add_arguments(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("--version", dest="target_version", help="入れる版（省略時はチャンネルの最新）")
  parser.add_argument("--allow-downgrade", action="store_true", help="今より古い版も入れる")
  parser.add_argument("--from-file", help="GitHubから取得せず、手元のzipを使う（隣に .sha256 が必要）")


# 今の版のインストーラー

def handoff_path(system: System, layout: Layout) -> Path:
  return system.path(str(layout.state / HANDOFF_FILE))


def write_handoff(system: System, layout: Layout, data: dict) -> Path:
  path = handoff_path(system, layout)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  os.chmod(path, 0o600)
  return path


def launch_new_installer(system: System, ui: UI, app_new: Path, handoff: Path) -> int:
  """新しい版のインストーラーを、画面の入出力をそのまま渡して動かす。"""
  env = {**os.environ, "PYTHONPATH": str(app_new)}
  completed = subprocess.run(
    [sys.executable, "-m", "installer.handoff", str(handoff)],
    cwd=str(app_new),
    env=env,
  )
  return completed.returncode


def launch_rollback(system: System, ui: UI, record: InstanceRecord) -> int:
  """1つ前の版（app.prev/）のインストーラーで rollback を動かす。"""
  app_prev = system.path(str(record.layout.app_prev))
  if not (app_prev / "installer").is_dir():
    raise InstanceError("戻せる1つ前の版がありません。repair を使ってください")
  env = {**os.environ, "PYTHONPATH": str(app_prev)}
  args = [sys.executable, "-m", "installer", "rollback", "--instance", record.instance]
  return subprocess.run(args, cwd=str(app_prev), env=env).returncode


def _resolve_unfinished(system: System, ui: UI, record: InstanceRecord) -> str:
  """更新中の印が残っていたら、repair と rollback のどちらにするかを聞く。"""
  updating = record.updating
  message = f"前回の更新（{updating.get('from')} → {updating.get('to')}）が途中で止まっています。"
  if ui.assume_yes:
    raise InstanceError(
      message + "sudo tsumugium-setup repair か sudo tsumugium-setup rollback で直してください"
    )
  ui.warn(message)
  ui.info(f"  repair  : 記録の版（{record.version}）を入れ直す")
  ui.info("  rollback: 1つ前の版（app.prev/）に戻す")
  return ui.choose("どちらで直しますか", ["repair", "rollback"], "repair")


def run_upgrade(
    args: argparse.Namespace,
    system: System,
    ui: UI,
    *,
    client: GitHubClient | None = None,
    launch: Callable[[System, UI, Path, Path], int] = launch_new_installer,
    rollback: Callable[[System, UI, InstanceRecord], int] = launch_rollback,
) -> int:
  instance = select_instance(system, ui, args.instance)
  record = load_record(system, instance)
  if record.updating:
    choice = _resolve_unfinished(system, ui, record)
    if choice == "rollback":
      return rollback(system, ui, record)
    return _fetch_and_hand_off(args, system, ui, instance, "repair", client, launch)
  return _fetch_and_hand_off(args, system, ui, instance, "upgrade", client, launch)


def run_repair(
    args: argparse.Namespace,
    system: System,
    ui: UI,
    *,
    client: GitHubClient | None = None,
    launch: Callable[[System, UI, Path, Path], int] = launch_new_installer,
) -> int:
  instance = select_instance(system, ui, args.instance)
  return _fetch_and_hand_off(args, system, ui, instance, "repair", client, launch)


def _fetch_and_hand_off(args, system, ui, instance, mode, client, launch) -> int:
  repair = mode == "repair"
  with InstanceLock(system, instance):
    record = load_record(system, instance)
    if record.updating and not repair:
      raise InstanceError("前回の更新が途中で止まっています。repair か rollback で直してください")
    layout = record.layout
    ui.info(f"インスタンス {instance}（今の版 {record.version}、{record.channel}）")
    ui.step("取得")
    from_file = getattr(args, "from_file", None)
    if from_file:
      fetched = extract_from_file(system, ui, layout, Path(from_file))
      if repair:
        if fetched.version != Version.parse(record.version):
          shutil.rmtree(fetched.path, ignore_errors=True)
          raise ReleaseError(
            f"zipの版（{fetched.version}）が記録の版（{record.version}）と違います。"
            "repair には同じ版のzipを使ってください"
          )
        direction = "repair"
      else:
        direction = check_direction(record.version, fetched.version, allow_downgrade=args.allow_downgrade)
    else:
      client = client or GitHubClient()
      requested = record.version if repair else args.target_version
      release = select_release(client, record.repo, record.channel, requested=requested)
      if repair:
        direction = "repair"
      else:
        direction = check_direction(record.version, release.version, allow_downgrade=args.allow_downgrade)
      if direction != "same":
        fetched = fetch_release(system, ui, layout, client, release)
    if direction == "same":
      if from_file:
        shutil.rmtree(fetched.path, ignore_errors=True)
      ui.info(f"すでに {record.version} です。入れ直すときは sudo tsumugium-setup repair を使ってください")
      return EXIT_OK

    if not (fetched.path / "installer" / "handoff.py").is_file():
      shutil.rmtree(fetched.path, ignore_errors=True)
      raise ReleaseError(f"{fetched.version} には、この方法で更新できるインストーラーがありません")
    if repair:
      question = f"{fetched.version} を入れ直します。よろしいですか？"
    else:
      word = "戻します" if direction == "older" else "更新します"
      question = f"{record.version} から {fetched.version} に{word}。よろしいですか？"
    if not ui.confirm(question, default=True):
      shutil.rmtree(fetched.path, ignore_errors=True)
      raise UserAbort("中止しました")

    handoff = write_handoff(system, layout, {
      "handoff": HANDOFF_VERSION,
      "mode": mode,
      "instance": instance,
      "from_version": record.version,
      "to_version": fetched.build["version"],
      "assume_yes": ui.assume_yes,
    })
    try:
      return launch(system, ui, fetched.path, handoff)
    finally:
      handoff.unlink(missing_ok=True)


# 新しい版のインストーラー

def read_handoff(path: Path) -> dict:
  try:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError) as error:
    raise InstanceError(f"引き継ぎのファイルを読めません: {error}") from None
  if not isinstance(data, dict) or data.get("handoff") != HANDOFF_VERSION:
    raise InstanceError(
      f"引き継ぎの形式（handoff={data.get('handoff') if isinstance(data, dict) else None!r}）に"
      "対応していません。何も変えずに止めます"
    )
  for key in ("instance", "from_version", "to_version"):
    if not isinstance(data.get(key), str):
      raise InstanceError(f"引き継ぎのファイルに {key} がありません")
  return data


def _timestamp() -> str:
  return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_databases(system: System, layout: Layout, user: str, from_version: str) -> Path:
  """data/db/ を data/backup/upgrade-<日時>-<前の版>/ にコピーし、5世代だけ残す。"""
  backup_root = system.path(str(layout.backup))
  name = f"{UPGRADE_BACKUP_PREFIX}{_timestamp()}-{Version.parse(from_version).tag}"
  destination = backup_root / name
  shutil.copytree(system.path(str(layout.db)), destination)
  system.run(["chown", "-R", f"{user}:{user}", str(layout.backup / name)])
  old = sorted(
    path for path in backup_root.iterdir()
    if path.is_dir() and path.name.startswith(UPGRADE_BACKUP_PREFIX)
  )
  for path in old[:-UPGRADE_BACKUP_KEEP]:
    shutil.rmtree(path)
  return destination


def restore_databases(system: System, layout: Layout, user: str, backup: Path) -> None:
  db = system.path(str(layout.db))
  shutil.rmtree(db)
  shutil.copytree(backup, db)
  system.run(["chown", "-R", f"{user}:{user}", str(layout.db)])


@dataclass
class _Switch:
  """切り替えで変えたものを覚えておき、失敗したときに戻すために使う。"""

  was_active: bool
  unit_text: str | None = None
  backup: Path | None = None
  stopped: bool = False
  switched: bool = False
  # repair で、壊れているかもしれない app/ を脇に置いたか
  aside: bool = False
  # upgrade で、それまでの app.prev/ を脇に置いたか（失敗したら戻す）
  prev_aside: bool = False


class Continuation:
  def __init__(self, system: System, ui: UI, data: dict, *, python: str, wait):
    self.system = system
    self.ui = ui
    self.data = data
    self.python = python
    self.wait = wait
    self.record = load_record(system, data["instance"])
    self.layout = self.record.layout
    self.systemctl = Systemctl(system)
    self.from_version = data["from_version"]
    self.to_version = data["to_version"]
    # repair は記録の版を入れ直す。app.prev/（本当の1つ前の版）は残す
    self.repair = data.get("mode", "upgrade") == "repair"
    self.app_aside = self.layout.prefix / "app.repair-old"
    self.prev_aside = self.layout.prefix / "app.prev-old"

  def path(self, absolute) -> Path:
    return self.system.path(str(absolute))

  def discard_new(self) -> None:
    shutil.rmtree(self.path(self.layout.app_new), ignore_errors=True)

  # 準備（Botは動いたまま）

  def prepare(self) -> bool | None:
    """切り替えの前の準備。更新後に起動するかを返す。更新しないときは None。"""
    if self.record.version != self.from_version:
      raise InstanceError(
        f"記録の版（{self.record.version}）が、引き継いだ版（{self.from_version}）と違います"
      )
    if not self.path(self.layout.app_new).is_dir():
      raise InstanceError(f"{self.layout.app_new} がありません")

    ui = self.ui
    service = self.record.service
    start_after = True
    if not self.systemctl.is_active(service):
      if self.repair and ui.assume_yes:
        # 入れ直しは、止まった（壊れた）Botを直すために使うことが多いので、起動して確かめる
        ui.info(f"{unit_name(service)} は止まっています。入れ直したあとで起動して確かめます")
      elif ui.assume_yes:
        ui.warn(f"{unit_name(service)} が止まっているため、更新しません。起動してから、もう一度実行してください")
        return None
      start_after = ui.confirm(
        f"{unit_name(service)} は止まっています。更新のあとで起動して確かめますか？", default=True
      )

    ui.step("更新の準備")
    build_venv(self.system, ui, self.python, self.layout.app_new)
    if start_after:
      try:
        version = probe_voicevox(self.record.voicevox_url)
      except CheckError as error:
        raise CheckError(f"{error}。VOICEVOXを起動してから、もう一度実行してください") from None
      ui.info(f"VOICEVOX: 接続できました（バージョン {version}）")
    upgrade_env(
      self.system,
      ui,
      self.layout,
      self.path(self.layout.app_new) / ".env.template",
      self.record.user,
      self.to_version,
    )
    return start_after

  # 切り替え

  def switch(self, state: _Switch) -> InstanceRecord:
    ui = self.ui
    record = self.record
    ui.step("切り替え")
    save_record(self.system, replace(record, updating={
      "from": self.from_version,
      "to": self.to_version,
      "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }))
    if state.was_active:
      self.systemctl.stop(record.service)
      state.stopped = True
      ui.info(f"{unit_name(record.service)} を止めました")
    state.backup = backup_databases(self.system, self.layout, record.user, self.from_version)
    ui.info(f"DBを {self.layout.backup / state.backup.name} にコピーしました")

    app, app_prev, app_new = (self.path(p) for p in (self.layout.app, self.layout.app_prev, self.layout.app_new))
    if self.repair:
      aside = self.path(self.app_aside)
      shutil.rmtree(aside, ignore_errors=True)
      if app.exists():
        os.rename(app, aside)
        state.aside = True
      os.rename(app_new, app)
      state.switched = True
      ui.info(f"{self.layout.app} を入れ直しました（{self.layout.app_prev} はそのまま残します）")
    else:
      # それまでの app.prev/ は、成功するまで消さずに脇に置く
      prev_aside = self.path(self.prev_aside)
      shutil.rmtree(prev_aside, ignore_errors=True)
      if app_prev.exists():
        os.rename(app_prev, prev_aside)
        state.prev_aside = True
      os.rename(app, app_prev)
      os.rename(app_new, app)
      state.switched = True
      ui.info(f"{self.layout.app} を {self.to_version} に切り替えました（前の版は {self.layout.app_prev}）")

    unit = unit_path(self.system, record.service)
    state.unit_text = unit.read_text(encoding="utf-8") if unit.is_file() else None
    new_record = replace(record, version=self.to_version, updating=None, unverified=None)
    write_unit(self.system, new_record)
    return new_record

  def start_and_wait(self, version: str) -> None:
    service = self.record.service
    clear_ready(self.system, self.layout)
    self.systemctl.start(service)
    self.ui.info(f"起動の完了を待っています（最大{int(READY_TIMEOUT)}秒）...")
    self.wait(self.system, self.layout, service, version)

  def rollback(self, state: _Switch, reason: str) -> None:
    ui = self.ui
    record = self.record
    ui.error(reason)
    ui.step("ロールバック")
    self.systemctl.stop(record.service, check=False)
    if state.switched and self.repair:
      app = self.path(self.layout.app)
      shutil.rmtree(app, ignore_errors=True)
      if state.aside:
        os.rename(self.path(self.app_aside), app)
      ui.info(f"{self.layout.app} を入れ直す前の状態に戻しました")
    elif state.switched:
      app, app_prev = self.path(self.layout.app), self.path(self.layout.app_prev)
      failed = self.path(self.layout.prefix / "app.failed")
      shutil.rmtree(failed, ignore_errors=True)
      os.rename(app, failed)
      os.rename(app_prev, app)
      shutil.rmtree(failed, ignore_errors=True)
      ui.info(f"{self.layout.app} を {self.from_version} に戻しました")
    if state.prev_aside:
      os.rename(self.path(self.prev_aside), self.path(self.layout.app_prev))
    else:
      self.discard_new()
    if state.backup is not None:
      restore_databases(self.system, self.layout, record.user, state.backup)
      ui.info("DBを更新の前の状態に戻しました")
    if state.unit_text is not None:
      unit = unit_path(self.system, record.service)
      unit.write_text(state.unit_text, encoding="utf-8")
      self.systemctl.daemon_reload()
      ui.info("unitを元に戻しました")
    save_record(self.system, replace(record, updating=None))
    ui.info(".env に書き足した項目は残しています")
    if state.was_active:
      try:
        self.start_and_wait(self.from_version)
        ui.info(f"{self.from_version} で起動し直しました")
      except ReadyError as error:
        ui.error(f"戻した版でも起動を確認できませんでした: {error}")

  def run(self) -> int:
    ui = self.ui
    if self.repair:
      ui.info(f"{self.to_version} の入れ直しを続けます（取得した版のインストーラー）")
    else:
      ui.info(f"{self.from_version} → {self.to_version} の更新を続けます（新しい版のインストーラー）")
    try:
      start_after = self.prepare()
    except BaseException:
      self.discard_new()
      raise
    if start_after is None:
      self.discard_new()
      return EXIT_ERROR

    state = _Switch(was_active=self.systemctl.is_active(self.record.service))
    try:
      new_record = self.switch(state)
      if start_after:
        ui.step("起動")
        self.start_and_wait(self.to_version)
      else:
        new_record = replace(new_record, unverified=self.to_version)
      save_record(self.system, new_record)
    except ReadyError as error:
      self.rollback(state, f"{self.to_version} の起動を確認できませんでした: {error}")
      return EXIT_ERROR
    except Exception as error:  # 途中のどこで失敗しても、前の版に戻す
      self.rollback(state, f"更新の途中で失敗しました: {error}")
      return EXIT_ERROR

    if state.aside:
      shutil.rmtree(self.path(self.app_aside), ignore_errors=True)
    if state.prev_aside:
      shutil.rmtree(self.path(self.prev_aside), ignore_errors=True)
    # 入口のスクリプトも新しい版のものに置き直す
    install_entry(self.system)
    if start_after:
      ui.info(f"{self.to_version} を入れ直しました" if self.repair else f"{self.to_version} に更新しました")
    else:
      ui.info(f"{self.to_version} に入れ替えました。Botは止めたままです")
      ui.info("次に起動したときに、Botが ready.json を書くかで更新を確かめます")
    return EXIT_OK


def continue_upgrade(
    system: System,
    ui: UI,
    handoff: Path,
    *,
    python: str | None = None,
    wait=None,
) -> int:
  data = read_handoff(handoff)
  return Continuation(
    system, ui, data, python=python or sys.executable, wait=wait or wait_ready
  ).run()
