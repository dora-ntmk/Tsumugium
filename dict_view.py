"""
ファイル名：dict_view.py
作者：どら
説明：辞書ページング表示 UI モジュール。
      辞書エントリ一覧を 20 件/ページで表示する discord.ui.View。
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
      items: list[tuple[str, str]],
      key_prefix: str,
      error_notifier=None,
      word_formatter: Callable[[str], str] | None = None,
  ):
    super().__init__(timeout=120)
    self.items = items
    self.key_prefix = key_prefix
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.word_formatter = word_formatter or (lambda word: word)
    self.page = 0
    self.total_pages = (len(items) + _PAGE_SIZE - 1) // _PAGE_SIZE if items else 0
    self.message: discord.Message | None = None
    self._update_buttons()

  def build_embed(self) -> discord.Embed:
    is_sounddict = self.key_prefix == 'sounddict'
    embed = make_embed('音声辞書一覧' if is_sounddict else '辞書一覧')

    start = self.page * _PAGE_SIZE
    page_items = self.items[start:start + _PAGE_SIZE]

    header = '単語  →  音声名' if is_sounddict else '単語  →  読み方'
    lines = [
      f"{self.word_formatter(w)}  →  {r}"
      for w, r in page_items
    ]
    if header:
      separator = "─" * 24
      lines = [header, separator] + lines
    embed.description = "```\n" + "\n".join(lines) + "\n```"

    page_str = f'ページ {self.page + 1} / {self.total_pages}'
    embed.set_footer(text=page_str)
    return embed

  def _update_buttons(self):
    self.prev_button.disabled = (self.total_pages <= 1)
    self.next_button.disabled = (self.total_pages <= 1)

  @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
  async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
    self.page = (self.page - 1) % self.total_pages
    self._update_buttons()
    await interaction.response.edit_message(embed=self.build_embed(), view=self)

  @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
  async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
    self.page = (self.page + 1) % self.total_pages
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
