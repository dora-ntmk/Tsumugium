"""config.db の announcements 表（運営者からのお知らせ）を扱うSQLite Repository。

サーバー設定と同じ config.db に置き、既存の定時バックアップで守る。
"""

import sqlite3

from models.announcement import Announcement


_COLUMNS = (
  "id, title, body, image, image_name, link_url, link_label,"
  " starts_at, expires_at, created_at"
)

# update() で書き換えてよい列
_UPDATABLE = {
  "title", "body", "image", "image_name", "link_url", "link_label",
  "starts_at", "expires_at",
}


class AnnouncementRepository:
  def __init__(self, db_path: str = "db/config.db"):
    self._conn = sqlite3.connect(
      db_path,
      check_same_thread=False,
      timeout=30,
    )
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute(
      """
      CREATE TABLE IF NOT EXISTS announcements (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT    NOT NULL,
        body        TEXT    NOT NULL,
        image       BLOB    DEFAULT NULL,
        image_name  TEXT    DEFAULT NULL,
        link_url    TEXT    DEFAULT NULL,
        link_label  TEXT    DEFAULT NULL,
        starts_at   INTEGER DEFAULT NULL,
        expires_at  INTEGER DEFAULT NULL,
        created_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
      )
      """
    )
    self._conn.commit()

  @staticmethod
  def _from_row(row) -> Announcement:
    return Announcement(
      id=row[0],
      title=row[1],
      body=row[2],
      image=None if row[3] is None else bytes(row[3]),
      image_name=row[4],
      link_url=row[5],
      link_label=row[6],
      starts_at=row[7],
      expires_at=row[8],
      created_at=row[9],
    )

  def add(
      self,
      *,
      title: str,
      body: str,
      image: bytes | None = None,
      image_name: str | None = None,
      link_url: str | None = None,
      link_label: str | None = None,
      starts_at: int | None = None,
      expires_at: int | None = None,
      created_at: int | None = None,
  ) -> int:
    cursor = self._conn.execute(
      """
      INSERT INTO announcements (
        title, body, image, image_name, link_url, link_label,
        starts_at, expires_at, created_at
      )
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, strftime('%s', 'now')))
      """,
      (
        title, body, image, image_name, link_url, link_label,
        starts_at, expires_at, created_at,
      ),
    )
    self._conn.commit()
    return cursor.lastrowid

  def get(self, announcement_id: int) -> Announcement | None:
    row = self._conn.execute(
      f"SELECT {_COLUMNS} FROM announcements WHERE id = ?",
      (announcement_id,),
    ).fetchone()
    return None if row is None else self._from_row(row)

  def list_all(self) -> list[Announcement]:
    rows = self._conn.execute(
      f"SELECT {_COLUMNS} FROM announcements ORDER BY id"
    ).fetchall()
    return [self._from_row(row) for row in rows]

  def update(self, announcement_id: int, **fields) -> bool:
    unknown = set(fields) - _UPDATABLE
    if unknown:
      raise ValueError(f"書き換えられない列です: {sorted(unknown)}")
    if not fields:
      return self.get(announcement_id) is not None
    names = sorted(fields)
    assignments = ", ".join(f"{name} = ?" for name in names)
    cursor = self._conn.execute(
      f"UPDATE announcements SET {assignments} WHERE id = ?",
      (*(fields[name] for name in names), announcement_id),
    )
    self._conn.commit()
    return cursor.rowcount > 0

  def delete(self, announcement_id: int) -> bool:
    cursor = self._conn.execute(
      "DELETE FROM announcements WHERE id = ?",
      (announcement_id,),
    )
    self._conn.commit()
    return cursor.rowcount > 0

  def delete_expired(self, now: int) -> int:
    cursor = self._conn.execute(
      "DELETE FROM announcements WHERE expires_at IS NOT NULL AND expires_at <= ?",
      (now,),
    )
    self._conn.commit()
    return cursor.rowcount

  def close(self) -> None:
    self._conn.close()
