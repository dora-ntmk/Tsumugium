"""運営者からのお知らせ1件分の型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Announcement:
  id: int
  title: str
  body: str
  image: bytes | None = None
  # 添付するときのファイル名（拡張子は画像の中身から決めたもの）
  image_name: str | None = None
  link_url: str | None = None
  link_label: str | None = None
  # UNIX時刻（秒）。starts_at が None ならすぐ表示し、expires_at が None なら恒久
  starts_at: int | None = None
  expires_at: int | None = None
  created_at: int = 0

  @property
  def permanent(self) -> bool:
    return self.expires_at is None

  def is_active(self, now: int) -> bool:
    if self.starts_at is not None and now < self.starts_at:
      return False
    return self.expires_at is None or now < self.expires_at

  def is_expired(self, now: int) -> bool:
    return self.expires_at is not None and now >= self.expires_at
