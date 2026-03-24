"""
ファイル名：server_config.py
作者：どら
説明：サーバー設定 DB 管理モジュール。
      ギルドごとの設定値を SQLite に保存・取得する ServerConfig クラスを提供する。
      設定値のバリデーション、デフォルト値管理、VOICEVOX パラメータへの変換を担う。
依存関係：なし
"""
import sqlite3
from typing import Set
from config import DEFAULT_SPEAKER

DEFAULTS = {
  "TextTarget": None,
  "VoiceTarget": None,
  "Speaker": None,  # None = Botのデフォルト（環境変数 DEFAULT_SPEAKER）を使用
  "Volume": 100,
  "Speed": 100,
  "MaxChar": 50,
  "AutoJoin": False,
  "AccessNotice": False,
  "Language": "ja",
  "Greeting": True,
}

_TYPE_VALIDATORS = {
  "TextTarget":   (lambda v: v is None or (isinstance(v, int) and v > 0)),
  "VoiceTarget":  (lambda v: v is None or (isinstance(v, int) and v > 0)),
  "Speaker":      (lambda v: v is None or (isinstance(v, int) and v >= 0)),
  "Volume":       (lambda v: isinstance(v, int) and 0 <= v <= 100),
  "Speed":        (lambda v: isinstance(v, int) and 50 <= v <= 200),
  "MaxChar":      (lambda v: isinstance(v, int) and 30 <= v <= 200),
  "AutoJoin":     (lambda v: isinstance(v, bool)),
  "AccessNotice": (lambda v: isinstance(v, bool)),
  "Language":     (lambda v: isinstance(v, str) and v in ("ja", "en", "zh-CN", "zh-TW", "ko", "hg")),
  "Greeting":     (lambda v: isinstance(v, bool)),
}

# bool型のキー一覧（SQLiteの0/1 ↔ Python bool変換用）
_BOOL_KEYS = {"AutoJoin", "AccessNotice", "Greeting"}


class ServerConfig:
  def __init__(self, db_path: str = "db/config.db"):
    self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute("""
                       CREATE TABLE IF NOT EXISTS guild_config (
                                                                   guild_id      TEXT    PRIMARY KEY,
                                                                   TextTarget    INTEGER,
                                                                   VoiceTarget   INTEGER,
                                                                   Speaker       INTEGER,
                                                                   Volume        INTEGER NOT NULL DEFAULT 100,
                                                                   Speed         INTEGER NOT NULL DEFAULT 100,
                                                                   MaxChar       INTEGER NOT NULL DEFAULT 50,
                                                                   AutoJoin      INTEGER NOT NULL DEFAULT 0,
                                                                   AccessNotice  INTEGER NOT NULL DEFAULT 0,
                                                                   Language      TEXT    NOT NULL DEFAULT 'ja',
                                                                   Greeting      INTEGER NOT NULL DEFAULT 1
                       )
                       """)
    self._conn.commit()
    # 既存DBマイグレーション
    info = self._conn.execute("PRAGMA table_info(guild_config)").fetchall()
    col_names = {c[1] for c in info}
    # Greeting列がなければ追加
    if "Greeting" not in col_names:
      self._conn.execute("ALTER TABLE guild_config ADD COLUMN Greeting INTEGER NOT NULL DEFAULT 1")
      self._conn.commit()
      info = self._conn.execute("PRAGMA table_info(guild_config)").fetchall()
    # Speaker列がNOT NULLの場合、テーブルを再作成してNULL許容に変更
    speaker_col = next((c for c in info if c[1] == "Speaker"), None)
    if speaker_col and speaker_col[3] == 1:  # notnull=1
      self._conn.executescript("""
        BEGIN;
        CREATE TABLE guild_config_new (
          guild_id      TEXT    PRIMARY KEY,
          TextTarget    INTEGER,
          VoiceTarget   INTEGER,
          Speaker       INTEGER,
          Volume        INTEGER NOT NULL DEFAULT 100,
          Speed         INTEGER NOT NULL DEFAULT 100,
          MaxChar       INTEGER NOT NULL DEFAULT 50,
          AutoJoin      INTEGER NOT NULL DEFAULT 0,
          AccessNotice  INTEGER NOT NULL DEFAULT 0,
          Language      TEXT    NOT NULL DEFAULT 'ja',
          Greeting      INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO guild_config_new SELECT * FROM guild_config;
        DROP TABLE guild_config;
        ALTER TABLE guild_config_new RENAME TO guild_config;
        COMMIT;
      """)

  def _to_python(self, key: str, value):
    """SQLiteの値をPython型に変換する。"""
    if value is None:
      if key == "Speaker":
        return DEFAULT_SPEAKER  # 環境変数のデフォルト話者を使用
      return DEFAULTS[key]
    if key in _BOOL_KEYS:
      return bool(value)
    return value

  def _to_sql(self, value):
    """Python型をSQLiteに格納できる型に変換する。"""
    if isinstance(value, bool):
      return int(value)
    return value

  def init_guild(self, guild_id: int):
    self._conn.execute(
      "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)",
      (str(guild_id),)
    )
    self._conn.commit()

  def get(self, guild_id: int, key: str):
    if key not in DEFAULTS:
      raise KeyError(f"不明な設定キー: {key}")
    cur = self._conn.execute(
      f"SELECT {key} FROM guild_config WHERE guild_id = ?",
      (str(guild_id),)
    )
    row = cur.fetchone()
    if row is None:
      return DEFAULTS[key]
    return self._to_python(key, row[0])

  def set(self, guild_id: int, key: str, value):
    if key not in DEFAULTS:
      raise KeyError(f"不明な設定キー: {key}")
    if not _TYPE_VALIDATORS[key](value):
      raise ValueError(f"{key} に無効な値です: {value!r}")
    gid = str(guild_id)
    self._conn.execute(
      "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)",
      (gid,)
    )
    self._conn.execute(
      f"UPDATE guild_config SET {key} = ? WHERE guild_id = ?",
      (self._to_sql(value), gid)
    )
    self._conn.commit()

  def get_all(self, guild_id: int) -> dict:
    cur = self._conn.execute(
      "SELECT TextTarget, VoiceTarget, Speaker, Volume, Speed, MaxChar, AutoJoin, AccessNotice, Language, Greeting"
      " FROM guild_config WHERE guild_id = ?",
      (str(guild_id),)
    )
    row = cur.fetchone()
    if row is None:
      return dict(DEFAULTS)
    keys = ["TextTarget", "VoiceTarget", "Speaker", "Volume", "Speed", "MaxChar", "AutoJoin", "AccessNotice", "Language", "Greeting"]
    result = {}
    for k, v in zip(keys, row):
      result[k] = self._to_python(k, v)
    return result

  def get_raw_speaker(self, guild_id: int):
    """DBのSpeaker値をそのまま返す。NULLの場合はNone（Botのデフォルト使用中）。"""
    cur = self._conn.execute(
      "SELECT Speaker FROM guild_config WHERE guild_id = ?",
      (str(guild_id),)
    )
    row = cur.fetchone()
    if row is None:
      return None
    return row[0]

  def remove_guild(self, guild_id: int):
    self._conn.execute(
      "DELETE FROM guild_config WHERE guild_id = ?",
      (str(guild_id),)
    )
    self._conn.commit()

  def reset(self, guild_id: int, key: str):
    if key not in DEFAULTS:
      raise KeyError(f"不明な設定キー: {key}")
    self._conn.execute(
      f"UPDATE guild_config SET {key} = ? WHERE guild_id = ?",
      (self._to_sql(DEFAULTS[key]), str(guild_id))
    )
    self._conn.commit()

  def get_all_guild_ids(self) -> Set[str]:
    cur = self._conn.execute("SELECT guild_id FROM guild_config")
    return {row[0] for row in cur.fetchall()}

  def volume_to_vvtts(self, guild_id: int) -> float:
    return self.get(guild_id, "Volume") / 100.0

  def speed_to_vvtts(self, guild_id: int) -> float:
    return self.get(guild_id, "Speed") / 100.0