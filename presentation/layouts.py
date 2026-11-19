"""Components v2（LayoutView）のメッセージの共通ひな形。

presentation/embeds.py の make_embed と同じく、色・見出し・本文の入った枠だけを作る。
中に並べる部品（区切り線・文・画像など）は呼び出し側が add_item で足す。
v2のメッセージには content・embed を一緒に付けられない。
"""

from dataclasses import dataclass, field

import discord

from presentation.embeds import EmbedType, embed_color


@dataclass
class LayoutMessage:
  """送るときに渡す view と、view から attachment:// で参照するファイル。

  送るときは send(view=..., files=...)、defer() したあとは
  edit_original_response(view=..., attachments=...) を使う。
  discord.File は一度しか送れないので、送るたびに作り直す。
  """
  view: discord.ui.LayoutView
  container: discord.ui.Container
  files: list[discord.File] = field(default_factory=list)

  def add_item(self, item: discord.ui.Item) -> "LayoutMessage":
    """枠の中の最後に部品を足す（Embed の add_field に当たる）。"""
    self.container.add_item(item)
    return self

  def add_file(self, file: discord.File) -> "LayoutMessage":
    self.files.append(file)
    return self


def make_layout(
    title: str,
    description: str | None = None,
    *,
    layout_type: EmbedType = EmbedType.INFO,
    header: str | None = None,
) -> LayoutMessage:
  """make_embed と同じ色の枠（Container）に、`## 見出し` と本文を入れたメッセージを作る。

  header は枠の外（上）に出す一言（プレビューの説明など）。
  """
  text = f"## {title}"
  if description:
    text += f"\n{description}"
  container = discord.ui.Container(
    discord.ui.TextDisplay(text),
    accent_colour=embed_color(layout_type),
  )
  view = discord.ui.LayoutView(timeout=None)
  if header:
    view.add_item(discord.ui.TextDisplay(header))
  view.add_item(container)
  return LayoutMessage(view=view, container=container)
