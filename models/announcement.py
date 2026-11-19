"""運営者からのお知らせ1件分の型と、日時・リンクの表示に使う値。

Service（入力の確認）と Presentation（表示）の両方から使うので、どちらにも依存しないここに置く。
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


JST = timezone(timedelta(hours=9), "JST")
DEFAULT_LINK_LABEL = "詳しくはこちら"


def format_time(timestamp: int, now: int | None = None) -> str:
  """日本時間で表示する。今年なら年を省く。"""
  moment = datetime.fromtimestamp(timestamp, JST)
  this_year = datetime.fromtimestamp(time.time() if now is None else now, JST).year
  if moment.year == this_year:
    return moment.strftime("%m/%d %H:%M")
  return moment.strftime("%Y/%m/%d %H:%M")


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
  def link_text(self) -> str:
    return self.link_label or DEFAULT_LINK_LABEL

  @property
  def permanent(self) -> bool:
    return self.expires_at is None

  def is_active(self, now: int) -> bool:
    if self.starts_at is not None and now < self.starts_at:
      return False
    return self.expires_at is None or now < self.expires_at

  def is_expired(self, now: int) -> bool:
    return self.expires_at is not None and now >= self.expires_at
