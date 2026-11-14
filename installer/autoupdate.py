"""自動更新の timer と service、オン・オフの切り替え、結果の書き残し。

自動更新は、systemd の timer が10分おきに `tsumugium-setup upgrade --auto` を動かして行う。
更新するかどうかの判断と更新そのものは installer/upgrade.py が行う。

service は、今の app/ にこのファイルがあるときだけ動く（ConditionPathExists）。
自動更新のない古い版に rollback したときに、10分おきに失敗し続けないようにするため。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from installer.entry import ENTRY_PATH
from installer.instance import InstanceLock, InstanceRecord, Layout, load_record, save_record, select_instance
from installer.service import UNIT_MARKER, Systemctl, remove_managed_file, write_managed_file
from installer.system import System
from installer.ui import UI


EXIT_OK = 0

INTERVAL = "10min"
FIRST_RUN = "5min"
RANDOM_DELAY = "1min"
# 更新では依存の導入や起動の確認を待つので、長めにする
UPDATE_TIMEOUT = "30min"

STATE_FILE = "auto-update.json"
LAST_UPDATE_FILE = "last-update.json"
# 更新しているあいだだけ置く。Botはこれを見て、ステータスを「アップデート中」にする
UPDATING_FILE = "updating.json"
# Botを止める前に置く、止める理由。Botは止まるときと次に起動したときにこれを見て、
# VCにいた人への知らせ方と、起動したあとにつなぎ直すかを決める
RESTART_REASON_FILE = "restart-reason.json"
REASON_UPDATE = "update"
REASON_MANUAL_STOP = "manual_stop"

# auto-update.json の result
RESULT_OFF = "off"
RESULT_STOPPED = "stopped"
RESULT_UNFINISHED = "unfinished"
RESULT_BUSY = "busy"
RESULT_LATEST = "latest"
RESULT_SKIPPED = "skipped"
RESULT_UPDATED = "updated"
RESULT_FAILED = "failed"
RESULT_ERROR = "error"

RESULT_LABELS = {
  RESULT_OFF: "自動更新はオフ",
  RESULT_STOPPED: "Botが止まっているので更新しなかった",
  RESULT_UNFINISHED: "前回の更新が途中で止まっているので更新しなかった",
  RESULT_BUSY: "ほかの操作が実行中だったので更新しなかった",
  RESULT_LATEST: "新しい版はなかった",
  RESULT_SKIPPED: "新しい版は飛ばす版だったので更新しなかった",
  RESULT_UPDATED: "更新した",
  RESULT_FAILED: "更新できなかった",
  RESULT_ERROR: "確かめられなかった",
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("state", choices=("on", "off"), help="on で有効、off で無効にする")


# unit

def update_service_name(service: str) -> str:
  return f"{service}-update.service"


def update_timer_name(service: str) -> str:
  return f"{service}-update.timer"


def render_update_service(record: InstanceRecord) -> str:
  layout = record.layout
  return f"""\
{UNIT_MARKER}
# このファイルは tsumugium-setup が作りました。直接書き換えても、次の更新で元に戻ります。
[Unit]
Description=Tsumugium の自動更新 ({record.instance})
Wants=network-online.target
After=network-online.target
ConditionPathExists={layout.app}/installer/autoupdate.py

[Service]
Type=oneshot
ExecStart={ENTRY_PATH} upgrade --auto --instance {record.instance}
TimeoutStartSec={UPDATE_TIMEOUT}
"""


def render_update_timer(record: InstanceRecord) -> str:
  return f"""\
{UNIT_MARKER}
# このファイルは tsumugium-setup が作りました。直接書き換えても、次の更新で元に戻ります。
[Unit]
Description=Tsumugium の自動更新のタイマー ({record.instance})

[Timer]
OnBootSec={FIRST_RUN}
OnUnitActiveSec={INTERVAL}
RandomizedDelaySec={RANDOM_DELAY}
Unit={update_service_name(record.service)}

[Install]
WantedBy=timers.target
"""


def apply_auto_update(system: System, record: InstanceRecord) -> None:
  """timer と service を書き、記録のオン・オフに合わせて timer を有効・無効にする。"""
  changed = write_managed_file(system, update_service_name(record.service), render_update_service(record))
  changed = write_managed_file(system, update_timer_name(record.service), render_update_timer(record)) or changed
  if changed:
    Systemctl(system).daemon_reload()
  timer = update_timer_name(record.service)
  if record.auto_update_enabled:
    system.run(["systemctl", "enable", "--now", timer])
  else:
    system.run(["systemctl", "disable", "--now", timer], check=False)


def remove_auto_update(system: System, service: str) -> bool:
  """timer と service を止めて消す。消したときは True を返す。"""
  timer = update_timer_name(service)
  system.run(["systemctl", "disable", "--now", timer], check=False)
  removed = remove_managed_file(system, timer)
  removed = remove_managed_file(system, update_service_name(service)) or removed
  if removed:
    Systemctl(system).daemon_reload()
  return removed


# 結果の書き残し（data/state/。Botも読むので、専用ユーザーの持ち物にする）

def now_text() -> str:
  return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def write_state(system: System, layout: Layout, user: str, name: str, data: dict) -> Path:
  directory = system.path(str(layout.state))
  directory.mkdir(parents=True, exist_ok=True)
  path = directory / name
  fd, temp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as file:
      file.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temp, 0o640)
    os.replace(temp, path)
  except BaseException:
    try:
      os.remove(temp)
    except FileNotFoundError:
      pass
    raise
  system.run(["chown", f"{user}:{user}", str(layout.state / name)], check=False)
  return path


def read_state(system: System, layout: Layout, name: str) -> dict | None:
  try:
    data = json.loads(system.path(str(layout.state / name)).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return None
  return data if isinstance(data, dict) else None


def write_check_result(system: System, record: InstanceRecord, result: str, message: str, **extra) -> None:
  """自動更新が確かめた結果を auto-update.json に残す（status で表示する）。"""
  write_state(system, record.layout, record.user, STATE_FILE, {
    "checked_at": now_text(),
    "result": result,
    "message": message,
    "version": record.version,
    **extra,
  })


def write_last_update(system: System, record: InstanceRecord, data: dict) -> None:
  """更新（手でも自動でも）の結果を last-update.json に残す。Botが起動したときに知らせるために使う。"""
  write_state(system, record.layout, record.user, LAST_UPDATE_FILE, {"at": now_text(), **data})


def mark_updating(system: System, record: InstanceRecord, to_version: str, mode: str) -> None:
  """更新を始めたことを Bot に知らせる。書いたプロセスがいなくなれば、Botは無視する。"""
  write_state(system, record.layout, record.user, UPDATING_FILE, {
    "pid": os.getpid(),
    "mode": mode,
    "from": record.version,
    "to": to_version,
    "started_at": now_text(),
  })


def clear_updating(system: System, layout: Layout) -> None:
  system.path(str(layout.state / UPDATING_FILE)).unlink(missing_ok=True)


def mark_restart_reason(system: System, record: InstanceRecord, reason: str) -> None:
  """Botを止める理由を残す。書けなくても止める処理は続ける（Botは再起動として扱う）。"""
  try:
    write_state(system, record.layout, record.user, RESTART_REASON_FILE, {
      "reason": reason,
      "at": now_text(),
    })
  except Exception as error:
    print(f"{RESTART_REASON_FILE} を書けませんでした: {error}")


# auto-update モード

def run_mode(args: argparse.Namespace, system: System, ui: UI) -> int:
  instance = select_instance(system, ui, args.instance)
  with InstanceLock(system, instance):
    record = replace(load_record(system, instance), auto_update=args.state == "on")
    save_record(system, record)
    apply_auto_update(system, record)
  if record.auto_update:
    ui.info(f"インスタンス {instance} の自動更新を有効にしました（{record.channel} の新しい版を10分おきに確かめます）")
  else:
    ui.info(f"インスタンス {instance} の自動更新を無効にしました。更新は sudo tsumugium-setup upgrade で行います")
  return EXIT_OK


def describe(system: System, record: InstanceRecord) -> list[str]:
  """status に出す自動更新の行。"""
  lines = [f"  自動更新    {'オン（10分おき）' if record.auto_update_enabled else 'オフ'}"]
  state = read_state(system, record.layout, STATE_FILE)
  if state and isinstance(state.get("checked_at"), str):
    label = RESULT_LABELS.get(state.get("result"), str(state.get("result")))
    lines.append(f"  最後の確認  {state['checked_at']}（{label}）")
  if record.skip_versions:
    lines.append(f"  飛ばす版    {', '.join(record.skip_versions)}（自動更新では入れません）")
  return lines
