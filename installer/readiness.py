"""Botの起動の準備完了（ready.json）を待つ。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from installer.instance import Layout
from installer.service import Systemctl, unit_name
from installer.system import System


READY_TIMEOUT = 60.0
READY_INTERVAL = 1.0


class ReadyError(RuntimeError):
  """Botが準備完了にならなかったことを表す。"""


@dataclass(frozen=True)
class ReadyInfo:
  version: str
  pid: int
  ready_at: str


def clear_ready(system: System, layout: Layout) -> None:
  """起動の前に、前回の ready.json を消す。"""
  try:
    system.path(str(layout.ready_file)).unlink()
  except FileNotFoundError:
    pass


def main_pid(system: System, service: str) -> int:
  result = system.run(
    ["systemctl", "show", "--property=MainPID", "--value", unit_name(service)],
    check=False,
  )
  try:
    return int(result.stdout.strip())
  except ValueError:
    return 0


def read_ready(system: System, layout: Layout) -> ReadyInfo | None:
  path = system.path(str(layout.ready_file))
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    return None
  except (OSError, ValueError):
    # 書きかけは置き換えで防いでいるが、壊れていれば次の確認まで待つ
    return None
  if not isinstance(data, dict):
    return None
  try:
    return ReadyInfo(str(data["version"]), int(data["pid"]), str(data.get("ready_at", "")))
  except (KeyError, TypeError, ValueError):
    return None


def wait_ready(
    system: System,
    layout: Layout,
    service: str,
    expected_version: str,
    *,
    timeout: float = READY_TIMEOUT,
    interval: float = READY_INTERVAL,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ReadyInfo:
  """Botが ready.json を書くまで待つ。

  サービスの今のプロセスが書いたもので、バージョンが期待どおりなら成功とする。
  サービスが止まった・違うバージョンが動いた・時間切れのときは ReadyError。
  """
  systemctl = Systemctl(system)
  deadline = clock() + timeout
  logs = f"journalctl -u {unit_name(service)} -n 50 で記録を確かめてください"
  while True:
    if not systemctl.is_active(service):
      raise ReadyError(f"Botが起動の途中で止まりました。{logs}")
    info = read_ready(system, layout)
    if info is not None and info.pid == main_pid(system, service):
      if info.version != expected_version:
        raise ReadyError(
          f"動いているBotのバージョンが {info.version} です（期待したのは {expected_version}）"
        )
      return info
    if clock() >= deadline:
      raise ReadyError(f"{int(timeout)}秒待ちましたが、Botの準備が終わりませんでした。{logs}")
    sleep(interval)
