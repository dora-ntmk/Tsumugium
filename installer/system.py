"""システムに触る処理の窓口。

パスはすべて「システムのルート」からの相対で組み立て、コマンドは Runner を通して実行する。
テストでは、ルートを一時ディレクトリに、Runner を記録だけする偽物に差し替える。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class CommandError(RuntimeError):
  """外部コマンドが失敗したことを表す。"""

  def __init__(self, args: list[str], returncode: int, stderr: str):
    self.args_list = list(args)
    self.returncode = returncode
    self.stderr = stderr
    super().__init__(
      f"コマンドが失敗しました（終了コード {returncode}）: {' '.join(args)}"
      + (f"\n{stderr.strip()}" if stderr and stderr.strip() else "")
    )


@dataclass
class CommandResult:
  returncode: int
  stdout: str = ""
  stderr: str = ""


class Runner:
  """subprocess で外部コマンドを実行する。"""

  def run(
      self,
      args: list[str],
      *,
      check: bool = True,
      env: dict[str, str] | None = None,
      cwd: str | None = None,
      stream: bool = False,
  ) -> CommandResult:
    """コマンドを実行する。

    stream が True のときは出力を取り込まず、そのまま画面に流す（apt や pip のように
    時間がかかり、進み具合を見せたいコマンドに使う）。このとき結果の stdout・stderr は空になる。
    """
    if stream:
      completed = subprocess.run(args, env=env, cwd=cwd)
      if check and completed.returncode != 0:
        raise CommandError(args, completed.returncode, "（出力は上に表示されています）")
      return CommandResult(completed.returncode)
    completed = subprocess.run(
      args,
      capture_output=True,
      text=True,
      env=env,
      cwd=cwd,
    )
    result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
      raise CommandError(args, result.returncode, result.stderr)
    return result


@dataclass
class RecordingRunner(Runner):
  """呼ばれたコマンドを記録するだけの偽物（テスト用）。

  responses に「コマンドの先頭の語の並び」→ CommandResult を登録しておくと、その結果を返す。
  登録がなければ、成功（終了コード0、出力なし）として扱う。
  """

  calls: list[list[str]] = field(default_factory=list)
  responses: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
  # stream=True で呼ばれたコマンド（出力を画面に流したもの）
  streamed: list[list[str]] = field(default_factory=list)

  def run(self, args, *, check=True, env=None, cwd=None, stream=False):
    self.calls.append(list(args))
    if stream:
      self.streamed.append(list(args))
    result = CommandResult(0)
    for length in range(len(args), 0, -1):
      key = tuple(args[:length])
      if key in self.responses:
        result = self.responses[key]
        break
    if check and result.returncode != 0:
      raise CommandError(list(args), result.returncode, result.stderr)
    return result


class System:
  """パスの組み立てとコマンドの実行をまとめる。"""

  def __init__(
      self,
      root: str | os.PathLike = "/",
      runner: Runner | None = None,
      *,
      euid: int | None = None,
  ):
    self.root = Path(root)
    self.runner = runner or Runner()
    self._euid = euid

  @property
  def is_root(self) -> bool:
    euid = self._euid if self._euid is not None else os.geteuid()
    return euid == 0

  def path(self, absolute: str | os.PathLike) -> Path:
    """システム上の絶対パスを、ルートの下の実際のパスに変える。

    ルートの外を指すパス（.. を含むものなど）は受け付けない。
    """
    absolute = Path(absolute)
    if not absolute.is_absolute():
      raise ValueError(f"絶対パスを指定してください: {absolute}")
    if ".." in absolute.parts:
      raise ValueError(f".. を含むパスは使えません: {absolute}")
    return self.root.joinpath(*absolute.parts[1:])

  def run(self, args: list[str], **kwargs) -> CommandResult:
    return self.runner.run(args, **kwargs)
