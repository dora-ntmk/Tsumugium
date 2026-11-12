"""テストの土台が動くことと、主要モジュールを読み込めることを確かめる。"""

import importlib
import os
import re
import sqlite3
import unittest

from tests.support import TempDirTestCase


# main.py は読み込むとBotを起動するので対象外にする。
MODULES = [
  "config",
  "swap",
  "word_dict",
  "sound_dict",
  "setting",
  "dict_view",
  "backup",
  "clients.voicevox_client",
  "clients.discord_soundboard_client",
  "clients.managed_discord_client",
  "cogs.connection_cog",
  "cogs.general_cog",
  "cogs.lifecycle_cog",
  "services.ready_state_service",
  "cogs.playback_cog",
  "cogs.user_cog",
  "models.audio_item",
  "models.dictionary_snapshot",
  "models.guild_session",
  "models.preprocess_result",
  "presentation.embeds",
  "presentation.error_handler",
  "repositories.dictionary_repository",
  "repositories.guild_config_repository",
  "repositories.soundboard_cache_repository",
  "repositories.user_config_repository",
  "services.connection_service",
  "services.error_notification_service",
  "services.message_service",
  "services.speaker_service",
  "services.speech_service",
  "services.status_service",
  "services.user_reading_service",
  "services.voice_service",
]

# AGENTS.md「バージョン表記」: vX.X.X.XX、ベータは vX.X.X.XX (beta X)
VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+\.\d+( \(beta [1-9]\d*\))?$")
LAST_UPDATED_PATTERN = re.compile(r"^\d{4}/\d{2}/\d{2}$")


class ImportTest(unittest.TestCase):
  def test_modules_can_be_imported(self):
    for name in MODULES:
      with self.subTest(module=name):
        importlib.import_module(name)


class VersionTest(unittest.TestCase):
  def test_version_follows_agents_rule(self):
    config = importlib.import_module("config")
    self.assertRegex(config.VERSION, VERSION_PATTERN)

  def test_last_updated_format(self):
    config = importlib.import_module("config")
    self.assertRegex(config.LAST_UPDATED, LAST_UPDATED_PATTERN)

  def test_version_pattern_examples(self):
    for valid in ("v4.1.0.69", "v4.2.0.70 (beta 1)", "v4.2.0.70 (beta 12)"):
      with self.subTest(version=valid):
        self.assertRegex(valid, VERSION_PATTERN)
    for invalid in ("v4.2.0", "4.2.0.70", "v4.2.0.70 (beta 0)", "v4.2.0.70-beta.1"):
      with self.subTest(version=invalid):
        self.assertNotRegex(invalid, VERSION_PATTERN)


class SupportTest(TempDirTestCase):
  def test_tmp_path_is_inside_temporary_directory(self):
    path = self.tmp_path("db", "config.db")
    self.assertTrue(path.startswith(self.tmp_dir))
    self.assertTrue(os.path.isdir(os.path.dirname(path)))

  def test_sqlite_db_can_be_created_in_temporary_directory(self):
    path = self.tmp_path("db", "check.db")
    with sqlite3.connect(path) as connection:
      connection.execute("CREATE TABLE t (x INTEGER)")
    self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
  unittest.main()
