"""コマンドラインの入口。モードを振り分ける。"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from installer.checks import CheckError
from installer.envfile import EnvError
from installer.instance import InstanceError
from installer.service import ServiceError
from installer.system import CommandError, System
from installer.ui import UI, UserAbort


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

MODES = {
  "install": "初回のインストール",
  "upgrade": "指定したバージョン（省略時は最新）に更新する",
  "repair": "今と同じバージョンを入れ直す",
  "rollback": "1つ前のバージョンへ戻す",
  "uninstall": "サービスなどを消す（DBと.envは既定で残す）",
  "status": "インスタンスごとの状態を表示する",
  "start": "Botを動かす",
  "stop": "Botを止める",
}


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="tsumugium-setup",
    description="Tsumugium のインストーラー兼アップデーター",
  )
  subparsers = parser.add_subparsers(dest="mode", metavar="モード", required=True)
  for mode, help_text in MODES.items():
    sub = subparsers.add_parser(mode, help=help_text, description=help_text)
    sub.add_argument("--instance", help="対象のインスタンス名")
    sub.add_argument(
      "--yes",
      action="store_true",
      help="質問をせず、既定値で進める",
    )
  return parser


def _not_implemented(mode: str) -> Callable[[argparse.Namespace, System, UI], int]:
  def handler(args: argparse.Namespace, system: System, ui: UI) -> int:
    ui.error(f"{mode} モードはまだ実装されていません")
    return EXIT_ERROR

  return handler


# モード名 → 処理。実装した段階で差し替える。
HANDLERS: dict[str, Callable[[argparse.Namespace, System, UI], int]] = {
  mode: _not_implemented(mode) for mode in MODES
}


def main(
    argv: list[str] | None = None,
    *,
    system: System | None = None,
    ui: UI | None = None,
) -> int:
  parser = build_parser()
  try:
    args = parser.parse_args(argv)
  except SystemExit as exit_:
    return EXIT_USAGE if exit_.code not in (0, None) else EXIT_OK

  system = system or System()
  ui = ui or UI(assume_yes=args.yes)

  if not system.is_root:
    ui.error("root権限が必要です。sudo を付けて実行してください")
    return EXIT_ERROR

  try:
    return HANDLERS[args.mode](args, system, ui)
  except UserAbort as abort:
    ui.error(str(abort))
    return EXIT_ERROR
  except (CheckError, CommandError, EnvError, InstanceError, ServiceError) as error:
    ui.error(str(error))
    return EXIT_ERROR
  except KeyboardInterrupt:
    ui.error("中断しました")
    return 130


if __name__ == "__main__":
  sys.exit(main())
