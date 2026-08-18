"""
ファイル名：dict_view.py
作者：どら
説明：辞書ページング表示 UI モジュール。
      辞書エントリ一覧を 20 件/ページで表示する discord.ui.View。
      通常辞書・優先辞書を分けて表示し、セクション間ジャンプにも対応する。
依存関係：discord.py
"""
import discord
from presentation.embeds import make_embed
from services.error_notification_service import ensure_error_notifier
from collections.abc import Callable

_PAGE_SIZE = 20


class DictViewPaginator(discord.ui.View):
  def __init__(
      self,
      normal_items: list[tuple[str, str]],
      priority_items: list[tuple[str, str]],
      key_prefix: str,
      error_notifier=None,
      word_formatter: Callable[[str], str] | None = None,
  ):
    super().__init__(timeout=120)
    self.normal_items   = normal_items
    self.priority_items = priority_items
    self.key_prefix = key_prefix
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.word_formatter = word_formatter or (lambda word: word)
    self.page = 0
    self.normal_pages   = (len(normal_items)   + _PAGE_SIZE - 1) // _PAGE_SIZE if normal_items   else 0
    self.priority_pages = (len(priority_items) + _PAGE_SIZE - 1) // _PAGE_SIZE if priority_items else 0
    self.total_pages = self.normal_pages + self.priority_pages
    self.message: discord.Message | None = None
    self._update_buttons()

  def build_embed(self) -> discord.Embed:
    is_sounddict = self.key_prefix == 'sounddict'
    embed = make_embed('音声辞書一覧' if is_sounddict else '辞書一覧')

    if self.page < self.normal_pages:
      start = self.page * _PAGE_SIZE
      page_items = self.normal_items[start:start + _PAGE_SIZE]
      section = '通常辞書'
      section_page = self.page + 1
      section_total = self.normal_pages
    else:
      priority_page = self.page - self.normal_pages
      start = priority_page * _PAGE_SIZE
      page_items = self.priority_items[start:start + _PAGE_SIZE]
      section = '優先辞書'
      section_page = priority_page + 1
      section_total = self.priority_pages

    header = '単語  →  音声名' if is_sounddict else '単語  →  読み方'
    lines = [
      f"{self.word_formatter(w)}  →  {r}"
      for w, r in page_items
    ]
    if header:
      separator = "─" * 24
      lines = [header, separator] + lines
    parts = [f"**{section}**"]
    parts.append("```\n" + "\n".join(lines) + "\n```")
    embed.description = "\n".join(parts)

    page_str = f'ページ {section_page} / {section_total}'
    embed.set_footer(text=page_str)
    return embed

  def _update_buttons(self):
    in_normal = self.page < self.normal_pages
    section_pages = self.normal_pages if in_normal else self.priority_pages

    self.prev_button.disabled = (section_pages <= 1)
    self.next_button.disabled = (section_pages <= 1)

    self.jump_normal_button.disabled   = (self.normal_pages   == 0 or in_normal)
    self.jump_priority_button.disabled = (self.priority_pages == 0 or not in_normal)

  @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
  async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
    if self.page < self.normal_pages:
      self.page = (self.page - 1) % self.normal_pages
    else:
      pp = (self.page - self.normal_pages - 1) % self.priority_pages
      self.page = self.normal_pages + pp
    self._update_buttons()
    await interaction.response.edit_message(embed=self.build_embed(), view=self)

  @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
  async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
    if self.page < self.normal_pages:
      self.page = (self.page + 1) % self.normal_pages
    else:
      pp = (self.page - self.normal_pages + 1) % self.priority_pages
      self.page = self.normal_pages + pp
    self._update_buttons()
    await interaction.response.edit_message(embed=self.build_embed(), view=self)

  @discord.ui.button(label="📚", style=discord.ButtonStyle.primary)
  async def jump_normal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
    self.page = 0
    self._update_buttons()
    await interaction.response.edit_message(embed=self.build_embed(), view=self)

  @discord.ui.button(label="⭐", style=discord.ButtonStyle.primary)
  async def jump_priority_button(self, interaction: discord.Interaction, button: discord.ui.Button):
    self.page = self.normal_pages
    self._update_buttons()
    await interaction.response.edit_message(embed=self.build_embed(), view=self)

  async def on_timeout(self):
    if self.message is not None:
      for item in self.children:
        item.disabled = True
      try:
        await self.message.edit(view=self)
      except Exception as e:
        self.error_notifier.report(f"on_timeout: {e}")
