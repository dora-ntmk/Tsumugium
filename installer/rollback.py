"""rollback モード: app.prev/（1つ前の版）に戻す。

入口（tsumugium-setup）は、このモードを app.prev/ にあるインストーラーで動かす。
そのため、このファイルのコードは「戻す先の版」のものとして動く。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

from installer.entry import install_entry
from installer.instance import InstanceError, InstanceLock, load_record, save_record, select_instance
from installer.readiness import READY_TIMEOUT, ReadyError, clear_ready, wait_ready
from installer.release import Version
from installer.service import Systemctl, unit_name, write_unit
from installer.system import System
from installer.ui import UI, UserAbort
from installer.upgrade import UPGRADE_BACKUP_PREFIX, restore_databases


EXIT_OK = 0
EXIT_ERROR = 1


def previous_version(app_prev: Path) -> str:
  try:
    build = json.loads((app_prev / "build-info.json").read_text(encoding="utf-8"))
    return str(Version.parse(build["version"]))
  except FileNotFoundError:
    raise InstanceError("戻せる1つ前の版がありません") from None
  except (OSError, ValueError, KeyError, TypeError):
    raise InstanceError(f"{app_prev}/build-info.json を読めません") from None


def find_backup(system: System, backup_dir, version: str) -> Path | None:
  """その版から更新したときのDBのバックアップのうち、一番新しいもの。"""
  root = system.path(str(backup_dir))
  if not root.is_dir():
    return None
  suffix = f"-{Version.parse(version).tag}"
  candidates = sorted(
    path for path in root.iterdir()
    if path.is_dir() and path.name.startswith(UPGRADE_BACKUP_PREFIX) and path.name.endswith(suffix)
  )
  return candidates[-1] if candidates else None


def run_rollback(args: argparse.Namespace, system: System, ui: UI, *, wait=None) -> int:
  wait = wait or wait_ready
  instance = select_instance(system, ui, args.instance)
  with InstanceLock(system, instance):
    record = load_record(system, instance)
    layout = record.layout
    app = system.path(str(layout.app))
    app_prev = system.path(str(layout.app_prev))
    target = previous_version(app_prev)

    ui.info(f"インスタンス {instance}: {record.version} から {target} に戻します")
    if record.updating:
      ui.info("（途中で止まった更新を、1つ前の版に戻して片付けます）")
    if not ui.confirm("戻してよいですか？", default=True):
      raise UserAbort("中止しました")
    backup = find_backup(system, layout.backup, target)
    restore_db = False
    if backup is None:
      ui.warn(f"{target} から更新したときのDBのバックアップが見つからないため、DBはそのままにします")
    else:
      ui.info(f"DBのバックアップ: {layout.backup / backup.name}")
      restore_db = ui.confirm(
        "DBも更新の直前の状態に戻しますか？（戻さない場合、新しい版がDBの形を変えていると読めないことがあります）",
        default=True,
      )

    systemctl = Systemctl(system)
    was_active = systemctl.is_active(record.service)
    ui.step("ロールバック")
    systemctl.stop(record.service, check=False)
    # app/ と app.prev/ を入れ替える。新しい版も消さないので、upgrade でまた進められる
    swap = system.path(str(layout.prefix / "app.swap"))
    shutil.rmtree(swap, ignore_errors=True)
    if app.exists():
      os.rename(app, swap)
    os.rename(app_prev, app)
    if swap.exists():
      os.rename(swap, app_prev)
    ui.info(f"{layout.app} を {target} に戻しました（戻す前の版は {layout.app_prev}）")
    if restore_db:
      restore_databases(system, layout, record.user, backup)
      ui.info("DBを更新の直前の状態に戻しました")

    restored = replace(record, version=target, updating=None, unverified=None)
    write_unit(system, restored)
    install_entry(system)
    ui.info(".env はそのままです（新しい版で足した項目は、この版では使われません）")

    if not was_active:
      save_record(system, replace(restored, unverified=target))
      ui.info("Botは止めたままです。動かすときは sudo tsumugium-setup start")
      return EXIT_OK
    save_record(system, restored)
    ui.step("起動")
    clear_ready(system, layout)
    systemctl.start(record.service)
    ui.info(f"起動の完了を待っています（最大{int(READY_TIMEOUT)}秒）...")
    try:
      wait(system, layout, record.service, target)
    except ReadyError as error:
      ui.error(f"{target} の起動を確認できませんでした: {error}")
      ui.info(f"記録は {target} にしてあります。repair で入れ直すか、upgrade で新しい版に進めてください")
      save_record(system, replace(restored, unverified=target))
      return EXIT_ERROR
    ui.info(f"{target} に戻しました")
    return EXIT_OK
