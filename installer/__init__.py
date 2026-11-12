"""Tsumugium のインストーラー兼アップデーター。

標準ライブラリだけで動き、Python 3.10以上を前提にする。
初回は展開したzipの install.sh から、2回目以降は tsumugium-setup から呼ばれる。
"""

# 古いバージョンのインストーラーから新しいバージョンへ処理を引き継ぐときの形式の番号。
HANDOFF_VERSION = 1

# インストーラーを別のプロセスで動かすときの起動の仕方。
# python -m installer は、今いるディレクトリの installer/ を先に読んでしまう
# （展開したzipやリポジトリの中で実行すると、違う版が動く）。
# そこで -I（今いるディレクトリも PYTHONPATH も見ない）で起動し、使う版の場所だけを先頭に足す。
BOOTSTRAP = "import sys; sys.path.insert(0, sys.argv.pop(1)); from {module} import main; sys.exit(main())"


def python_command(python: str, app_dir, module: str, args) -> list[str]:
  """app_dir にある installer の module.main を、args を引数にして動かすコマンド。"""
  return [python, "-I", "-c", BOOTSTRAP.format(module=module), str(app_dir), *args]
