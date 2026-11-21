"""config.db の bot_settings 表（運営者が決めるBot全体の設定）を扱うSQLite Repository。

サーバー設定と同じ config.db に置き、既存の定時バックアップで守る。値は文字列で持つ。
"""

import sqlite3
from collections.abc import Iterable


class BotSettingRepository:
  def __init__(self, db_path: str = "db/config.db"):
    self._conn = sqlite3.connect(
      db_path,
      check_same_thread=False,
      timeout=30,
    )
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute(
      """
      CREATE TABLE IF NOT EXISTS bot_settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
      )
      """
    )
    self._conn.commit()

  def get(self, key: str) -> str | None:
    row = self._conn.execute(
      "SELECT value FROM bot_settings WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else row[0]

  def get_many(self, keys: Iterable[str]) -> dict[str, str]:
    """保存されている項目だけを返す。"""
    keys = list(keys)
    if not keys:
      return {}
    placeholders = ", ".join("?" for _ in keys)
    rows = self._conn.execute(
      f"SELECT key, value FROM bot_settings WHERE key IN ({placeholders})", keys
    ).fetchall()
    return dict(rows)

  def set_many(self, values: dict[str, str]) -> None:
    """まとめて保存する（1つのトランザクション）。"""
    with self._conn:
      self._conn.executemany(
        """
        INSERT INTO bot_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [(key, str(value)) for key, value in values.items()],
      )

  def delete(self, keys: Iterable[str]) -> None:
    with self._conn:
      self._conn.executemany(
        "DELETE FROM bot_settings WHERE key = ?", [(key,) for key in keys]
      )

  def close(self) -> None:
    self._conn.close()
