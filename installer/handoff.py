"""更新の引き継ぎ先。今の版のインストーラーが、新しい版のこのファイルを呼ぶ。

  python -m installer.handoff <data/state/handoff.json のパス>
"""

from __future__ import annotations

import json
import sys

from installer.cli import EXIT_ERROR, KNOWN_ERRORS
from installer.system import System
from installer.ui import UI, UserAbort
from installer.upgrade import continue_upgrade


def main(
    argv: list[str] | None = None,
    *,
    system: System | None = None,
    ui: UI | None = None,
    **options,
) -> int:
  """options は continue_upgrade にそのまま渡す（テストで python や wait を差し替えるため）。"""
  argv = sys.argv[1:] if argv is None else argv
  if len(argv) != 1:
    print("使い方: python -m installer.handoff <引き継ぎのファイル>", file=sys.stderr)
    return 2
  system = system or System()
  if ui is None:
    try:
      with open(argv[0], encoding="utf-8") as file:
        assume_yes = bool(json.load(file).get("assume_yes"))
    except (OSError, ValueError, AttributeError):
      assume_yes = True
    ui = UI(assume_yes=assume_yes)
  if not system.is_root:
    ui.error("root権限が必要です")
    return EXIT_ERROR
  try:
    return continue_upgrade(system, ui, argv[0], **options)
  except UserAbort as abort:
    ui.error(str(abort))
    return EXIT_ERROR
  except KNOWN_ERRORS as error:
    ui.error(str(error))
    return EXIT_ERROR
  except KeyboardInterrupt:
    ui.error("中断しました")
    return 130


if __name__ == "__main__":
  sys.exit(main())
