"""Components v2（LayoutView）で組むメッセージの共通ひな形。

Embed（presentation/embeds.py）から順に移していく。第一弾は接続メッセージで、
運営者からのお知らせを下に載せる。v2のメッセージには content・embed を一緒に付けられない。
"""

import io
from dataclasses import dataclass, field

import discord

from models.announcement import Announcement
from presentation.embeds import EmbedType, embed_color
from services.announcement_service import DEFAULT_LINK_LABEL, format_time


ANNOUNCEMENT_HEADING = "## 📢お知らせ"


@dataclass
class LayoutMessage:
  """送るときに渡す view と、view から attachment:// で参照するファイル。

  discord.File は一度しか送れないので、送るたびに作り直す。
  """
  view: discord.ui.LayoutView
  files: list[discord.File] = field(default_factory=list)


def _image_filename(announcement: Announcement) -> str:
  extension = (announcement.image_name or "image.png").rsplit(".", 1)[-1]
  return f"announcement-{announcement.id}.{extension}"


def _announcement_text(announcement: Announcement, now: int | None) -> str:
  lines = [f"**{announcement.title}**"]
  if not announcement.permanent:
    lines.append(f"-# {format_time(announcement.expires_at, now)}まで")
  lines.append(announcement.body)
  return "\n".join(lines)


def _announcement_items(
    announcement: Announcement,
    *,
    include_images: bool,
    now: int | None,
    files: list[discord.File],
) -> list[discord.ui.Item]:
  items: list[discord.ui.Item] = [
    discord.ui.TextDisplay(_announcement_text(announcement, now)),
  ]
  if include_images and announcement.image is not None:
    filename = _image_filename(announcement)
    files.append(discord.File(io.BytesIO(announcement.image), filename=filename))
    items.append(
      discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}"))
    )
  if announcement.link_url:
    items.append(
      discord.ui.ActionRow(
        discord.ui.Button(
          style=discord.ButtonStyle.link,
          label=announcement.link_label or DEFAULT_LINK_LABEL,
          url=announcement.link_url,
        )
      )
    )
  return items


def make_layout(
    title: str,
    description: str | None = None,
    *,
    layout_type: EmbedType = EmbedType.INFO,
    announcements=(),
    hidden_count: int = 0,
    include_images: bool = True,
    header: str | None = None,
    now: int | None = None,
) -> LayoutMessage:
  """枠（Container）の中に見出しと本文、その下にお知らせを並べたメッセージを作る。

  枠の色は make_embed と同じ。お知らせがなければ区切り線から下を出さない。
  header は枠の外（上）に出す一言（プレビューの説明など）。
  """
  files: list[discord.File] = []
  text = f"## {title}"
  if description:
    text += f"\n{description}"
  children: list[discord.ui.Item] = [discord.ui.TextDisplay(text)]

  announcements = list(announcements)
  if announcements:
    children.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    children.append(discord.ui.TextDisplay(ANNOUNCEMENT_HEADING))
    for index, announcement in enumerate(announcements):
      if index:
        children.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
      children.extend(
        _announcement_items(
          announcement,
          include_images=include_images,
          now=now,
          files=files,
        )
      )
    if hidden_count > 0:
      children.append(
        discord.ui.TextDisplay(f"-# ほか{hidden_count}件のお知らせがあります")
      )

  view = discord.ui.LayoutView(timeout=None)
  if header:
    view.add_item(discord.ui.TextDisplay(header))
  view.add_item(discord.ui.Container(*children, accent_colour=embed_color(layout_type)))
  return LayoutMessage(view=view, files=files)
