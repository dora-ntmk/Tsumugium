"""config.dbへのすべてのSQLiteアクセスを担当する。"""

from __future__ import annotations

import sqlite3

from config import DEFAULT_SPEAKER


DEFAULTS = {
  "TextTarget": None,
  "VoiceTarget": None,
  "Speaker": None,
  "Volume": 100,
  "Speed": 100,
  "MaxChar": 50,
  "AutoJoin": False,
  "AccessNotice": False,
  "Greeting": True,
}

_TYPE_VALIDATORS = {
  "TextTarget": lambda value: value is None or (isinstance(value, int) and value > 0),
  "VoiceTarget": lambda value: value is None or (isinstance(value, int) and value > 0),
  "Speaker": lambda value: value is None or (isinstance(value, int) and value >= 0),
  "Volume": lambda value: isinstance(value, int) and 0 <= value <= 100,
  "Speed": lambda value: isinstance(value, int) and 50 <= value <= 200,
  "MaxChar": lambda value: isinstance(value, int) and 30 <= value <= 200,
  "AutoJoin": lambda value: isinstance(value, bool),
  "AccessNotice": lambda value: isinstance(value, bool),
  "Greeting": lambda value: isinstance(value, bool),
}

_BOOL_KEYS = {"AutoJoin", "AccessNotice", "Greeting"}


class GuildConfigRepository:
  def __init__(self, db_path: str = "db/config.db"):
    self._conn = sqlite3.connect(
      db_path,
      check_same_thread=False,
      timeout=30,
    )
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute(
      """
      CREATE TABLE IF NOT EXISTS guild_config (
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
    self._conn.commit()
    self._ensure_compatible_schema()

  def _ensure_compatible_schema(self) -> None:
    info = self._conn.execute("PRAGMA table_info(guild_config)").fetchall()
    column_names = {column[1] for column in info}
    if "Greeting" not in column_names:
      self._conn.execute(
        "ALTER TABLE guild_config ADD COLUMN Greeting INTEGER NOT NULL DEFAULT 1"
      )
      self._conn.commit()
      info = self._conn.execute("PRAGMA table_info(guild_config)").fetchall()

    speaker_column = next(
      (column for column in info if column[1] == "Speaker"),
      None,
    )
    if speaker_column and speaker_column[3] == 1:
      self._conn.executescript(
        """
        BEGIN;
        CREATE TABLE guild_config_new (
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
        );
        INSERT INTO guild_config_new SELECT * FROM guild_config;
        DROP TABLE guild_config;
        ALTER TABLE guild_config_new RENAME TO guild_config;
        COMMIT;
        """
      )

  def _to_python(self, key: str, value):
    if value is None:
      if key == "Speaker":
        return DEFAULT_SPEAKER
      return DEFAULTS[key]
    if key in _BOOL_KEYS:
      return bool(value)
    return value

  @staticmethod
  def _to_sql(value):
    if isinstance(value, bool):
      return int(value)
    return value

  def init_guild(self, guild_id: int) -> None:
    self._conn.execute(
      "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)",
      (str(guild_id),),
    )
    self._conn.commit()

  def get(self, guild_id: int, key: str):
    if key not in DEFAULTS:
      raise KeyError(f"不明な設定キー: {key}")
    row = self._conn.execute(
      f"SELECT {key} FROM guild_config WHERE guild_id = ?",
      (str(guild_id),),
    ).fetchone()
    if row is None:
      return DEFAULTS[key]
    return self._to_python(key, row[0])

  def set(self, guild_id: int, key: str, value) -> None:
    if key not in DEFAULTS:
      raise KeyError(f"不明な設定キー: {key}")
    if not _TYPE_VALIDATORS[key](value):
      raise ValueError(f"{key} に無効な値です: {value!r}")
    gid = str(guild_id)
    self._conn.execute(
      "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)",
      (gid,),
    )
    self._conn.execute(
      f"UPDATE guild_config SET {key} = ? WHERE guild_id = ?",
      (self._to_sql(value), gid),
    )
    self._conn.commit()

  def get_all(self, guild_id: int) -> dict:
    row = self._conn.execute(
      """
      SELECT TextTarget, VoiceTarget, Speaker, Volume, Speed, MaxChar,
             AutoJoin, AccessNotice, Greeting
      FROM guild_config
      WHERE guild_id = ?
      """,
      (str(guild_id),),
    ).fetchone()
    if row is None:
      return dict(DEFAULTS)
    keys = [
      "TextTarget",
      "VoiceTarget",
      "Speaker",
      "Volume",
      "Speed",
      "MaxChar",
      "AutoJoin",
      "AccessNotice",
      "Greeting",
    ]
    return {
      key: self._to_python(key, value)
      for key, value in zip(keys, row)
    }

  def get_raw_speaker(self, guild_id: int):
    row = self._conn.execute(
      "SELECT Speaker FROM guild_config WHERE guild_id = ?",
      (str(guild_id),),
    ).fetchone()
    return None if row is None else row[0]

  def remove_guild(self, guild_id: int) -> None:
    self._conn.execute(
      "DELETE FROM guild_config WHERE guild_id = ?",
      (str(guild_id),),
    )
    self._conn.commit()

  def reset(self, guild_id: int, key: str) -> None:
    if key not in DEFAULTS:
      raise KeyError(f"不明な設定キー: {key}")
    self._conn.execute(
      f"UPDATE guild_config SET {key} = ? WHERE guild_id = ?",
      (self._to_sql(DEFAULTS[key]), str(guild_id)),
    )
    self._conn.commit()

  def get_all_guild_ids(self) -> set[str]:
    rows = self._conn.execute("SELECT guild_id FROM guild_config").fetchall()
    return {row[0] for row in rows}

  def volume_to_vvtts(self, guild_id: int) -> float:
    return self.get(guild_id, "Volume") / 100.0

  def speed_to_vvtts(self, guild_id: int) -> float:
    return self.get(guild_id, "Speed") / 100.0

  def close(self) -> None:
    self._conn.close()
