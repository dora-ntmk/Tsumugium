import os
import re
import json
import unicodedata
import discord
from typing import Optional
from messages import build_embed, get_desc


def _lstr(key: str) -> discord.app_commands.locale_str:
    return discord.app_commands.locale_str(get_desc(key), key=key)


_CUSTOM_EMOJI_RE = re.compile(r'<a?:[\w/:%#$&?()~.=+\-]+:\d+>')
_STANDARD_EMOJI_RE = re.compile(r'^:([\w/:%#$&?()~.=+\-]+):$')

_SPOILER_RE      = re.compile(r'\|\|[\s\S]+?\|\|')
_STRIKE_RE       = re.compile(r'~~[\s\S]+?~~')
_CODE_BLOCK_RE   = re.compile(r'```[\s\S]+?```')
_INLINE_CODE_RE  = re.compile(r'`[^`]+`')
_MENTION_USER_RE = re.compile(r'<@!?(\d+)>')
_MENTION_CH_RE   = re.compile(r'<#(\d+)>')
_MENTION_ROLE_RE = re.compile(r'<@&(\d+)>')
_WWW_RE          = re.compile(r'[wWｗＷ]{2,}')

_URL_PATTERNS = [
    (re.compile(r'https?://(?:www\.)?youtu(?:\.be|be\.com)\S*'), 'ユーチューブへのリンク'),
    (re.compile(r'https?://(?:www\.)?(?:twitter|x)\.com\S*'),   'ツイッターへのリンク'),
    (re.compile(r'https?://(?:www\.)?twitch\.tv\S*'),            'ツイッチへのリンク'),
    (re.compile(r'https?://(?:www\.)?nicovideo\.jp\S*'),         'ニコニコへのリンク'),
    (re.compile(r'https?://nico\.ms\S*'),                        'ニコニコへのリンク'),
    (re.compile(r'https?://(?:www\.)?amazon\.(?:co\.jp|com)\S*'), 'アマゾンへのリンク'),
    (re.compile(r'https?://discord\.gg\S*'),                     'ディスコードへのリンク'),
    (re.compile(r'https?://(?:www\.)?discord\.com\S*'),          'ディスコードへのリンク'),
    (re.compile(r'https?://\S+'),                                'URLを省略'),
]

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff'}
_VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.webm', '.mkv', '.flv'}
_AUDIO_EXTS = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac'}


def _is_emoji_word(word: str) -> bool:
    if _CUSTOM_EMOJI_RE.fullmatch(word):
        return True
    if _STANDARD_EMOJI_RE.fullmatch(word):
        return True
    if not word:
        return False
    for ch in word:
        cp = ord(ch)
        if not (0x2600 <= cp <= 0x27BF or
                0x1F000 <= cp <= 0x1FFFF or
                0x2B00 <= cp <= 0x2BFF or
                cp in (0x200D, 0xFE0F, 0x20E3)):
            return False
    return True


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json(path: str, data: dict):
    with open(path, mode='w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _apply_dict(segments: list, mapping: dict) -> list:
    """Literal word replacements on non-replaced segments."""
    for word, read in mapping.items():
        if not word:
            continue
        pattern = re.escape(word)
        new_segments = []
        for seg_text, replaced in segments:
            if replaced:
                new_segments.append((seg_text, True))
                continue
            last_end = 0
            for m in re.finditer(pattern, seg_text):
                before = seg_text[last_end:m.start()]
                if before:
                    new_segments.append((before, False))
                new_segments.append((read, True))
                last_end = m.end()
            remaining = seg_text[last_end:]
            if remaining:
                new_segments.append((remaining, False))
        segments = new_segments
    return segments


def _apply_regex(segments: list, pattern, repl_fn) -> list:
    """Regex replacements on non-replaced segments."""
    new_segments = []
    for seg_text, replaced in segments:
        if replaced:
            new_segments.append((seg_text, True))
            continue
        last_end = 0
        for m in re.finditer(pattern, seg_text):
            before = seg_text[last_end:m.start()]
            if before:
                new_segments.append((before, False))
            replacement = repl_fn(m) if callable(repl_fn) else repl_fn
            if replacement:
                new_segments.append((replacement, True))
            last_end = m.end()
        remaining = seg_text[last_end:]
        if remaining:
            new_segments.append((remaining, False))
    return new_segments


_PAGE_SIZE = 20


def _normalize(s: str) -> str:
    """大文字小文字・半角全角を統一する。"""
    return unicodedata.normalize('NFKC', s).lower()


def _filter_entries(entries: dict, word: str) -> list[tuple[str, str]]:
    """キー全文一致 → キー部分一致 → よみがな部分一致（キー不一致のもの）の順で返す。"""
    nword = _normalize(word)
    exact_key   = [(k, v) for k, v in entries.items() if _normalize(k) == nword]
    partial_key = [(k, v) for k, v in entries.items() if nword in _normalize(k) and _normalize(k) != nword]
    value_match = [(k, v) for k, v in entries.items() if nword not in _normalize(k) and nword in _normalize(v)]
    return exact_key + partial_key + value_match


class DictViewPaginator(discord.ui.View):
    def __init__(self, word_items: list[tuple[str, str]], emoji_items: list[tuple[str, str]], lang: str):
        super().__init__(timeout=120)
        self.word_items  = word_items
        self.emoji_items = emoji_items
        self.lang = lang
        self.page = 0
        self.word_pages  = (len(word_items)  + _PAGE_SIZE - 1) // _PAGE_SIZE if word_items  else 0
        self.emoji_pages = (len(emoji_items) + _PAGE_SIZE - 1) // _PAGE_SIZE if emoji_items else 0
        self.total_pages = self.word_pages + self.emoji_pages
        self.message: discord.Message | None = None
        self._update_buttons()

    def _build_embed(self) -> discord.Embed:
        embed = build_embed('dict.view', lang=self.lang)
        prefix = get_desc('dict.view.prefix', lang=self.lang)

        if self.page < self.word_pages:
            start = self.page * _PAGE_SIZE
            page_items = self.word_items[start:start + _PAGE_SIZE]
            section = get_desc('dict.view.section_word', lang=self.lang)
            section_page = self.page + 1
            section_total = self.word_pages
        else:
            emoji_page = self.page - self.word_pages
            start = emoji_page * _PAGE_SIZE
            page_items = self.emoji_items[start:start + _PAGE_SIZE]
            section = get_desc('dict.view.section_emoji', lang=self.lang)
            section_page = emoji_page + 1
            section_total = self.emoji_pages

        lines = [f"{w}  →  {r}" for w, r in page_items]
        parts = []
        if prefix:
            parts.append(prefix)
        if section:
            parts.append(f"**{section}**")
        parts.append("```\n" + "\n".join(lines) + "\n```")
        embed.description = "\n".join(parts)

        page_str = get_desc('dict.view.page', lang=self.lang).format(
            page=section_page, total=section_total
        )
        embed.set_footer(text=page_str)
        return embed

    def _update_buttons(self):
        in_word = self.page < self.word_pages
        section_pages = self.word_pages if in_word else self.emoji_pages

        self.prev_button.disabled = (section_pages <= 1)
        self.next_button.disabled = (section_pages <= 1)

        self.jump_word_button.disabled  = (self.word_pages  == 0 or in_word)
        self.jump_emoji_button.disabled = (self.emoji_pages == 0 or not in_word)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.word_pages:
            self.page = (self.page - 1) % self.word_pages
        else:
            ep = (self.page - self.word_pages - 1) % self.emoji_pages
            self.page = self.word_pages + ep
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.word_pages:
            self.page = (self.page + 1) % self.word_pages
        else:
            ep = (self.page - self.word_pages + 1) % self.emoji_pages
            self.page = self.word_pages + ep
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="Aa", style=discord.ButtonStyle.primary)
    async def jump_word_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="😃", style=discord.ButtonStyle.primary)
    async def jump_emoji_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.word_pages
        self._update_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def on_timeout(self):
        if self.message is not None:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class DictManager:
    def __init__(self):
        os.makedirs('dict', exist_ok=True)
        common_path = 'dict/common.json'
        if not os.path.exists(common_path):
            _save_json(common_path, {})
        emoji_ja_data = _load_json('dict/emoji_ja.json')
        self._emoji_ja: dict = {
            k: v['short_name']
            for k, v in emoji_ja_data.items()
            if isinstance(v, dict) and 'short_name' in v
        }

    def _guild_path(self, guild_id: int) -> str:
        return f'dict/{guild_id}.json'

    def _guild_emoji_path(self, guild_id: int) -> str:
        return f'dict/{guild_id}_emoji.json'

    def init_guild(self, guild_id: int):
        path = self._guild_path(guild_id)
        if not os.path.exists(path):
            _save_json(path, {})

    def remove_guild(self, guild_id: int):
        for path in (self._guild_path(guild_id), self._guild_emoji_path(guild_id)):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as e:
                    print(f'辞書ファイル削除失敗: {path}: {e}')

    def add(self, guild_id: int, word: str, read: str) -> bool:
        """Returns True if overwriting an existing entry."""
        if len(read) > 50:
            raise ValueError('too_long')
        path = self._guild_emoji_path(guild_id) if _is_emoji_word(word) else self._guild_path(guild_id)
        data = _load_json(path)
        overwrite = word in data
        # Prepend new entry so it takes priority
        data = {word: read, **{k: v for k, v in data.items() if k != word}}
        _save_json(path, data)
        return overwrite

    def delete(self, guild_id: int, word: str) -> Optional[str]:
        """Returns the removed read string, or None if not found."""
        for path in (self._guild_path(guild_id), self._guild_emoji_path(guild_id)):
            data = _load_json(path)
            if word in data:
                read = data.pop(word)
                _save_json(path, data)
                return read
        return None

    def preprocess_text(self, text: str, guild_id: int, guild, attachments) -> tuple[str, list[tuple[int, int]]]:
        segments = [(text, False)]

        # 1-3. Guild emoji dict → guild word dict → common dict
        for path in (
            self._guild_emoji_path(guild_id),
            self._guild_path(guild_id),
            'dict/common.json',
        ):
            d = _load_json(path)
            if d:
                segments = _apply_dict(segments, d)

        # 4. emoji_ja short_name mapping
        if self._emoji_ja:
            segments = _apply_dict(segments, self._emoji_ja)

        # 5a. Spoilers
        segments = _apply_regex(segments, _SPOILER_RE, ',ネタバレ,')
        # 5b. Strikethrough
        segments = _apply_regex(segments, _STRIKE_RE, ',取り消し線,')
        # 5c. Code blocks
        segments = _apply_regex(segments, _CODE_BLOCK_RE, ',コードブロック,')
        # 5d. Inline code
        segments = _apply_regex(segments, _INLINE_CODE_RE, ',コードブロック,')

        # 5e. User mentions
        def _mention_user(m):
            if guild:
                member = guild.get_member(int(m.group(1)))
                if member:
                    return f'{member.display_name}へのメンション,'
            return 'ユーザーへのメンション,'
        segments = _apply_regex(segments, _MENTION_USER_RE, _mention_user)

        # 5f. Channel links
        def _channel_link(m):
            if guild:
                ch = guild.get_channel(int(m.group(1)))
                if ch:
                    return f'{ch.name}へのリンク,'
            return 'チャンネルへのリンク,'
        segments = _apply_regex(segments, _MENTION_CH_RE, _channel_link)

        # 5g. Role mentions
        def _mention_role(m):
            if guild:
                role = guild.get_role(int(m.group(1)))
                if role:
                    return f'{role.name}へのメンション,'
            return 'ロールへのメンション,'
        segments = _apply_regex(segments, _MENTION_ROLE_RE, _mention_role)

        # 5i. URLs (before www so www in URLs is protected)
        for url_re, url_read in _URL_PATTERNS:
            segments = _apply_regex(segments, url_re, url_read + ',')

        # 5h. www pattern → N × わら
        def _www_replace(m):
            return 'わら' * len(m.group(0)) + ','
        segments = _apply_regex(segments, _WWW_RE, _www_replace)

        # 5j. Newlines
        segments = _apply_regex(segments, re.compile(r'\n'), ',')
        # 5k. Spaces (half-width and full-width)
        segments = _apply_regex(segments, re.compile(r'[ \u3000]+'), ',')

        # 6. Join with replaced range tracking
        result_parts = []
        replaced_ranges = []
        pos = 0
        for seg_text, replaced in segments:
            end = pos + len(seg_text)
            if replaced:
                replaced_ranges.append((pos, end))
            result_parts.append(seg_text)
            pos = end
        result = ''.join(result_parts)

        # 7. Attachments (appended after; ranges not tracked)
        for att in attachments:
            _, ext = os.path.splitext(att.filename.lower())
            if ext in _IMAGE_EXTS:
                result += ',画像が添付されました'
            elif ext in _VIDEO_EXTS:
                result += ',動画が添付されました'
            elif ext in _AUDIO_EXTS:
                result += ',音声が添付されました'
            else:
                result += ',ファイルが添付されました'

        return result, replaced_ranges


class WordDict:
    def __init__(self, client, tree, dict_manager: DictManager, server_config):
        self.client = client
        self.tree = tree
        self.dict_manager = dict_manager
        self.server_config = server_config
        self._register()

    def _register(self):
        dict_group = discord.app_commands.Group(
            name='dict',
            description=_lstr('commands.dict._group')
        )

        @dict_group.command(name='add', description=_lstr('commands.dict.add.description'))
        @discord.app_commands.describe(
            word=_lstr('commands.dict.add.args.word'),
            read=_lstr('commands.dict.add.args.read')
        )
        @discord.app_commands.checks.has_permissions(manage_guild=True)
        async def dict_add(ctx, word: str, read: str):
            try:
                await ctx.response.defer()
                lang = self.server_config.get(ctx.guild.id, 'Language')
                try:
                    overwrite = self.dict_manager.add(ctx.guild.id, word, read)
                except ValueError:
                    await ctx.edit_original_response(
                        embed=build_embed('dict.add.too_long', lang=lang, read=read)
                    )
                    return
                key = 'dict.add.overwrite' if overwrite else 'dict.add.success'
                await ctx.edit_original_response(
                    embed=build_embed(key, lang=lang, word=word, read=read)
                )
            except discord.errors.InteractionResponded:
                return
            except discord.errors.HTTPException as e:
                print(f'HTTPException in dict_add: {e}')
            except Exception as e:
                print(f'Exception in dict_add: {e}')
                try:
                    lang = self.server_config.get(ctx.guild.id, 'Language')
                    await ctx.edit_original_response(
                        embed=build_embed('dict.add.failure', lang=lang)
                    )
                except Exception as inner:
                    print(f'Exception in dict_add fallback: {inner}')

        @dict_group.command(name='del', description=_lstr('commands.dict.del.description'))
        @discord.app_commands.describe(word=_lstr('commands.dict.del.args.word'))
        @discord.app_commands.checks.has_permissions(manage_guild=True)
        async def dict_del(ctx, word: str):
            try:
                await ctx.response.defer()
                lang = self.server_config.get(ctx.guild.id, 'Language')
                read = self.dict_manager.delete(ctx.guild.id, word)
                if read is None:
                    await ctx.edit_original_response(
                        embed=build_embed('dict.del.not_found', lang=lang, word=word)
                    )
                    return
                await ctx.edit_original_response(
                    embed=build_embed('dict.del.success', lang=lang, word=word, read=read)
                )
            except discord.errors.InteractionResponded:
                return
            except discord.errors.HTTPException as e:
                print(f'HTTPException in dict_del: {e}')
            except Exception as e:
                print(f'Exception in dict_del: {e}')
                try:
                    lang = self.server_config.get(ctx.guild.id, 'Language')
                    await ctx.edit_original_response(
                        embed=build_embed('dict.del.failure', lang=lang)
                    )
                except Exception as inner:
                    print(f'Exception in dict_del fallback: {inner}')

        @dict_group.command(name='view', description=_lstr('commands.dict.view.description'))
        @discord.app_commands.describe(
            ephemeral=_lstr('commands.dict.view.args.ephemeral'),
            search=_lstr('commands.dict.view.args.search')
        )
        async def dict_view(ctx, search: Optional[str] = None, ephemeral: bool = False):
            try:
                await ctx.response.defer(ephemeral=ephemeral)
                lang = self.server_config.get(ctx.guild.id, 'Language')
                word_entries  = _load_json(self.dict_manager._guild_path(ctx.guild.id))
                emoji_entries = _load_json(self.dict_manager._guild_emoji_path(ctx.guild.id))

                if not word_entries and not emoji_entries:
                    embed = build_embed('dict.view', lang=lang)
                    embed.description = get_desc('dict.view.empty', lang=lang)
                    await ctx.edit_original_response(embed=embed)
                    return

                if search:
                    word_items  = _filter_entries(word_entries,  search)
                    emoji_items = _filter_entries(emoji_entries, search)
                else:
                    word_items  = list(word_entries.items())
                    emoji_items = list(emoji_entries.items())

                if not word_items and not emoji_items:
                    await ctx.edit_original_response(
                        embed=build_embed('dict.view.not_found', lang=lang, word=search)
                    )
                    return

                paginator = DictViewPaginator(word_items, emoji_items, lang)
                embed = paginator._build_embed()

                if paginator.total_pages <= 1:
                    await ctx.edit_original_response(embed=embed)
                else:
                    msg = await ctx.edit_original_response(embed=embed, view=paginator)
                    paginator.message = msg

            except discord.errors.InteractionResponded:
                return
            except discord.errors.HTTPException as e:
                print(f'HTTPException in dict_view: {e}')
            except Exception as e:
                print(f'Exception in dict_view: {e}')

        @dict_group.error
        async def dict_error(ctx, error):
            if isinstance(error, discord.app_commands.MissingPermissions):
                await ctx.response.send_message(
                    embed=build_embed('dict.error.no_permission'),
                    ephemeral=True
                )

        self.tree.add_command(dict_group)