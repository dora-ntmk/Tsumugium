"""main.py の終わり方を確かめる。

main.py は読み込むとBotを起動するので、構文木を読んで確かめる。
"""

import ast
import pathlib
import unittest

MAIN = pathlib.Path(__file__).resolve().parent.parent / "main.py"


def _handler(name: str) -> ast.ExceptHandler:
  tree = ast.parse(MAIN.read_text(encoding="utf-8"))
  for node in ast.walk(tree):
    if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) and node.type.id == name:
      return node
  raise AssertionError(f"except {name} が main.py にありません")


def _exit_codes(handler: ast.ExceptHandler) -> list:
  codes = []
  for node in ast.walk(handler):
    if not isinstance(node, ast.Call):
      continue
    func = node.func
    is_exit = (
      isinstance(func, ast.Attribute) and func.attr == "exit"
      and isinstance(func.value, ast.Name) and func.value.id == "sys"
    ) or (isinstance(func, ast.Name) and func.id == "SystemExit")
    if is_exit and node.args and isinstance(node.args[0], ast.Constant):
      codes.append(node.args[0].value)
  return codes


class MainExitTest(unittest.TestCase):
  def test_voicevox_startup_error_exits_with_failure(self):
    # 0 で終わると systemd の Restart=on-failure が効かず、
    # マシンの起動直後にVOICEVOXより先に起動したとき、そのまま止まってしまう
    codes = _exit_codes(_handler("VoicevoxStartupError"))
    self.assertTrue(codes, "VoicevoxStartupError のあとで終了コードを指定していません")
    self.assertTrue(all(code not in (0, None) for code in codes), codes)


if __name__ == "__main__":
  unittest.main()
