"""ユーザー単位の読み方と個人辞書を管理するSQLite Repository。"""

import sqlite3


class UserConfigRepository:
  def __init__(self, db_path: str = "db/users.db"):
    self._conn = sqlite3.connect(
      db_path,
      check_same_thread=False,
      timeout=30,
    )
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute("PRAGMA foreign_keys=ON")
    self._conn.executescript(
      """
      CREATE TABLE IF NOT EXISTS user_settings (
        user_id TEXT PRIMARY KEY,
        reading TEXT DEFAULT NULL
      );

      CREATE TABLE IF NOT EXISTS user_dictionary (
        user_id  TEXT NOT NULL,
        word     TEXT NOT NULL,
        reading  TEXT NOT NULL,
        added_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
        PRIMARY KEY (user_id, word),
        FOREIGN KEY (user_id) REFERENCES user_settings(user_id)
          ON DELETE CASCADE
      );

      CREATE INDEX IF NOT EXISTS idx_user_dictionary_user
        ON user_dictionary (user_id);
      """
    )
    self._conn.commit()

  @staticmethod
  def _normalize_user_id(user_id: int | str) -> str:
    value = str(user_id)
    if not value.isdigit() or int(value) <= 0:
      raise ValueError(f"無効なDiscordユーザーIDです: {user_id!r}")
    return str(int(value))

  @staticmethod
  def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
      raise ValueError(f"{field_name}は空にできません")
    if len(value) > 50:
      raise ValueError(f"{field_name}は50文字以内で指定してください")

  def _ensure_user(self, user_id: str) -> None:
    self._conn.execute(
      "INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)",
      (user_id,),
    )

  def get_user_reading(self, user_id: int | str) -> str | None:
    uid = self._normalize_user_id(user_id)
    row = self._conn.execute(
      "SELECT reading FROM user_settings WHERE user_id = ?",
      (uid,),
    ).fetchone()
    return None if row is None else row[0]

  def set_user_reading(self, user_id: int | str, reading: str) -> None:
    uid = self._normalize_user_id(user_id)
    self._validate_text(reading, "reading")
    self._conn.execute(
      """
      INSERT INTO user_settings (user_id, reading)
      VALUES (?, ?)
      ON CONFLICT(user_id) DO UPDATE SET reading = excluded.reading
      """,
      (uid, reading),
    )
    self._conn.commit()

  def clear_user_reading(self, user_id: int | str) -> bool:
    uid = self._normalize_user_id(user_id)
    cursor = self._conn.execute(
      "UPDATE user_settings SET reading = NULL WHERE user_id = ? AND reading IS NOT NULL",
      (uid,),
    )
    self._conn.commit()
    return cursor.rowcount > 0

  def add_personal_reading(
      self,
      user_id: int | str,
      word: str,
      reading: str,
  ) -> bool:
    uid = self._normalize_user_id(user_id)
    self._validate_text(word, "word")
    self._validate_text(reading, "reading")
    existing = self._conn.execute(
      "SELECT 1 FROM user_dictionary WHERE user_id = ? AND word = ?",
      (uid, word),
    ).fetchone()
    self._ensure_user(uid)
    self._conn.execute(
      """
      INSERT INTO user_dictionary (user_id, word, reading, added_at)
      VALUES (?, ?, ?, strftime('%s', 'now'))
      ON CONFLICT(user_id, word) DO UPDATE SET
        reading = excluded.reading,
        added_at = excluded.added_at
      """,
      (uid, word, reading),
    )
    self._conn.commit()
    return existing is not None

  def delete_personal_reading(
      self,
      user_id: int | str,
      word: str,
  ) -> str | None:
    uid = self._normalize_user_id(user_id)
    row = self._conn.execute(
      "SELECT reading FROM user_dictionary WHERE user_id = ? AND word = ?",
      (uid, word),
    ).fetchone()
    if row is None:
      return None
    self._conn.execute(
      "DELETE FROM user_dictionary WHERE user_id = ? AND word = ?",
      (uid, word),
    )
    self._conn.commit()
    return row[0]

  def get_personal_readings(self, user_id: int | str) -> dict[str, str]:
    uid = self._normalize_user_id(user_id)
    rows = self._conn.execute(
      """
      SELECT word, reading
      FROM user_dictionary
      WHERE user_id = ?
      ORDER BY added_at DESC, word ASC
      """,
      (uid,),
    ).fetchall()
    return dict(rows)

  def remove_user(self, user_id: int | str) -> bool:
    uid = self._normalize_user_id(user_id)
    cursor = self._conn.execute(
      "DELETE FROM user_settings WHERE user_id = ?",
      (uid,),
    )
    self._conn.commit()
    return cursor.rowcount > 0

  def close(self) -> None:
    self._conn.close()
