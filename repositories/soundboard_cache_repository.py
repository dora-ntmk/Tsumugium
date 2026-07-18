"""soundboards.dbへのすべてのSQLiteアクセスを担当する。"""

import sqlite3


class SoundboardCacheRepository:
  def __init__(self, db_path: str):
    self._conn = sqlite3.connect(
      db_path,
      check_same_thread=False,
      timeout=30,
    )
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute(
      """
      CREATE TABLE IF NOT EXISTS soundboards (
        guild_id TEXT NOT NULL,
        sound_id TEXT NOT NULL,
        name     TEXT NOT NULL,
        PRIMARY KEY (guild_id, sound_id)
      )
      """
    )
    self._conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_soundboards_guild ON soundboards (guild_id)"
    )
    self._conn.commit()

  def remove_guild(self, guild_id: int) -> None:
    try:
      self._conn.execute(
        "DELETE FROM soundboards WHERE guild_id = ?",
        (str(guild_id),),
      )
      self._conn.commit()
    except sqlite3.Error as e:
      print(f'サウンドボード一覧削除失敗 guild_id={guild_id}: {e}')

  def add(self, guild_id: int | str, sound_id: int | str, name: str) -> None:
    self._conn.execute(
      """
      INSERT OR REPLACE INTO soundboards (guild_id, sound_id, name)
      VALUES (?, ?, ?)
      """,
      (str(guild_id), str(sound_id), name),
    )
    self._conn.commit()

  def delete(self, guild_id: int | str, sound_id: int | str) -> None:
    self._conn.execute(
      "DELETE FROM soundboards WHERE guild_id = ? AND sound_id = ?",
      (str(guild_id), str(sound_id)),
    )
    self._conn.commit()

  def get_sounds(self, guild_id: int | str) -> list[tuple[str, str]]:
    return self._conn.execute(
      "SELECT sound_id, name FROM soundboards WHERE guild_id = ?",
      (str(guild_id),),
    ).fetchall()

  def synchronize(
      self,
      guild_id: int | str,
      current_sounds: list[tuple[str, str]],
  ) -> list[str]:
    """現在のDiscordサウンド一覧へ同期し、削除されたsound_idを返す。"""
    gid = str(guild_id)
    normalized = [(str(sound_id), name) for sound_id, name in current_sounds]
    current_ids = {sound_id for sound_id, _ in normalized}
    database_ids = {
      row[0]
      for row in self._conn.execute(
        "SELECT sound_id FROM soundboards WHERE guild_id = ?",
        (gid,),
      ).fetchall()
    }
    inserts = [
      (gid, sound_id, name)
      for sound_id, name in normalized
      if sound_id not in database_ids
    ]
    deleted_ids = sorted(database_ids - current_ids)
    self._conn.executemany(
      "INSERT INTO soundboards (guild_id, sound_id, name) VALUES (?, ?, ?)",
      inserts,
    )
    self._conn.executemany(
      "DELETE FROM soundboards WHERE guild_id = ? AND sound_id = ?",
      [(gid, sound_id) for sound_id in deleted_ids],
    )
    self._conn.commit()
    return deleted_ids
