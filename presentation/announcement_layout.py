"""運営者からのお知らせ欄（Components v2の部品）を作る。

make_layout で作った枠の下に、区切り線を引いて各お知らせを足す。
タイトル（## 見出し）と本文の右にリンクボタン（Section の accessory）、その下に画像を置く。
"""

import io

import discord

from models.announcement import Announcement, format_time
from presentation.layouts import LayoutMessage


def _image_filename(announcement: Announcement) -> str:
  extension = (announcement.image_name or "image.png").rsplit(".", 1)[-1]
  return f"announcement-{announcement.id}.{extension}"


def _announcement_text(announcement: Announcement, now: int | None) -> str:
  lines = [f"## {announcement.title}", announcement.body]
  if not announcement.permanent:
    lines.append(f"-# {format_time(announcement.expires_at, now)}まで")
  return "\n".join(lines)


def _add_announcement(
    layout: LayoutMessage,
    announcement: Announcement,
    *,
    include_images: bool,
    now: int | None,
) -> None:
  """タイトルと本文の右にリンクボタン、その下に画像を置く。"""
  text = discord.ui.TextDisplay(_announcement_text(announcement, now))
  if announcement.link_url:
    layout.add_item(
      discord.ui.Section(
        text,
        accessory=discord.ui.Button(
          style=discord.ButtonStyle.link,
          label=announcement.link_text,
          url=announcement.link_url,
        ),
      )
    )
  else:
    layout.add_item(text)
  if include_images and announcement.image is not None:
    filename = _image_filename(announcement)
    layout.add_file(discord.File(io.BytesIO(announcement.image), filename=filename))
    layout.add_item(
      discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}"))
    )


def add_announcements(
    layout: LayoutMessage,
    announcements,
    *,
    hidden_count: int = 0,
    include_images: bool = True,
    now: int | None = None,
) -> LayoutMessage:
  """枠の下にお知らせを足す。お知らせがなければ何も足さない。

  見出し（「お知らせ」など）は付けず、お知らせごとに区切り線（large）を引いてから並べる。
  include_images が False（ファイルを添付できないチャンネル）なら画像を省く。
  """
  announcements = list(announcements)
  if not announcements:
    return layout
  for announcement in announcements:
    layout.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    _add_announcement(layout, announcement, include_images=include_images, now=now)
  if hidden_count > 0:
    layout.add_item(discord.ui.TextDisplay(f"-# ほか{hidden_count}件のお知らせがあります"))
  return layout
