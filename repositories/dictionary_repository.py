"""dict.dbへのすべてのSQLiteアクセスを担当する。"""

import sqlite3
from typing import Optional

from models.dictionary_snapshot import DictionarySnapshot, SoundEntry
from services.error_notification_service import ensure_error_notifier


class DictionaryRepository:
  def __init__(self, db_path: str, error_notifier=None):
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._conn = sqlite3.connect(
      db_path,
      check_same_thread=False,
      timeout=30,
    )
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute(
      """
      CREATE TABLE IF NOT EXISTS dict (
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
    self._conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_dict_guild ON dict (guild_id)"
    )
    columns = {
      row[1]
      for row in self._conn.execute("PRAGMA table_info(dict)").fetchall()
    }
    if "full_match" not in columns:
      self._conn.execute(
        "ALTER TABLE dict ADD COLUMN full_match INTEGER NOT NULL DEFAULT 1"
      )
    if "trigger_user_id" not in columns:
      self._conn.execute(
        "ALTER TABLE dict ADD COLUMN trigger_user_id TEXT DEFAULT NULL"
      )
    self._conn.commit()

  def remove_guild(self, guild_id: int) -> None:
    try:
      self._conn.execute(
        "DELETE FROM dict WHERE guild_id = ?",
        (str(guild_id),),
      )
      self._conn.commit()
    except sqlite3.Error as e:
      self.error_notifier.report(f'辞書削除失敗 guild_id={guild_id}: {e}')

  def add_reading(
      self,
      guild_id: int,
      word: str,
      reading: str,
      *,
      is_priority: bool,
  ) -> bool:
    gid = str(guild_id)
    row = self._conn.execute(
      "SELECT reading FROM dict WHERE guild_id = ? AND word = ?",
      (gid, word),
    ).fetchone()
    overwrite = row is not None and row[0] is not None
    self._conn.execute(
      """
      INSERT INTO dict (guild_id, word, reading, sound_id, is_priority, added_at)
      VALUES (?, ?, ?, NULL, ?, strftime('%s', 'now'))
      ON CONFLICT(guild_id, word) DO UPDATE SET
        reading     = excluded.reading,
        is_priority = excluded.is_priority,
        added_at    = excluded.added_at
      """,
      (gid, word, reading, int(is_priority)),
    )
    self._conn.commit()
    return overwrite

  def delete_reading(self, guild_id: int, word: str) -> Optional[str]:
    gid = str(guild_id)
    row = self._conn.execute(
      "SELECT reading, sound_id FROM dict WHERE guild_id = ? AND word = ?",
      (gid, word),
    ).fetchone()
    if row is None or row[0] is None:
      return None
    reading, sound_id = row
    if sound_id is not None:
      self._conn.execute(
        "UPDATE dict SET reading = NULL WHERE guild_id = ? AND word = ?",
        (gid, word),
      )
    else:
      self._conn.execute(
        "DELETE FROM dict WHERE guild_id = ? AND word = ?",
        (gid, word),
      )
    self._conn.commit()
    return reading

  def get_reading_entries(
      self,
      guild_id: int,
  ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    rows = self._conn.execute(
      """
      SELECT word, reading, is_priority
      FROM dict
      WHERE guild_id = ? AND reading IS NOT NULL
      ORDER BY added_at DESC
      """,
      (str(guild_id),),
    ).fetchall()
    normal = []
    priority = []
    for word, reading, is_priority in rows:
      target = priority if is_priority else normal
      target.append((word, reading))
    return normal, priority

  def add_sound(
      self,
      guild_id: int,
      word: str,
      sound_id: str,
      *,
      is_priority: bool,
      full_match: bool = True,
      trigger_user_id: Optional[str] = None,
  ) -> bool:
    gid = str(guild_id)
    row = self._conn.execute(
      "SELECT sound_id FROM dict WHERE guild_id = ? AND word = ?",
      (gid, word),
    ).fetchone()
    overwrite = row is not None and row[0] is not None
    self._conn.execute(
      """
      INSERT INTO dict (
        guild_id, word, sound_id, reading, is_priority,
        full_match, trigger_user_id, added_at
      )
      VALUES (?, ?, ?, NULL, ?, ?, ?, strftime('%s', 'now'))
      ON CONFLICT(guild_id, word) DO UPDATE SET
        sound_id        = excluded.sound_id,
        is_priority     = excluded.is_priority,
        full_match      = excluded.full_match,
        trigger_user_id = excluded.trigger_user_id,
        added_at        = excluded.added_at
      """,
      (
        gid,
        word,
        sound_id,
        int(is_priority),
        int(full_match),
        trigger_user_id,
      ),
    )
    self._conn.commit()
    return overwrite

  def delete_sound(self, guild_id: int, word: str) -> Optional[str]:
    gid = str(guild_id)
    row = self._conn.execute(
      "SELECT sound_id, reading FROM dict WHERE guild_id = ? AND word = ?",
      (gid, word),
    ).fetchone()
    if row is None or row[0] is None:
      return None
    sound_id, reading = row
    if reading is not None:
      self._conn.execute(
        "UPDATE dict SET sound_id = NULL WHERE guild_id = ? AND word = ?",
        (gid, word),
      )
    else:
      self._conn.execute(
        "DELETE FROM dict WHERE guild_id = ? AND word = ?",
        (gid, word),
      )
    self._conn.commit()
    return sound_id

  def get_sound_entries(self, guild_id: int) -> tuple[
      list[tuple[str, str, int, Optional[str]]],
      list[tuple[str, str, int, Optional[str]]],
  ]:
    rows = self._conn.execute(
      """
      SELECT word, sound_id, is_priority, full_match, trigger_user_id
      FROM dict
      WHERE guild_id = ? AND sound_id IS NOT NULL
      ORDER BY added_at DESC
      """,
      (str(guild_id),),
    ).fetchall()
    normal = []
    priority = []
    for word, sound_id, is_priority, full_match, user_id in rows:
      target = priority if is_priority else normal
      target.append((word, sound_id, full_match, user_id))
    return normal, priority

  def invalidate_sound(self, guild_id: int | str, sound_id: str) -> None:
    gid = str(guild_id)
    sid = str(sound_id)
    self._conn.execute(
      "DELETE FROM dict WHERE guild_id = ? AND sound_id = ? AND reading IS NULL",
      (gid, sid),
    )
    self._conn.execute(
      "UPDATE dict SET sound_id = NULL WHERE guild_id = ? AND sound_id = ? AND reading IS NOT NULL",
      (gid, sid),
    )
    self._conn.commit()

  def delete_entry(self, guild_id: int, word: str) -> None:
    self._conn.execute(
      "DELETE FROM dict WHERE guild_id = ? AND word = ?",
      (str(guild_id), word),
    )
    self._conn.commit()

  def get_preprocessing_snapshot(self, guild_id: int) -> DictionarySnapshot:
    gid = str(guild_id)
    sound_rows = self._conn.execute(
      """
      SELECT word, sound_id, full_match, trigger_user_id
      FROM dict
      WHERE guild_id = ? AND sound_id IS NOT NULL
      """,
      (gid,),
    ).fetchall()
    reading_rows = self._conn.execute(
      """
      SELECT word, reading, is_priority
      FROM dict
      WHERE guild_id = ? AND reading IS NOT NULL
      ORDER BY added_at DESC
      """,
      (gid,),
    ).fetchall()
    common_rows = self._conn.execute(
      """
      SELECT word, reading
      FROM dict
      WHERE guild_id = '__common__' AND reading IS NOT NULL
      ORDER BY added_at DESC
      """
    ).fetchall()

    priority_readings = {}
    normal_readings = {}
    for word, reading, is_priority in reading_rows:
      target = priority_readings if is_priority else normal_readings
      target[word] = reading

    return DictionarySnapshot(
      sounds=tuple(
        SoundEntry(
          word=word,
          sound_id=sound_id,
          full_match=bool(full_match),
          trigger_user_id=trigger_user_id,
        )
        for word, sound_id, full_match, trigger_user_id in sound_rows
      ),
      priority_readings=priority_readings,
      normal_readings=normal_readings,
      common_readings=dict(common_rows),
    )

  def close(self) -> None:
    self._conn.close()
