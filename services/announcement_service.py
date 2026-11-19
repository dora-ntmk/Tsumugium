"""運営者からのお知らせの検証・保存と、接続メッセージに載せるお知らせの選び出しを担当する。"""

import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from models.announcement import JST, Announcement, format_time

MAX_TITLE_LENGTH = 100
MAX_BODY_LENGTH = 500
MAX_LINK_URL_LENGTH = 512
MAX_LINK_LABEL_LENGTH = 80
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# 接続メッセージに載せる件数。超えた分は「ほか N 件」とまとめる
MAX_DISPLAY = 3

# 画像の中身の先頭のバイト列 → 拡張子
_IMAGE_SIGNATURES = (
  (b"\x89PNG\r\n\x1a\n", "png"),
  (b"\xff\xd8\xff", "jpg"),
  (b"GIF87a", "gif"),
  (b"GIF89a", "gif"),
)

_RELATIVE_PATTERN = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$")
_ABSOLUTE_FORMATS = (
  "%Y-%m-%d %H:%M",
  "%Y/%m/%d %H:%M",
  "%Y-%m-%d",
  "%Y/%m/%d",
)
# /announce edit で期限・開始を外すときの値
CLEAR_WORDS = {"none", "なし"}

TIME_FORMAT_HELP = "例: 2026-12-01 18:00（日本時間）・3d・12h・30m"


class AnnouncementError(ValueError):
  """入力の誤り。メッセージはそのまま運営者へ見せる。"""


@dataclass(frozen=True)
class AnnouncementDraft:
  """保存する前のお知らせ。validate() を通してから保存する。"""
  title: str
  body: str
  link_url: str | None = None
  link_label: str | None = None
  starts_at: int | None = None
  expires_at: int | None = None
  image: bytes | None = None
  image_name: str | None = None


def _blank_to_none(value: str | None) -> str | None:
  if value is None:
    return None
  value = value.strip()
  return value or None


def is_clear_word(text: str | None) -> bool:
  return text is not None and unicodedata.normalize("NFKC", text).strip().lower() in CLEAR_WORDS


def parse_time(text: str | None, now: int, *, base: int | None = None) -> int | None:
  """日時の指定をUNIX時刻にする。空欄ならNone。

  `3d`・`12h`・`30m`（組み合わせ可、例: `1d12h`）は base（省略時は now）から数え、
  `YYYY-MM-DD HH:MM`・`YYYY/MM/DD HH:MM`（時刻を省くと0:00）は日本時間として読む。
  """
  if text is None:
    return None
  value = unicodedata.normalize("NFKC", text).strip().lower()
  if not value:
    return None
  relative = _RELATIVE_PATTERN.match(value.replace(" ", ""))
  if relative and any(relative.groups()):
    days, hours, minutes = (int(part or 0) for part in relative.groups())
    seconds = days * 86400 + hours * 3600 + minutes * 60
    if seconds <= 0:
      raise AnnouncementError(f"期間は1分以上にしてください: {text}")
    return (now if base is None else base) + seconds
  value = re.sub(r"\s+", " ", value)
  for time_format in _ABSOLUTE_FORMATS:
    try:
      parsed = datetime.strptime(value, time_format)
    except ValueError:
      continue
    return int(parsed.replace(tzinfo=JST).timestamp())
  raise AnnouncementError(f"日時を読めませんでした: {text}（{TIME_FORMAT_HELP}）")


def detect_image_extension(data: bytes) -> str | None:
  for signature, extension in _IMAGE_SIGNATURES:
    if data.startswith(signature):
      return extension
  if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
    return "webp"
  return None


def check_image_size(size: int) -> None:
  if size > MAX_IMAGE_BYTES:
    raise AnnouncementError(
      f"画像は{MAX_IMAGE_BYTES // (1024 * 1024)}MBまでです"
      f"（選んだ画像: {size / (1024 * 1024):.1f}MB）"
    )


def check_image(data: bytes) -> str:
  """画像の大きさと形式を確かめ、拡張子を返す。"""
  check_image_size(len(data))
  extension = detect_image_extension(data)
  if extension is None:
    raise AnnouncementError("画像はPNG・JPEG・GIF・WebPのどれかにしてください")
  return extension


def validate(draft: AnnouncementDraft, now: int) -> AnnouncementDraft:
  """入力を確かめ、前後の空白を除いたお知らせを返す。誤りは AnnouncementError。"""
  title = (draft.title or "").strip()
  body = (draft.body or "").strip()
  if not title:
    raise AnnouncementError("タイトルを入力してください")
  if len(title) > MAX_TITLE_LENGTH:
    raise AnnouncementError(f"タイトルは{MAX_TITLE_LENGTH}文字までです")
  if not body:
    raise AnnouncementError("本文を入力してください")
  if len(body) > MAX_BODY_LENGTH:
    raise AnnouncementError(f"本文は{MAX_BODY_LENGTH}文字までです")

  link_url = _blank_to_none(draft.link_url)
  link_label = _blank_to_none(draft.link_label)
  if link_url is None and link_label is not None:
    raise AnnouncementError("リンクの表示名だけでは表示できません。リンクURLも入力してください")
  if link_url is not None:
    if len(link_url) > MAX_LINK_URL_LENGTH:
      raise AnnouncementError(f"リンクURLは{MAX_LINK_URL_LENGTH}文字までです")
    parts = urlsplit(link_url)
    if parts.scheme not in ("http", "https") or not parts.netloc or " " in link_url:
      raise AnnouncementError("リンクURLは https:// か http:// で始まるURLにしてください")
  if link_label is not None and len(link_label) > MAX_LINK_LABEL_LENGTH:
    raise AnnouncementError(f"リンクの表示名は{MAX_LINK_LABEL_LENGTH}文字までです")

  if draft.expires_at is not None:
    if draft.expires_at <= now:
      raise AnnouncementError("表示終了がすでに過ぎています")
    if draft.starts_at is not None and draft.starts_at >= draft.expires_at:
      raise AnnouncementError("表示開始は表示終了より前にしてください")

  if (draft.image is None) != (draft.image_name is None):
    raise AnnouncementError("画像を読み込めませんでした")
  if draft.image is not None:
    check_image(draft.image)

  return AnnouncementDraft(
    title=title,
    body=body,
    link_url=link_url,
    link_label=link_label,
    starts_at=draft.starts_at,
    expires_at=draft.expires_at,
    image=draft.image,
    image_name=draft.image_name,
  )


def _display_order(announcement: Announcement):
  # 恒久（古い順）のあとに、期間限定（期限の近い順）
  if announcement.permanent:
    return (0, announcement.created_at, announcement.id)
  return (1, announcement.expires_at, announcement.id)


class AnnouncementService:
  def __init__(self, repository, clock=time.time):
    self.repository = repository
    self._clock = clock
    self.repository.delete_expired(self.now())

  def now(self) -> int:
    return int(self._clock())

  def get(self, announcement_id: int) -> Announcement | None:
    announcement = self.repository.get(announcement_id)
    if announcement is None or announcement.is_expired(self.now()):
      return None
    return announcement

  def active(self) -> list[Announcement]:
    """今表示するお知らせを、表示する順に返す。"""
    now = self.now()
    return sorted(
      (item for item in self.repository.list_all() if item.is_active(now)),
      key=_display_order,
    )

  def display(self) -> tuple[list[Announcement], int]:
    """接続メッセージに載せるお知らせ（最大 MAX_DISPLAY 件）と、載せきれない件数。"""
    announcements = self.active()
    return announcements[:MAX_DISPLAY], max(0, len(announcements) - MAX_DISPLAY)

  def list_all(self) -> list[Announcement]:
    """期限が過ぎていないお知らせ（表示前のものを含む）を、表示する順に返す。"""
    now = self.now()
    return sorted(
      (item for item in self.repository.list_all() if not item.is_expired(now)),
      key=lambda item: (not item.is_active(now), *_display_order(item)),
    )

  def add(self, draft: AnnouncementDraft) -> int:
    now = self.now()
    checked = validate(draft, now)
    self.repository.delete_expired(now)
    return self.repository.add(
      title=checked.title,
      body=checked.body,
      image=checked.image,
      image_name=checked.image_name,
      link_url=checked.link_url,
      link_label=checked.link_label,
      starts_at=checked.starts_at,
      expires_at=checked.expires_at,
      created_at=now,
    )

  def update(self, announcement_id: int, draft: AnnouncementDraft) -> None:
    checked = validate(draft, self.now())
    updated = self.repository.update(
      announcement_id,
      title=checked.title,
      body=checked.body,
      image=checked.image,
      image_name=checked.image_name,
      link_url=checked.link_url,
      link_label=checked.link_label,
      starts_at=checked.starts_at,
      expires_at=checked.expires_at,
    )
    if not updated:
      raise AnnouncementError(f"ID {announcement_id} のお知らせが見つかりません")

  def delete(self, announcement_id: int) -> bool:
    return self.repository.delete(announcement_id)

  def status_text(self, announcement: Announcement) -> str:
    """一覧に出す期間の説明。"""
    now = self.now()
    if announcement.permanent:
      period = "恒久"
    else:
      period = f"{format_time(announcement.expires_at, now)}まで"
    if announcement.is_active(now):
      return f"表示中・{period}"
    return f"予約・{format_time(announcement.starts_at, now)}から（{period}）"
