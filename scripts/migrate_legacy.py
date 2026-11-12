"""旧環境から、インストーラーで入れるための .env と data/ を作る移行ツール。

配布用のzipには入れず、移行する人に直接渡す。標準ライブラリだけで動く。

使い方:
  1. 配布用のzipを展開したディレクトリ（install.sh と同じ場所）に、
     旧環境のディレクトリを丸ごとコピーした old/ と、このファイルを置く
       cp -a /home/<ユーザー>/Tsumugium old
  2. python3 migrate_legacy.py
  3. 表示に従って sudo ./install.sh install を実行する

old/ の中身は変えない。何度実行してもよい（.env と data/ を作り直す）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path


TOKEN_KEY = "DISCORD_BOT_TOKEN"
TOKEN_PLACEHOLDER = "YOUR_DISCORD_BOT_TOKEN"
DEFAULT_VOICEVOX_URL = "http://127.0.0.1:50021"
MIN_MAJOR_VERSION = 4

# DBの項目名 → (旧環境の既定の場所, 新しい環境でのファイル名, 件数を数える表, 必須か)
DATABASES = {
  "SERVER_CONFIG_DB": ("db/config.db", "config.db", "guild_config", True),
  "DICT_DB": ("db/dict.db", "dict.db", "dict", True),
  "SOUND_BOARDS_DB": ("db/soundboards.db", "soundboards.db", "soundboards", False),
  "USER_CONFIG_DB": ("db/users.db", "users.db", "user_settings", False),
}
# インストーラーが systemd の unit で渡すので、.env では無効にする項目
MANAGED_KEYS = (
  *DATABASES, "TMP_DIR", "BACKUP_DIR", "VOICEVOX_URL", "UPDATE_STATE_DIR", "TSUMUGIUM_ENV_FILE",
)
# Botに付けて配るデータ。既定の場所でなければ、新しい環境のBotから読めないので無効にする
BUNDLED_DATA = {
  "EMOJI_JA_JSON": "db/emoji_ja.json",
  "SPEAKERS_JSON": "db/speakers.json",
}
MIGRATION_NOTE = "# [移行ツール] {reason}。元の行を無効にして残しています"

_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_VERSION_RE = re.compile(r'^VERSION\s*=\s*"([^"]+)"', re.MULTILINE)
_MAJOR_RE = re.compile(r"^v(\d+)\.")


class MigrationError(RuntimeError):
  """移行を続けられないことを表す。"""


# .env の読み書き

def parse_value(raw: str) -> str:
  raw = raw.strip()
  if raw[:1] in ("'", '"'):
    end = raw.find(raw[0], 1)
    if end > 0:
      return raw[1:end]
    return raw
  match = re.search(r"\s#", raw)
  return (raw[:match.start()] if match else raw).strip()


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
  lines = path.read_text(encoding="utf-8").splitlines()
  values = {}
  for line in lines:
    if line.lstrip().startswith("#"):
      continue
    match = _ASSIGN_RE.match(line)
    if match:
      values[match.group(1)] = parse_value(match.group(2))
  return lines, values


def check_token(value: str | None) -> str | None:
  if not value:
    return f"{TOKEN_KEY} が空欄です"
  if value == TOKEN_PLACEHOLDER:
    return f"{TOKEN_KEY} がテンプレートのまま（{TOKEN_PLACEHOLDER}）です"
  if not _TOKEN_RE.fullmatch(value):
    return f"{TOKEN_KEY} がDiscordのトークンの形になっていません"
  return None


def convert_env(lines: list[str]) -> tuple[list[str], list[str]]:
  """新しい .env の行と、無効にした項目の名前を返す。"""
  result, disabled = [], []
  for line in lines:
    match = None if line.lstrip().startswith("#") else _ASSIGN_RE.match(line)
    if match:
      key, value = match.group(1), parse_value(match.group(2))
      reason = None
      if key in MANAGED_KEYS:
        reason = "インストーラーが systemd の unit で渡すので、ここに書いても使われません"
      elif key in BUNDLED_DATA and value and value != BUNDLED_DATA[key]:
        reason = "新しい環境のBotからは読めない場所を指しているので、Botに付属のファイルを使います"
      if reason:
        result.append(MIGRATION_NOTE.format(reason=reason))
        result.append("# " + line)
        disabled.append(key)
        continue
    result.append(line)
  return result, disabled


# DB

def resolve(old: Path, value: str) -> Path:
  path = Path(value)
  return path if path.is_absolute() else old / path


def copy_database(source: Path, destination: Path) -> None:
  """SQLite のバックアップ機能でコピーし、壊れていないかを確かめる。"""
  destination.parent.mkdir(parents=True, exist_ok=True)
  source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
  try:
    target_conn = sqlite3.connect(destination)
    try:
      source_conn.backup(target_conn)
      result = target_conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
      target_conn.close()
  except sqlite3.DatabaseError as error:
    raise MigrationError(f"{source} を読めません（壊れているか、SQLite のDBではありません）: {error}") from None
  finally:
    source_conn.close()
  if result != "ok":
    raise MigrationError(f"{source} が壊れています（integrity_check: {result}）")


def count_rows(path: Path, table: str) -> str:
  try:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
      return str(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
      conn.close()
  except sqlite3.DatabaseError:
    return "?"


# 流れ

def old_version(old: Path) -> str:
  config = old / "config.py"
  if not config.is_file():
    raise MigrationError(f"{config} がありません。old/ に旧環境のディレクトリを丸ごとコピーしてください")
  match = _VERSION_RE.search(config.read_text(encoding="utf-8"))
  if match is None:
    raise MigrationError(f"{config} からバージョンを読めません")
  version = match.group(1)
  major = _MAJOR_RE.match(version)
  if major is None or int(major.group(1)) < MIN_MAJOR_VERSION:
    raise MigrationError(
      f"旧環境のバージョン（{version}）は、この移行ツールでは扱えません（v{MIN_MAJOR_VERSION} 以上が対象です）。"
      "どらさんに相談してください"
    )
  return version


def migrate(base: Path, out=print) -> int:
  base = Path(base)
  if not (base / "install.sh").is_file() or not (base / "build-info.json").is_file():
    raise MigrationError(
      f"{base} は配布用のzipを展開したディレクトリではありません。"
      "このファイルを install.sh と同じ場所に置いて実行してください"
    )
  old = base / "old"
  if not old.is_dir():
    raise MigrationError(
      f"{old} がありません。旧環境のディレクトリを丸ごとコピーしてください（例: cp -a /home/<ユーザー>/Tsumugium old）"
    )
  version = old_version(old)
  out(f"旧環境: {old}（{version}）")

  old_env = old / ".env"
  if not old_env.is_file():
    raise MigrationError(f"{old_env} がありません")
  lines, values = read_env(old_env)
  problem = check_token(values.get(TOKEN_KEY))
  if problem:
    raise MigrationError(f"{old_env} の {problem}")

  # 先にDBを確かめてから、.env と data/ を作り直す
  sources = {}
  for key, (default, name, table, required) in DATABASES.items():
    path = resolve(old, values.get(key) or default)
    if path.is_file():
      sources[key] = path
    elif required:
      raise MigrationError(f"{key} のDBが見つかりません: {path}")
    else:
      out(f"注意: {key} のDBが見つかりません（{path}）。Botが起動するときに新しく作ります")

  data = base / "data"
  if data.exists():
    shutil.rmtree(data)
  db_dir = data / "db"
  db_dir.mkdir(parents=True)
  (data / "backup").mkdir()
  out("")
  out("DB:")
  for key, path in sources.items():
    _, name, table, _ = DATABASES[key]
    destination = db_dir / name
    copy_database(path, destination)
    out(f"  {path} → data/db/{name}（{table}: {count_rows(destination, table)}件）")

  backup_dir = resolve(old, values.get("BACKUP_DIR") or "backup")
  if backup_dir.is_dir():
    shutil.copytree(backup_dir, data / "backup" / "imported")
    count = sum(1 for path in (data / "backup" / "imported").rglob("*") if path.is_file())
    out(f"今までのバックアップ: {backup_dir} → data/backup/imported/（{count}ファイル）")
  else:
    out(f"今までのバックアップ: {backup_dir} がないため、コピーしません")

  new_lines, disabled = convert_env(lines)
  env_path = base / ".env"
  if env_path.exists():
    os.replace(env_path, base / ".env.migrate-bak")
    out("前回作った .env は .env.migrate-bak に残しました")
  fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
  with os.fdopen(fd, "w", encoding="utf-8") as file:
    file.write("\n".join(new_lines) + "\n")
  os.chmod(env_path, 0o600)
  out("")
  out(f".env を作りました（権限 600）: {env_path}")
  if disabled:
    out("  無効にした行: " + ", ".join(disabled))

  voicevox_url = values.get("VOICEVOX_URL")
  out("")
  out("移行の準備ができました。old/ の中身は変えていません。")
  out("次は、同じディレクトリで sudo ./install.sh install を実行してください。")
  if voicevox_url and voicevox_url.rstrip("/") != DEFAULT_VOICEVOX_URL:
    out(f"VOICEVOXのURLを聞かれたら、{voicevox_url} と入力してください（旧環境の設定）。")
  return 0


def main(argv=None) -> int:
  parser = argparse.ArgumentParser(description="旧環境から .env と data/ を作る移行ツール")
  parser.add_argument(
    "--base",
    default=str(Path(__file__).resolve().parent),
    help="配布用のzipを展開したディレクトリ（既定はこのファイルのある場所）",
  )
  args = parser.parse_args(argv)
  try:
    return migrate(Path(args.base))
  except MigrationError as error:
    print(f"エラー: {error}", file=sys.stderr)
    print("old/ の中身は変えていません。表示された内容をどらさんに送ってください。", file=sys.stderr)
    return 1


if __name__ == "__main__":
  sys.exit(main())
