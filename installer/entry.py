"""/usr/local/sbin/tsumugium-setup（入口のスクリプト）の中身と、置き方・消し方。

入口は、対象のインスタンスの app/ にあるインストーラーを呼ぶだけにする。
インストーラー本体はインスタンスごとに版が違うことがあるので、入口には処理を持たせない。
"""

from __future__ import annotations

import os
import tempfile

from installer.service import ServiceError
from installer.system import System


ENTRY_PATH = "/usr/local/sbin/tsumugium-setup"
ENTRY_MARKER = "# Managed by tsumugium-setup"

ENTRY_SCRIPT = '''#!/usr/bin/python3
# Managed by tsumugium-setup
"""tsumugium-setup: インスタンスの app/ にあるインストーラーを呼ぶ入口。

このファイルは tsumugium-setup が置きました。更新のたびに置き直します。
"""

import json
import os
import sys

RECORD_DIR = "/etc/tsumugium"


def instances(record_dir):
  try:
    names = os.listdir(record_dir)
  except FileNotFoundError:
    return []
  return sorted(name[:-5] for name in names if name.endswith(".json") and not name.startswith("."))


def requested_instance(args):
  for index, arg in enumerate(args):
    if arg == "--instance" and index + 1 < len(args):
      return args[index + 1]
    if arg.startswith("--instance="):
      return arg.split("=", 1)[1]
  return None


def choose(names, args):
  if "--yes" in args:
    print("エラー: インスタンスが複数あります。--instance で指定してください: " + ", ".join(names), file=sys.stderr)
    return None
  print("インスタンスが複数あります: " + ", ".join(names))
  while True:
    try:
      answer = input(f"対象のインスタンスを選んでください [{names[0]}]: ").strip() or names[0]
    except EOFError:
      return None
    if answer in names:
      return answer
    print("エラー: " + " / ".join(names) + " のどれかを入力してください", file=sys.stderr)


def main(argv=None, *, record_dir=RECORD_DIR, execute=os.execve):
  args = list(sys.argv[1:] if argv is None else argv)
  mode = next((arg for arg in args if not arg.startswith("-")), None)
  names = instances(record_dir)
  if not names:
    print("エラー: インストールされたインスタンスがありません。"
          "初めて入れるときは、展開したzipの install.sh を使ってください", file=sys.stderr)
    return 1
  name = requested_instance(args)
  if name is None:
    if mode in (None, "status") or len(names) == 1:
      name = names[0]
    else:
      name = choose(names, args)
      if name is None:
        return 1
    if mode not in (None, "status"):
      args += ["--instance", name]
  if name not in names:
    print(f"エラー: インスタンス {name} は見つかりません", file=sys.stderr)
    return 1
  try:
    with open(os.path.join(record_dir, name + ".json"), encoding="utf-8") as file:
      prefix = json.load(file)["prefix"]
  except (OSError, ValueError, KeyError, TypeError) as error:
    print(f"エラー: インスタンス {name} の記録を読めません: {error}", file=sys.stderr)
    return 1
  # rollback は、1つ前の版のインストーラー（app.prev/）で行う。
  # ほかのモードは app/ のものを使い、更新が途中で止まって app/ がないときは app.prev/ を使う
  if mode == "rollback":
    candidates = ["app.prev"]
  else:
    candidates = ["app", "app.prev"]
  app = next(
    (os.path.join(prefix, name) for name in candidates
     if os.path.isdir(os.path.join(prefix, name, "installer"))),
    None,
  )
  if app is None:
    if mode == "rollback":
      print("エラー: 戻せる1つ前の版がありません", file=sys.stderr)
    else:
      print(f"エラー: {prefix} にインストーラーがありません。展開したzipの install.sh で入れ直してください",
            file=sys.stderr)
    return 1
  env = dict(os.environ, PYTHONPATH=app)
  execute(sys.executable, [sys.executable, "-m", "installer", *args], env)
  return 0


if __name__ == "__main__":
  sys.exit(main())
'''


def install_entry(system: System) -> None:
  """入口のスクリプトを置く（root の 755）。印のない同じ名前のファイルがあれば上書きしない。"""
  path = system.path(ENTRY_PATH)
  if path.exists() and not is_managed_entry(path):
    raise ServiceError(f"{ENTRY_PATH} は tsumugium-setup が置いたものではないため、上書きしません")
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-tsumugium-setup-")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as file:
      file.write(ENTRY_SCRIPT)
    os.chmod(temp, 0o755)
    os.replace(temp, path)
  except BaseException:
    try:
      os.remove(temp)
    except FileNotFoundError:
      pass
    raise


def is_managed_entry(path) -> bool:
  try:
    with open(path, encoding="utf-8") as file:
      file.readline()
      return file.readline().rstrip("\n") == ENTRY_MARKER
  except (OSError, UnicodeDecodeError):
    return False


def remove_entry(system: System) -> bool:
  path = system.path(ENTRY_PATH)
  if path.exists() and is_managed_entry(path):
    path.unlink()
    return True
  return False
