"""移行ツール（scripts/migrate_legacy.py）のテスト。

旧環境は一時ディレクトリに作る。DBの中身はダミーのデータだけを使う。
"""

import importlib.util
import io
import json
import os
import sqlite3
import unittest
import unittest.mock
from pathlib import Path

from tests.support import DUMMY_GUILD_ID, DUMMY_USER_ID, TempDirTestCase
from tests.test_installer_install import SHAPED_TOKEN, InstallTestCase


ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("migrate_legacy", ROOT / "scripts" / "migrate_legacy.py")
migrate_legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_legacy)

OLD_ENV = f"""\
# ステータスメッセージ
STATUS_MESSAGE="{{guilds}}個"

# DiscordBotのトークン
DISCORD_BOT_TOKEN={SHAPED_TOKEN}

OPERATOR_USER_ID=

# データベース
SERVER_CONFIG_DB=db/config.db
DICT_DB=db/dict.db
SOUND_BOARDS_DB=db/soundboards.db
USER_CONFIG_DB=db/users.db

EMOJI_JA_JSON=db/emoji_ja.json
SPEAKERS_JSON=/home/olduser/Tsumugium/db/speakers.json
DEFAULT_SPEAKER=8
TMP_DIR=tmp
VOICEVOX_URL=http://192.0.2.10:50021
BACKUP_DIR=backup
BACKUP_TIMES=10:30
"""


def make_db(path, statements):
  path.parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(path)
  try:
    for statement in statements:
      conn.execute(statement)
    conn.commit()
  finally:
    conn.close()


def make_old_environment(base: Path, *, version="v4.1.0.69", env=OLD_ENV):
  base.mkdir(parents=True, exist_ok=True)
  (base / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
  (base / "build-info.json").write_text(json.dumps({"version": "v4.2.0.70"}), encoding="utf-8")
  old = base / "old"
  (old / "db").mkdir(parents=True, exist_ok=True)
  (old / "config.py").write_text(f'VERSION      = "{version}"\n', encoding="utf-8")
  (old / ".env").write_text(env, encoding="utf-8")
  make_db(old / "db" / "config.db", [
    "CREATE TABLE guild_config (guild_id TEXT PRIMARY KEY, Volume INTEGER)",
    f"INSERT INTO guild_config VALUES ('{DUMMY_GUILD_ID}', 100)",
  ])
  make_db(old / "db" / "dict.db", [
    "CREATE TABLE dict (guild_id TEXT, word TEXT, reading TEXT)",
    f"INSERT INTO dict VALUES ('{DUMMY_GUILD_ID}', 'てすと', 'テスト')",
    f"INSERT INTO dict VALUES ('{DUMMY_GUILD_ID}', 'だみー', 'ダミー')",
  ])
  make_db(old / "db" / "users.db", [
    "CREATE TABLE user_settings (user_id TEXT PRIMARY KEY, reading TEXT)",
    f"INSERT INTO user_settings VALUES ('{DUMMY_USER_ID}', 'だみー')",
  ])
  (old / "backup").mkdir()
  (old / "backup" / "backup_config_20260101_120000.db").write_bytes(b"dummy")
  return old


def snapshot(directory: Path):
  return {
    str(path.relative_to(directory)): path.read_bytes()
    for path in sorted(directory.rglob("*")) if path.is_file()
  }


class MigrateTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.base = Path(self.tmp_dir) / "tsumugium-v4.2.0.70"
    self.old = make_old_environment(self.base)
    self.output = []

  def migrate(self):
    return migrate_legacy.migrate(self.base, out=self.output.append)

  def text(self):
    return "\n".join(self.output)

  def test_env_is_converted(self):
    self.assertEqual(self.migrate(), 0)
    env = (self.base / ".env").read_text(encoding="utf-8")
    self.assertEqual(oct(os.stat(self.base / ".env").st_mode & 0o777), "0o600")
    self.assertIn(f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}", env.splitlines())
    self.assertIn("BACKUP_TIMES=10:30", env.splitlines())
    self.assertIn("EMOJI_JA_JSON=db/emoji_ja.json", env.splitlines())
    for key in ("SERVER_CONFIG_DB", "DICT_DB", "TMP_DIR", "BACKUP_DIR", "VOICEVOX_URL", "SPEAKERS_JSON"):
      with self.subTest(key=key):
        self.assertFalse(any(line.startswith(f"{key}=") for line in env.splitlines()))
        self.assertTrue(any(line.startswith(f"# {key}=") for line in env.splitlines()))
    self.assertIn("# [移行ツール]", env)
    self.assertIn("# ステータスメッセージ", env)
    self.assertIn("192.0.2.10:50021 と入力してください", self.text())

  def test_databases_and_backups_are_copied(self):
    self.migrate()
    data = self.base / "data"
    conn = sqlite3.connect(data / "db" / "dict.db")
    self.assertEqual(conn.execute("SELECT COUNT(*) FROM dict").fetchone()[0], 2)
    conn.close()
    self.assertTrue((data / "db" / "config.db").is_file())
    self.assertTrue((data / "db" / "users.db").is_file())
    self.assertFalse((data / "db" / "soundboards.db").exists())
    self.assertEqual(
      (data / "backup" / "imported" / "backup_config_20260101_120000.db").read_bytes(), b"dummy"
    )
    self.assertIn("dict: 2件", self.text())
    self.assertIn("SOUND_BOARDS_DB のDBが見つかりません", self.text())

  def test_absolute_database_path(self):
    elsewhere = Path(self.tmp_dir) / "elsewhere" / "dict.db"
    make_db(elsewhere, ["CREATE TABLE dict (word TEXT)", "INSERT INTO dict VALUES ('x')"])
    env = OLD_ENV.replace("DICT_DB=db/dict.db", f"DICT_DB={elsewhere}")
    (self.old / ".env").write_text(env, encoding="utf-8")
    self.migrate()
    self.assertIn(str(elsewhere), self.text())
    self.assertIn("dict: 1件", self.text())

  def test_old_directory_is_not_changed(self):
    before = snapshot(self.old)
    self.migrate()
    self.migrate()
    self.assertEqual(snapshot(self.old), before)

  def test_running_twice_rebuilds_and_keeps_previous_env(self):
    self.migrate()
    (self.base / "data" / "db" / "stray.db").write_bytes(b"x")
    self.migrate()
    self.assertFalse((self.base / "data" / "db" / "stray.db").exists())
    self.assertTrue((self.base / ".env.migrate-bak").is_file())

  def test_broken_database_stops(self):
    (self.old / "db" / "config.db").write_bytes(b"this is not sqlite" * 100)
    with self.assertRaisesRegex(migrate_legacy.MigrationError, "config.db"):
      self.migrate()
    self.assertFalse((self.base / ".env").exists())

  def test_missing_required_database_stops(self):
    (self.old / "db" / "dict.db").unlink()
    with self.assertRaisesRegex(migrate_legacy.MigrationError, "DICT_DB"):
      self.migrate()
    self.assertFalse((self.base / "data").exists())

  def test_invalid_token_stops(self):
    (self.old / ".env").write_text(
      OLD_ENV.replace(f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}", "DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN"),
      encoding="utf-8",
    )
    with self.assertRaisesRegex(migrate_legacy.MigrationError, "テンプレートのまま"):
      self.migrate()

  def test_old_major_version_stops(self):
    (self.old / "config.py").write_text('VERSION = "v3.5.0.60"\n', encoding="utf-8")
    with self.assertRaisesRegex(migrate_legacy.MigrationError, "v4 以上"):
      self.migrate()

  def test_wrong_place(self):
    (self.base / "install.sh").unlink()
    with self.assertRaisesRegex(migrate_legacy.MigrationError, "install.sh と同じ場所"):
      self.migrate()

  def test_main_reports_errors(self):
    (self.base / "old" / ".env").unlink()
    stderr = io.StringIO()
    with unittest.mock.patch("sys.stderr", stderr), unittest.mock.patch("sys.stdout", io.StringIO()):
      self.assertEqual(migrate_legacy.main(["--base", str(self.base)]), 1)
    self.assertIn("どらさんに送ってください", stderr.getvalue())


class MigrateThenInstallTest(InstallTestCase):
  """移行ツールが作った .env と data/ を、インストーラーがそのまま受け付けることを確かめる。"""

  def test_installer_accepts_migrated_files(self):
    os.remove(self.source / ".env")
    make_old_environment(self.source)
    migrate_legacy.migrate(self.source, out=lambda message: None)
    self.assertEqual(self.run_install(["--yes"]), 0, self.output())
    data = self.system.path("/opt/tsumugium/data")
    self.assertTrue((data / "db" / "dict.db").is_file())
    self.assertTrue((data / "backup" / "imported" / "backup_config_20260101_120000.db").is_file())
    self.assertFalse(self.system.path("/opt/tsumugium/app/old").exists())
    self.assertNotIn("使われません", self.output())
    self.assertNotIn(SHAPED_TOKEN, self.output())


if __name__ == "__main__":
  unittest.main()
