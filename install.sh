#!/bin/sh
# Tsumugium のインストーラーを起動する。
# Python 3.10 以上があるかを確かめ、同じディレクトリの installer/ を呼ぶだけにする。
#
#   sudo ./install.sh install

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

python=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    python=$(command -v "$candidate")
    break
  fi
done

if [ -z "$python" ]; then
  echo "エラー: Python 3 が見つかりません。" >&2
  echo "Ubuntu/Debian では次のコマンドで入れてから、もう一度実行してください。" >&2
  echo "  sudo apt-get install -y python3" >&2
  exit 1
fi

if ! "$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  version=$("$python" -c 'import platform; print(platform.python_version())')
  echo "エラー: Python 3.10 以上が必要です（見つかったのは $version）。" >&2
  echo "Ubuntu 22.04 以上、または Debian 12 以上で実行してください。" >&2
  exit 1
fi

# python -m installer は今いるディレクトリの installer/ を先に読むので使わない。
# -I で今いるディレクトリと PYTHONPATH を見ないようにし、このディレクトリだけを先頭に足す。
exec "$python" -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from installer.cli import main; sys.exit(main())' "$script_dir" "$@"
