"""Repository（SQLite）のテスト。DBはすべてテストごとの一時ディレクトリに作る。"""

import os
import sqlite3
import unittest
from unittest import mock

import config
from repositories.dictionary_repository import DictionaryRepository
from repositories.guild_config_repository import GuildConfigRepository
from repositories.soundboard_cache_repository import SoundboardCacheRepository
from repositories.user_config_repository import UserConfigRepository
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_OTHER_GUILD_ID,
  DUMMY_OTHER_USER_ID,
  DUMMY_SOUND_ID,
  DUMMY_TEXT_CHANNEL_ID,
  DUMMY_USER_ID,
  TempDirTestCase,
)


def _columns(db_path, table):
  with sqlite3.connect(db_path) as connection:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


class GuildConfigRepositoryTest(TempDirTestCase):
  def open_repo(self):
    repo = GuildConfigRepository(
      self.tmp_path("db", "config.db"),
      backup_dir=os.path.join(self.tmp_dir, "backup"),
    )
    self.addCleanup(repo.close)
    return repo

  def test_set_and_get(self):
    repo = self.open_repo()
    repo.set(DUMMY_GUILD_ID, "Volume", 80)
    repo.set(DUMMY_GUILD_ID, "TextTarget", DUMMY_TEXT_CHANNEL_ID)
    repo.set(DUMMY_GUILD_ID, "AutoJoin", True)
    self.assertEqual(repo.get(DUMMY_GUILD_ID, "Volume"), 80)
    self.assertEqual(repo.get(DUMMY_GUILD_ID, "TextTarget"), DUMMY_TEXT_CHANNEL_ID)
    self.assertIs(repo.get(DUMMY_GUILD_ID, "AutoJoin"), True)

  def test_invalid_values_are_rejected(self):
    repo = self.open_repo()
    cases = [
      ("Volume", 101),
      ("Volume", -1),
      ("Speed", 49),
      ("Speed", 201),
      ("SpacedSpeed", 201),
      ("AutoJoin", 1),
    ]
    for key, value in cases:
      with self.subTest(key=key, value=value):
        with self.assertRaises(ValueError):
          repo.set(DUMMY_GUILD_ID, key, value)

  def test_unknown_key_is_rejected(self):
    repo = self.open_repo()
    with self.assertRaises(KeyError):
      repo.get(DUMMY_GUILD_ID, "Language")

  def test_unset_speaker_uses_default_speaker(self):
    repo = self.open_repo()
    repo.init_guild(DUMMY_GUILD_ID)
    self.assertEqual(repo.get(DUMMY_GUILD_ID, "Speaker"), config.DEFAULT_SPEAKER)
    self.assertIsNone(repo.get_raw_speaker(DUMMY_GUILD_ID))

  def test_spaced_speed_defaults_to_75(self):
    repo = self.open_repo()
    repo.set(DUMMY_GUILD_ID, "Speed", 120)
    self.assertAlmostEqual(repo.speed_to_vvtts(DUMMY_GUILD_ID), 1.2)
    self.assertAlmostEqual(repo.speed_to_vvtts(DUMMY_GUILD_ID, spaced_out=True), 0.75)

  def test_spaced_speed_reset_returns_to_75(self):
    repo = self.open_repo()
    repo.set(DUMMY_GUILD_ID, "SpacedSpeed", 60)
    self.assertAlmostEqual(repo.speed_to_vvtts(DUMMY_GUILD_ID, spaced_out=True), 0.6)
    repo.reset(DUMMY_GUILD_ID, "SpacedSpeed")
    self.assertIsNone(repo.get(DUMMY_GUILD_ID, "SpacedSpeed"))
    self.assertAlmostEqual(repo.speed_to_vvtts(DUMMY_GUILD_ID, spaced_out=True), 0.75)

  def test_remove_guild(self):
    repo = self.open_repo()
    repo.init_guild(DUMMY_GUILD_ID)
    repo.init_guild(DUMMY_OTHER_GUILD_ID)
    repo.remove_guild(DUMMY_GUILD_ID)
    self.assertEqual(repo.get_all_guild_ids(), {str(DUMMY_OTHER_GUILD_ID)})


class GuildConfigMigrationTest(TempDirTestCase):
  """SpacedSpeed がない旧DBの補完と、移行前バックアップ。"""

  def setUp(self):
    super().setUp()
    self.db_path = self.tmp_path("db", "config.db")
    self.backup_dir = os.path.join(self.tmp_dir, "backup")
    with sqlite3.connect(self.db_path) as connection:
      connection.execute(
        """
        CREATE TABLE guild_config (
          guild_id      TEXT PRIMARY KEY,
          TextTarget    INTEGER,
          VoiceTarget   INTEGER,
          Speaker       INTEGER,
          Volume        INTEGER NOT NULL DEFAULT 100,
          Speed         INTEGER NOT NULL DEFAULT 100,
          MaxChar       INTEGER NOT NULL DEFAULT 50,
          AutoJoin      INTEGER NOT NULL DEFAULT 0,
          AccessNotice  INTEGER NOT NULL DEFAULT 0,
          Language      TEXT NOT NULL DEFAULT 'ja',
          Greeting      INTEGER NOT NULL DEFAULT 1
        )
        """
      )
      connection.execute(
        "INSERT INTO guild_config (guild_id, Speaker, Volume, Language) "
        "VALUES (?, 3, 70, 'ja')",
        (str(DUMMY_GUILD_ID),),
      )

  def test_old_db_is_backed_up_and_completed(self):
    repo = GuildConfigRepository(self.db_path, backup_dir=self.backup_dir)
    self.addCleanup(repo.close)

    self.assertIn("SpacedSpeed", _columns(self.db_path, "guild_config"))
    self.assertEqual(repo.get(DUMMY_GUILD_ID, "Speaker"), 3)
    self.assertEqual(repo.get(DUMMY_GUILD_ID, "Volume"), 70)
    self.assertIsNone(repo.get(DUMMY_GUILD_ID, "SpacedSpeed"))

    backups = os.listdir(self.backup_dir)
    self.assertEqual(len(backups), 1)
    self.assertRegex(backups[0], r"^backup_config_\d{8}_\d{6}_v3-latest\.db$")
    backup_path = os.path.join(self.backup_dir, backups[0])
    self.assertNotIn("SpacedSpeed", _columns(backup_path, "guild_config"))

  def test_access_notice_of_existing_guild_is_kept(self):
    # SpacedSpeed を足すために表を作り直しても、既存のサーバーの入退室通知（0）は変えない
    repo = GuildConfigRepository(self.db_path, backup_dir=self.backup_dir)
    self.addCleanup(repo.close)
    self.assertIs(repo.get(DUMMY_GUILD_ID, "AccessNotice"), False)
    repo.init_guild(DUMMY_OTHER_GUILD_ID)
    self.assertIs(repo.get(DUMMY_OTHER_GUILD_ID, "AccessNotice"), True)

  def test_language_column_and_value_are_kept(self):
    repo = GuildConfigRepository(self.db_path, backup_dir=self.backup_dir)
    repo.close()
    with sqlite3.connect(self.db_path) as connection:
      row = connection.execute(
        "SELECT Language FROM guild_config WHERE guild_id = ?",
        (str(DUMMY_GUILD_ID),),
      ).fetchone()
    self.assertEqual(row, ("ja",))

  def test_backup_failure_stops_without_changing_schema(self):
    with mock.patch(
        "repositories.guild_config_repository.backup_db",
        return_value=None,
    ):
      with self.assertRaises(RuntimeError):
        GuildConfigRepository(self.db_path, backup_dir=self.backup_dir)
    self.assertNotIn("SpacedSpeed", _columns(self.db_path, "guild_config"))

  def test_new_db_does_not_create_migration_backup(self):
    new_db = self.tmp_path("other", "config.db")
    repo = GuildConfigRepository(new_db, backup_dir=self.backup_dir)
    repo.close()
    self.assertFalse(os.path.exists(self.backup_dir))


class AccessNoticeDefaultTest(TempDirTestCase):
  """入退室通知は新しいサーバーでは有効にし、既存のサーバーの設定は変えない。"""

  def open_repo(self, db_path):
    repo = GuildConfigRepository(db_path, backup_dir=os.path.join(self.tmp_dir, "backup"))
    self.addCleanup(repo.close)
    return repo

  def make_current_db(self, db_path, access_notice):
    # 今までの版が作ったDB（表の既定値は AccessNotice = 0）
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as connection:
      connection.execute(
        """
        CREATE TABLE guild_config (
          guild_id      TEXT PRIMARY KEY,
          TextTarget    INTEGER,
          VoiceTarget   INTEGER,
          Speaker       INTEGER,
          Volume        INTEGER NOT NULL DEFAULT 100,
          Speed         INTEGER NOT NULL DEFAULT 100,
          SpacedSpeed   INTEGER DEFAULT NULL,
          MaxChar       INTEGER NOT NULL DEFAULT 100,
          AutoJoin      INTEGER NOT NULL DEFAULT 0,
          AccessNotice  INTEGER NOT NULL DEFAULT 0,
          Language      TEXT NOT NULL DEFAULT 'ja',
          Greeting      INTEGER NOT NULL DEFAULT 1
        )
        """
      )
      connection.execute(
        "INSERT INTO guild_config (guild_id, AccessNotice) VALUES (?, ?)",
        (str(DUMMY_GUILD_ID), access_notice),
      )

  def test_new_guild_is_enabled(self):
    repo = self.open_repo(self.tmp_path("db", "config.db"))
    self.assertIs(repo.get(DUMMY_GUILD_ID, "AccessNotice"), True)
    repo.init_guild(DUMMY_GUILD_ID)
    self.assertIs(repo.get(DUMMY_GUILD_ID, "AccessNotice"), True)
    self.assertIs(repo.get_all(DUMMY_GUILD_ID)["AccessNotice"], True)

  def test_new_guild_created_by_set_is_enabled(self):
    repo = self.open_repo(self.tmp_path("db", "config.db"))
    repo.set(DUMMY_GUILD_ID, "Volume", 80)
    self.assertIs(repo.get(DUMMY_GUILD_ID, "AccessNotice"), True)

  def test_existing_guild_keeps_its_setting(self):
    db_path = self.tmp_path("db", "config.db")
    self.make_current_db(db_path, access_notice=0)
    repo = self.open_repo(db_path)
    repo.init_guild(DUMMY_GUILD_ID)
    repo.set(DUMMY_GUILD_ID, "Volume", 80)
    self.assertIs(repo.get(DUMMY_GUILD_ID, "AccessNotice"), False)

  def test_new_guild_in_existing_db_is_enabled(self):
    # 既存のDBは表の既定値が 0 のままでも、新しいサーバーは有効になる
    db_path = self.tmp_path("db", "config.db")
    self.make_current_db(db_path, access_notice=0)
    repo = self.open_repo(db_path)
    repo.init_guild(DUMMY_OTHER_GUILD_ID)
    self.assertIs(repo.get(DUMMY_OTHER_GUILD_ID, "AccessNotice"), True)
    self.assertIs(repo.get(DUMMY_GUILD_ID, "AccessNotice"), False)

  def test_disabled_guild_stays_disabled(self):
    repo = self.open_repo(self.tmp_path("db", "config.db"))
    repo.init_guild(DUMMY_GUILD_ID)
    repo.set(DUMMY_GUILD_ID, "AccessNotice", False)
    repo.init_guild(DUMMY_GUILD_ID)
    self.assertIs(repo.get(DUMMY_GUILD_ID, "AccessNotice"), False)


class DictionaryRepositoryTest(TempDirTestCase):
  def open_repo(self):
    repo = DictionaryRepository(self.tmp_path("db", "dict.db"))
    self.addCleanup(repo.close)
    return repo

  def test_old_db_columns_are_completed(self):
    db_path = self.tmp_path("db", "old_dict.db")
    with sqlite3.connect(db_path) as connection:
      connection.execute(
        """
        CREATE TABLE dict (
          guild_id    TEXT NOT NULL,
          word        TEXT NOT NULL,
          reading     TEXT,
          sound_id    TEXT,
          is_priority INTEGER NOT NULL DEFAULT 0,
          added_at    INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
          PRIMARY KEY (guild_id, word)
        )
        """
      )
      connection.execute(
        "INSERT INTO dict (guild_id, word, sound_id) VALUES (?, 'てすと', ?)",
        (str(DUMMY_GUILD_ID), DUMMY_SOUND_ID),
      )
    repo = DictionaryRepository(db_path)
    self.addCleanup(repo.close)
    self.assertTrue({"full_match", "trigger_user_id"} <= _columns(db_path, "dict"))
    self.assertEqual(
      repo.get_sound_entries(DUMMY_GUILD_ID),
      [("てすと", DUMMY_SOUND_ID, 1, None)],
    )

  def test_add_and_delete_reading(self):
    repo = self.open_repo()
    self.assertFalse(repo.add_reading(DUMMY_GUILD_ID, "ねこ", "にゃん", is_priority=False))
    self.assertTrue(repo.add_reading(DUMMY_GUILD_ID, "ねこ", "にゃー", is_priority=False))
    self.assertEqual(repo.get_reading_entries(DUMMY_GUILD_ID), [("ねこ", "にゃー")])
    self.assertEqual(repo.delete_reading(DUMMY_GUILD_ID, "ねこ"), "にゃー")
    self.assertEqual(repo.get_reading_entries(DUMMY_GUILD_ID), [])
    self.assertIsNone(repo.delete_reading(DUMMY_GUILD_ID, "ねこ"))

  def test_reading_and_sound_can_share_a_word(self):
    repo = self.open_repo()
    repo.add_reading(DUMMY_GUILD_ID, "ねこ", "にゃん", is_priority=False)
    repo.add_sound(DUMMY_GUILD_ID, "ねこ", DUMMY_SOUND_ID, is_priority=False)
    self.assertEqual(repo.delete_sound(DUMMY_GUILD_ID, "ねこ"), DUMMY_SOUND_ID)
    self.assertEqual(repo.get_reading_entries(DUMMY_GUILD_ID), [("ねこ", "にゃん")])
    self.assertEqual(repo.get_sound_entries(DUMMY_GUILD_ID), [])

  def test_sound_options_are_stored(self):
    repo = self.open_repo()
    repo.add_sound(
      DUMMY_GUILD_ID,
      "おはよう",
      DUMMY_SOUND_ID,
      is_priority=False,
      full_match=False,
      trigger_user_id=str(DUMMY_USER_ID),
    )
    self.assertEqual(
      repo.get_sound_entries(DUMMY_GUILD_ID),
      [("おはよう", DUMMY_SOUND_ID, 0, str(DUMMY_USER_ID))],
    )

  def test_snapshot_separates_priority_normal_and_common(self):
    repo = self.open_repo()
    repo.add_reading(DUMMY_GUILD_ID, "ゆうせん", "ゆう", is_priority=True)
    repo.add_reading(DUMMY_GUILD_ID, "つうじょう", "つう", is_priority=False)
    repo.add_reading("__common__", "きょうつう", "きょう", is_priority=False)
    repo.add_reading(DUMMY_OTHER_GUILD_ID, "ほか", "ほかのサーバー", is_priority=False)
    repo.add_sound(
      DUMMY_GUILD_ID, "おと", DUMMY_SOUND_ID, is_priority=False, full_match=False
    )

    snapshot = repo.get_preprocessing_snapshot(DUMMY_GUILD_ID)
    self.assertEqual(snapshot.priority_readings, {"ゆうせん": "ゆう"})
    self.assertEqual(snapshot.normal_readings, {"つうじょう": "つう"})
    self.assertEqual(snapshot.common_readings, {"きょうつう": "きょう"})
    self.assertEqual(len(snapshot.sounds), 1)
    self.assertEqual(snapshot.sounds[0].sound_id, DUMMY_SOUND_ID)
    self.assertFalse(snapshot.sounds[0].full_match)

  def test_invalidate_sound_keeps_readings(self):
    repo = self.open_repo()
    repo.add_sound(DUMMY_GUILD_ID, "おとだけ", DUMMY_SOUND_ID, is_priority=False)
    repo.add_reading(DUMMY_GUILD_ID, "りょうほう", "よみ", is_priority=False)
    repo.add_sound(DUMMY_GUILD_ID, "りょうほう", DUMMY_SOUND_ID, is_priority=False)
    repo.invalidate_sound(DUMMY_GUILD_ID, DUMMY_SOUND_ID)
    self.assertEqual(repo.get_sound_entries(DUMMY_GUILD_ID), [])
    self.assertEqual(repo.get_reading_entries(DUMMY_GUILD_ID), [("りょうほう", "よみ")])

  def test_remove_guild_keeps_other_guilds(self):
    repo = self.open_repo()
    repo.add_reading(DUMMY_GUILD_ID, "ねこ", "にゃん", is_priority=False)
    repo.add_reading(DUMMY_OTHER_GUILD_ID, "いぬ", "わん", is_priority=False)
    repo.remove_guild(DUMMY_GUILD_ID)
    self.assertEqual(repo.get_reading_entries(DUMMY_GUILD_ID), [])
    self.assertEqual(repo.get_reading_entries(DUMMY_OTHER_GUILD_ID), [("いぬ", "わん")])


class SoundboardCacheRepositoryTest(TempDirTestCase):
  def test_synchronize_adds_and_removes(self):
    repo = SoundboardCacheRepository(self.tmp_path("db", "soundboards.db"))
    self.addCleanup(repo.close)
    repo.add(DUMMY_GUILD_ID, "1", "のこる")
    repo.add(DUMMY_GUILD_ID, "2", "きえる")
    repo.add(DUMMY_OTHER_GUILD_ID, "9", "ほかのサーバー")

    deleted = repo.synchronize(DUMMY_GUILD_ID, [(1, "のこる"), (3, "ふえる")])

    self.assertEqual(deleted, ["2"])
    self.assertEqual(
      sorted(repo.get_sounds(DUMMY_GUILD_ID)),
      [("1", "のこる"), ("3", "ふえる")],
    )
    self.assertEqual(repo.get_sounds(DUMMY_OTHER_GUILD_ID), [("9", "ほかのサーバー")])


class UserConfigRepositoryTest(TempDirTestCase):
  def open_repo(self):
    repo = UserConfigRepository(self.tmp_path("db", "users.db"))
    self.addCleanup(repo.close)
    return repo

  def test_set_get_and_clear_reading(self):
    repo = self.open_repo()
    self.assertIsNone(repo.get_user_reading(DUMMY_USER_ID))
    repo.set_user_reading(DUMMY_USER_ID, "よみかた")
    self.assertEqual(repo.get_user_reading(str(DUMMY_USER_ID)), "よみかた")
    self.assertTrue(repo.clear_user_reading(DUMMY_USER_ID))
    self.assertIsNone(repo.get_user_reading(DUMMY_USER_ID))
    self.assertFalse(repo.clear_user_reading(DUMMY_USER_ID))

  def test_invalid_input_is_rejected(self):
    repo = self.open_repo()
    with self.assertRaises(ValueError):
      repo.set_user_reading("not-a-number", "よみ")
    with self.assertRaises(ValueError):
      repo.set_user_reading(DUMMY_USER_ID, "   ")
    with self.assertRaises(ValueError):
      repo.set_user_reading(DUMMY_USER_ID, "あ" * 51)

  def test_remove_user_also_removes_personal_dictionary(self):
    repo = self.open_repo()
    repo.add_personal_reading(DUMMY_USER_ID, "ねこ", "にゃん")
    repo.add_personal_reading(DUMMY_OTHER_USER_ID, "いぬ", "わん")
    self.assertTrue(repo.remove_user(DUMMY_USER_ID))
    self.assertEqual(repo.get_personal_readings(DUMMY_USER_ID), {})
    self.assertEqual(repo.get_personal_readings(DUMMY_OTHER_USER_ID), {"いぬ": "わん"})


if __name__ == "__main__":
  unittest.main()
