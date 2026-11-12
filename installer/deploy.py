"""コードの置き場所に venv を作る処理（install と upgrade で共通）。"""

from __future__ import annotations

from pathlib import PurePosixPath

from installer.service import secure_app
from installer.system import System
from installer.ui import UI


def build_venv(system: System, ui: UI, python: str, app_dir: PurePosixPath) -> None:
  """app_dir/.venv を新しく作り、requirements.lock から依存を入れ、root の持ち物にする。

  venv の中のパスは app_dir を指したままになるが、使うのは .venv/bin/python と
  python -m pip だけで、どちらも置き場所を実行時に決めるので、あとで名前を変えても動く。
  """
  venv = app_dir / ".venv"
  ui.info("venv を作成し、requirements.lock から依存をインストールしています ...")
  system.run([python, "-m", "venv", "--clear", str(venv)])
  system.run([
    str(venv / "bin" / "python"), "-m", "pip", "install",
    "--disable-pip-version-check",
    "--no-input",
    "--require-hashes",
    "-r", str(app_dir / "requirements.lock"),
  ])
  secure_app(system, str(app_dir))
