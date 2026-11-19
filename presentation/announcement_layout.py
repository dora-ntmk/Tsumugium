"""運営者からのお知らせ欄（Components v2の部品）を作る。

make_layout で作った枠の下に、区切り線・「📢お知らせ」・各お知らせを足す。
"""

import io

import discord

from models.announcement import Announcement, format_time
from presentation.layouts import LayoutMessage


ANNOUNCEMENT_HEADING = "## 📢お知らせ"


def _image_filename(announcement: Announcement) -> str:
  extension = (announcement.image_name or "image.png").rsplit(".", 1)[-1]
  return f"announcement-{announcement.id}.{extension}"


def _announcement_text(announcement: Announcement, now: int | None) -> str:
  lines = [f"**{announcement.title}**"]
  if not announcement.permanent:
    lines.append(f"-# {format_time(announcement.expires_at, now)}まで")
  lines.append(announcement.body)
  return "\n".join(lines)


def _add_announcement(
    layout: LayoutMessage,
    announcement: Announcement,
    *,
    include_images: bool,
    now: int | None,
) -> None:
  layout.add_item(discord.ui.TextDisplay(_announcement_text(announcement, now)))
  if include_images and announcement.image is not None:
    filename = _image_filename(announcement)
    layout.add_file(discord.File(io.BytesIO(announcement.image), filename=filename))
    layout.add_item(
      discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}"))
    )
  if announcement.link_url:
    layout.add_item(
      discord.ui.ActionRow(
        discord.ui.Button(
          style=discord.ButtonStyle.link,
          label=announcement.link_text,
          url=announcement.link_url,
        )
      )
    )


def add_announcements(
    layout: LayoutMessage,
    announcements,
    *,
    hidden_count: int = 0,
    include_images: bool = True,
    now: int | None = None,
) -> LayoutMessage:
  """枠の下にお知らせ欄を足す。お知らせがなければ何も足さない。

  include_images が False（ファイルを添付できないチャンネル）なら画像を省く。
  """
  announcements = list(announcements)
  if not announcements:
    return layout
  layout.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
  layout.add_item(discord.ui.TextDisplay(ANNOUNCEMENT_HEADING))
  for index, announcement in enumerate(announcements):
    if index:
      layout.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    _add_announcement(layout, announcement, include_images=include_images, now=now)
  if hidden_count > 0:
    layout.add_item(discord.ui.TextDisplay(f"-# ほか{hidden_count}件のお知らせがあります"))
  return layout
