"""status・start・stop・uninstall モード。"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import replace

from installer.entry import remove_entry
from installer.instance import (
  InstanceLock,
  InstanceRecord,
  delete_record,
  list_instances,
  load_record,
  save_record,
  select_instance,
)
from installer.readiness import READY_TIMEOUT, ReadyError, clear_ready, wait_ready
from installer.service import Systemctl, remove_unit, unit_name
from installer.system import System
from installer.ui import UI, UserAbort


EXIT_OK = 0
EXIT_ERROR = 1

STATE_RUNNING = "稼働中"
STATE_STOPPED = "停止中（手動）"
STATE_DISABLED = "停止中（自動起動も無効）"
STATE_FAILED = "異常で停止"


def add_stop_arguments(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("--disable", action="store_true", help="自動起動も無効にする（マシンを再起動しても動かない）")


def add_uninstall_arguments(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("--purge", action="store_true", help="data/（DB）と .env も消す（確認を2回します）")


def service_state(system: System, service: str) -> str:
  systemctl = Systemctl(system)
  if systemctl.is_active(service):
    return STATE_RUNNING
  if systemctl.is_failed(service):
    return STATE_FAILED
  if not systemctl.is_enabled(service):
    return STATE_DISABLED
  return STATE_STOPPED


# status

def show_status(args: argparse.Namespace, system: System, ui: UI) -> int:
  if args.instance is not None:
    names = [select_instance(system, ui, args.instance)]
  else:
    names = list_instances(system)
  if not names:
    ui.info("インストールされたインスタンスはありません")
    return EXIT_OK
  for index, name in enumerate(names):
    record = load_record(system, name)
    if index:
      ui.info("")
    ui.info(f"[{record.instance}]")
    ui.info(f"  版          {record.version}（{record.channel}）")
    ui.info(f"  状態        {service_state(system, record.service)}")
    ui.info(f"  サービス名  {unit_name(record.service)}")
    ui.info(f"  入れる場所  {record.prefix}")
    ui.info("  自動更新    無効")
    if record.unverified:
      ui.info(f"  注意        未確認の更新あり（{record.unverified}。start で起動すると確かめます）")
    if record.updating:
      ui.info(
        f"  注意        更新が途中で止まっています（{record.updating.get('from')} → "
        f"{record.updating.get('to')}）。repair か rollback で直してください"
      )
  return EXIT_OK


# start / stop

def start(args: argparse.Namespace, system: System, ui: UI, *, wait=None) -> int:
  wait = wait or wait_ready
  instance = select_instance(system, ui, args.instance)
  with InstanceLock(system, instance):
    record = load_record(system, instance)
    systemctl = Systemctl(system)
    service = unit_name(record.service)
    if systemctl.is_active(record.service):
      ui.info(f"{service} はすでに動いています")
      return EXIT_OK
    systemctl.enable(record.service)
    clear_ready(system, record.layout)
    systemctl.start(record.service)
    ui.info(f"{service} を起動しました。起動の完了を待っています（最大{int(READY_TIMEOUT)}秒）...")
    try:
      wait(system, record.layout, record.service, record.version)
    except ReadyError as error:
      ui.error(str(error))
      return EXIT_ERROR
    if record.unverified:
      save_record(system, replace(record, unverified=None))
      ui.info(f"{record.version} の起動を確かめました")
    ui.info("起動を確認しました")
    return EXIT_OK


def stop(args: argparse.Namespace, system: System, ui: UI) -> int:
  instance = select_instance(system, ui, args.instance)
  with InstanceLock(system, instance):
    record = load_record(system, instance)
    systemctl = Systemctl(system)
    service = unit_name(record.service)
    systemctl.stop(record.service)
    ui.info(f"{service} を止めました")
    if args.disable:
      systemctl.disable(record.service)
      ui.info("自動起動も無効にしました。マシンを再起動しても動きません")
    else:
      ui.info("自動起動は有効のままです。マシンを再起動すると動き出します（止めたままにするには --disable）")
    ui.info("動かすときは sudo tsumugium-setup start")
    return EXIT_OK


# uninstall

def _confirm_uninstall(ui: UI, record: InstanceRecord, purge: bool) -> None:
  layout = record.layout
  ui.info(f"インスタンス {record.instance} を消します")
  ui.info(f"  消すもの: {unit_name(record.service)}、{layout.app}、{layout.app_prev}、記録、ユーザー {record.user}")
  if purge:
    ui.info(f"  さらに消すもの: {layout.data}（DBとバックアップ）、{layout.env_file}")
  else:
    ui.info(f"  残すもの: {layout.data}（DBとバックアップ）、{layout.env_file}")
  if not ui.confirm("消してよいですか？", default=False):
    raise UserAbort("アンインストールを中止しました")
  if purge:
    ui.warn("DBとバックアップも消すと、元に戻せません")
    answer = ui.ask(f"本当に消すときは、インスタンス名（{record.instance}）を入力してください", "")
    if answer != record.instance:
      raise UserAbort("インスタンス名が一致しないため、中止しました")


def uninstall(args: argparse.Namespace, system: System, ui: UI) -> int:
  if args.purge and ui.assume_yes:
    raise UserAbort("--purge は --yes と一緒には使えません。確認に答えながら実行してください")
  instance = select_instance(system, ui, args.instance)
  with InstanceLock(system, instance):
    record = load_record(system, instance)
    layout = record.layout
    _confirm_uninstall(ui, record, args.purge)

    systemctl = Systemctl(system)
    systemctl.stop(record.service, check=False)
    systemctl.disable(record.service, check=False)
    if remove_unit(system, record.service):
      ui.info(f"{unit_name(record.service)} を消しました")
    for directory in (layout.app, layout.app_prev, layout.app_new, layout.prefix / ".download"):
      shutil.rmtree(system.path(str(directory)), ignore_errors=True)
    ui.info(f"{layout.app} などのコードを消しました")
    if args.purge:
      shutil.rmtree(system.path(str(layout.data)), ignore_errors=True)
      for path in (layout.env_file, layout.env_backup):
        system.path(str(path)).unlink(missing_ok=True)
      prefix = system.path(str(layout.prefix))
      if prefix.is_dir() and not any(prefix.iterdir()):
        prefix.rmdir()
      ui.info(f"{layout.data} と {layout.env_file} を消しました")
    delete_record(system, instance)

    others = [load_record(system, name) for name in list_instances(system)]
    if not any(other.user == record.user for other in others):
      system.run(["userdel", record.user], check=False)
      system.run(["groupdel", record.user], check=False)
      ui.info(f"ユーザー {record.user} を消しました")
    if not others and remove_entry(system):
      ui.info("tsumugium-setup を消しました（インスタンスがなくなったため）")

  ui.info("")
  ui.info(f"インスタンス {instance} を消しました。")
  if not args.purge:
    ui.info(f"{layout.data} と {layout.env_file} は残しています。")
    ui.info("同じ名前で入れ直すと、そのまま使えます。")
  return EXIT_OK
