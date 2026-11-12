"""端末への表示と質問の部品。"""

from __future__ import annotations

import sys
from typing import Callable, TextIO


class UserAbort(Exception):
  """利用者が中止を選んだ、または入力が途切れたことを表す。"""


class UI:
  def __init__(
      self,
      *,
      assume_yes: bool = False,
      stdin: TextIO | None = None,
      stdout: TextIO | None = None,
      stderr: TextIO | None = None,
  ):
    self.assume_yes = assume_yes
    self.stdin = stdin or sys.stdin
    self.stdout = stdout or sys.stdout
    self.stderr = stderr or sys.stderr

  # 表示
  def info(self, message: str = "") -> None:
    print(message, file=self.stdout, flush=True)

  def step(self, title: str) -> None:
    print(f"\n[{title}]", file=self.stdout, flush=True)

  def warn(self, message: str) -> None:
    print(f"注意: {message}", file=self.stderr, flush=True)

  def error(self, message: str) -> None:
    print(f"エラー: {message}", file=self.stderr, flush=True)

  # 入力
  def _read_line(self, prompt: str) -> str:
    print(prompt, end="", file=self.stdout, flush=True)
    line = self.stdin.readline()
    if line == "":
      raise UserAbort("入力が途切れたため中止しました")
    return line.strip()

  def ask(
      self,
      question: str,
      default: str = "",
      *,
      validate: Callable[[str], str | None] | None = None,
  ) -> str:
    """既定値つきで文字列を聞く。空欄なら既定値を返す。

    validate はエラーの文を返すと聞き直し、None を返すと受け付ける。
    assume_yes のときは聞かずに既定値を返す（既定値が通らなければ中止する）。
    """
    if self.assume_yes:
      if validate is not None:
        problem = validate(default)
        if problem is not None:
          raise UserAbort(f"{question}: {problem}")
      return default
    while True:
      answer = self._read_line(f"{question} [{default}]: ") or default
      problem = validate(answer) if validate is not None else None
      if problem is None:
        return answer
      self.error(problem)

  def confirm(self, question: str, default: bool = False) -> bool:
    """はい/いいえを聞く。空欄なら既定値を返す。assume_yes のときははいを返す。"""
    if self.assume_yes:
      return True
    mark = "y" if default else "n"
    while True:
      answer = self._read_line(f"{question} (y/n) [{mark}]: ").lower()
      if answer == "":
        return default
      if answer in ("y", "yes", "はい"):
        return True
      if answer in ("n", "no", "いいえ"):
        return False
      self.error("y か n で答えてください")

  def choose(self, question: str, options: list[str], default: str) -> str:
    """選択肢から1つを選ばせる。"""
    if default not in options:
      raise ValueError(f"既定値が選択肢にありません: {default}")

    def validate(value: str) -> str | None:
      if value in options:
        return None
      return f"{' / '.join(options)} のどれかを入力してください"

    return self.ask(f"{question}（{' / '.join(options)}）", default, validate=validate)
