import discord
from messages import build_embed, get_desc

_PAGE_SIZE = 20


class DictViewPaginator(discord.ui.View):
  def __init__(self, normal_items: list[tuple[str, str]], priority_items: list[tuple[str, str]], lang: str, key_prefix: str):
    super().__init__(timeout=120)
    self.normal_items   = normal_items
    self.priority_items = priority_items
    self.lang = lang
    self.key_prefix = key_prefix
    self.page = 0
    self.normal_pages   = (len(normal_items)   + _PAGE_SIZE - 1) // _PAGE_SIZE if normal_items   else 0
    self.priority_pages = (len(priority_items) + _PAGE_SIZE - 1) // _PAGE_SIZE if priority_items else 0
    self.total_pages = self.normal_pages + self.priority_pages
    self.message: discord.Message | None = None
    self._update_buttons()

  def build_embed(self) -> discord.Embed:
    p = self.key_prefix
    embed = build_embed(f'{p}.view', lang=self.lang)
    prefix = get_desc(f'{p}.view.prefix', lang=self.lang)

    if self.page < self.normal_pages:
      start = self.page * _PAGE_SIZE
      page_items = self.normal_items[start:start + _PAGE_SIZE]
      section = get_desc(f'{p}.view.section_normal', lang=self.lang)
      section_page = self.page + 1
      section_total = self.normal_pages
    else:
      priority_page = self.page - self.normal_pages
      start = priority_page * _PAGE_SIZE
      page_items = self.priority_items[start:start + _PAGE_SIZE]
      section = get_desc(f'{p}.view.section_priority', lang=self.lang)
      section_page = priority_page + 1
      section_total = self.priority_pages

    header = get_desc(f'{p}.view.header', lang=self.lang)
    lines = [f"{w}  →  {r}" for w, r in page_items]
    if header:
      separator = "─" * 24
      lines = [header, separator] + lines
    parts = []
    if prefix:
      parts.append(prefix)
    if section:
      parts.append(f"**{section}**")
    parts.append("```\n" + "\n".join(lines) + "\n```")
    embed.description = "\n".join(parts)

    page_str = get_desc(f'{p}.view.page', lang=self.lang).format(
      page=section_page, total=section_total
    )
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
        print(f"on_timeout: {e}")
        pass