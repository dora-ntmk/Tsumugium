import os
import re
import json
import sqlite3
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
_TIMESTAMP_RE    = re.compile(r'<t:(\d+):[\s\S]>')
_MENTION_USER_RE = re.compile(r'<@!?(\d+)>')
_MENTION_CH_RE   = re.compile(r'<#(\d+)>')
_MENTION_ROLE_RE = re.compile(r'<@&(\d+)>')
_WWW_RE          = re.compile(r'[wWｗＷ]{2,}')

_URL_PATTERNS = [
    (re.compile(r'https://www.youtube[\w/:%#$&?()~.=+\-!@]+'), 'ユーチューブへのリンク'),
    (re.compile(r'https://youtu[\w/:%#$&?()~.=+\-!@]+'), 'ユーチューブへのリンク'),
    (re.compile(r'https://www.nicovideo[\w/:%#$&?()~.=+\-!@]+'), 'ニコニコへのリンク'),
    (re.compile(r'https://nico[\w/:%#$&?()~.=+\-!@]+'), 'ニコニコへのリンク'),
    (re.compile(r'https://twitter[\w/:%#$&?()~.=+\-!@]+'), 'ツイッターへのリンク'),
    (re.compile(r'https://x[\w/:%#$&?()~.=+\-!@]+'), 'エックスへのリンク'),
    (re.compile(r'https://www.instagram[\w/:%#$&?()~.=+\-!@]+'), 'インスタへのリンク'),
    (re.compile(r'https://www.facebook[\w/:%#$&?()~.=+\-!@]+'), 'フェイスブックへのリンク'),
    (re.compile(r'https://www.tiktok[\w/:%#$&?()~.=+\-!@]+'), 'ティックトックへのリンク'),
    (re.compile(r'https://discord[\w/:%#$&?()~.=+\-!@]+'), 'ディスコードないのリンク'),
    (re.compile(r'https://line[\w/:%#$&?()~.=+\-!@]+'), 'ラインへのリンク'),
    (re.compile(r'https://store.steampowered[\w/:%#$&?()~.=+\-!@]+'), 'スチームへのリンク'),
    (re.compile(r'https://www.ea[\w/:%#$&?()~.=+\-!@]+'), 'イーエーへのリンク'),
    (re.compile(r'https://www.origin[\w/:%#$&?()~.=+\-!@]+'), 'オリジンへのリンク'),
    (re.compile(r'https://store.epicgames[\w/:%#$&?()~.=+\-!@]+'), 'エピックゲームズへのリンク'),
    (re.compile(r'https://www.riotgames[\w/:%#$&?()~.=+\-!@]+'), 'ライアットゲームズへのリンク'),
    (re.compile(r'https://www.xbox[\w/:%#$&?()~.=+\-!@]+'), 'エックスボックスへのリンク'),
    (re.compile(r'https://www.amazon[\w/:%#$&?()~.=+\-!@]+'), 'アマゾンへのリンク'),
    (re.compile(r'https://item.rakuten[\w/:%#$&?()~.=+\-!@]+'), 'らくてんいちばへのリンク'),
    (re.compile(r'https://www.yodobashi[\w/:%#$&?()~.=+\-!@]+'), 'ヨドバシカメラへのリンク'),
    (re.compile(r'https://www.soundhouse[\w/:%#$&?()~.=+\-!@]+'), 'サウンドハウスへのリンク'),
    (re.compile(r'https://www.mapcamera[\w/:%#$&?()~.=+\-!@]+'), 'マップカメラへのリンク'),
    (re.compile(r'https://[\w/:%#$&?()~.=+\-!@]+aliexpress+[\w/:%#$&?()~.=+\-!@]+'), 'アリエクへのリンク'),
    (re.compile(r'http://abehiroshi.la.coocan.jp/+'), 'あべひろしのホームページへのリンク'),
    (re.compile(r'https://[\w/:%#$&?()~.=+\-!@]+'), 'リンク省略'),
    (re.compile(r'http://[\w/:%#$&?()~.=+\-!@]+'), 'リンク省略'),
    (re.compile(r'www\.\S+'), 'リンク省略'),
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


def _is_priority_word(word: str) -> bool:
    """優先辞書に登録すべき語かどうかを判定する。"""
    if _is_emoji_word(word):
        return True
    for pat in (_MENTION_USER_RE, _MENTION_CH_RE, _MENTION_ROLE_RE):
        if pat.search(word):
            return True
    for url_re, _ in _URL_PATTERNS:
        if url_re.search(word):
            return True
    return False


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}



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
    def __init__(self, normal_items: list[tuple[str, str]], priority_items: list[tuple[str, str]], lang: str):
        super().__init__(timeout=120)
        self.normal_items   = normal_items
        self.priority_items = priority_items
        self.lang = lang
        self.page = 0
        self.normal_pages   = (len(normal_items)   + _PAGE_SIZE - 1) // _PAGE_SIZE if normal_items   else 0
        self.priority_pages = (len(priority_items) + _PAGE_SIZE - 1) // _PAGE_SIZE if priority_items else 0
        self.total_pages = self.normal_pages + self.priority_pages
        self.message: discord.Message | None = None
        self._update_buttons()

    def build_dict_embed(self) -> discord.Embed:
        embed = build_embed('dict.view', lang=self.lang)
        prefix = get_desc('dict.view.prefix', lang=self.lang)

        if self.page < self.normal_pages:
            start = self.page * _PAGE_SIZE
            page_items = self.normal_items[start:start + _PAGE_SIZE]
            section = get_desc('dict.view.section_normal', lang=self.lang)
            section_page = self.page + 1
            section_total = self.normal_pages
        else:
            priority_page = self.page - self.normal_pages
            start = priority_page * _PAGE_SIZE
            page_items = self.priority_items[start:start + _PAGE_SIZE]
            section = get_desc('dict.view.section_priority', lang=self.lang)
            section_page = priority_page + 1
            section_total = self.priority_pages

        header = get_desc('dict.view.header', lang=self.lang)
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

        page_str = get_desc('dict.view.page', lang=self.lang).format(
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
        await interaction.response.edit_message(embed=self.build_dict_embed(), view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.normal_pages:
            self.page = (self.page + 1) % self.normal_pages
        else:
            pp = (self.page - self.normal_pages + 1) % self.priority_pages
            self.page = self.normal_pages + pp
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_dict_embed(), view=self)

    @discord.ui.button(label="📚", style=discord.ButtonStyle.primary)
    async def jump_normal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_dict_embed(), view=self)

    @discord.ui.button(label="⭐", style=discord.ButtonStyle.primary)
    async def jump_priority_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.normal_pages
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_dict_embed(), view=self)

    async def on_timeout(self):
        if self.message is not None:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except Exception as e:
                print(f"on_timeout: {e}")
                pass


class DictManager:
    def __init__(self, db_path: str = 'db/dict.db'):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                guild_id    TEXT    NOT NULL,
                word        TEXT    NOT NULL,
                reading     TEXT    NOT NULL,
                is_priority INTEGER NOT NULL DEFAULT 0,
                added_at    INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                PRIMARY KEY (guild_id, word)
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_guild ON entries (guild_id)"
        )
        self._conn.commit()
        emoji_ja_data = _load_json('db/emoji_ja.json')
        self._emoji_ja: dict = {
            k: v['short_name']
            for k, v in emoji_ja_data.items()
            if isinstance(v, dict) and 'short_name' in v
        }

    def init_guild(self, guild_id: int):
        pass  # テーブルはすでに存在。エントリは add() 時に生成される

    def remove_guild(self, guild_id: int):
        try:
            self._conn.execute(
                "DELETE FROM entries WHERE guild_id = ?", (str(guild_id),)
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f'辞書削除失敗 guild_id={guild_id}: {e}')

    def add(self, guild_id: int, word: str, read: str) -> bool:
        """Returns True if overwriting an existing entry."""
        if len(read) > 50:
            raise ValueError('too_long')
        is_priority = 1 if _is_priority_word(word) else 0
        gid = str(guild_id)
        cur = self._conn.execute(
            "SELECT 1 FROM entries WHERE guild_id = ? AND word = ?", (gid, word)
        )
        overwrite = cur.fetchone() is not None
        self._conn.execute(
            """INSERT OR REPLACE INTO entries (guild_id, word, reading, is_priority, added_at)
               VALUES (?, ?, ?, ?, strftime('%s', 'now'))""",
            (gid, word, read, is_priority)
        )
        self._conn.commit()
        return overwrite

    def delete(self, guild_id: int, word: str) -> Optional[str]:
        """Returns the removed read string, or None if not found."""
        gid = str(guild_id)
        cur = self._conn.execute(
            "SELECT reading FROM entries WHERE guild_id = ? AND word = ?", (gid, word)
        )
        row = cur.fetchone()
        if row is None:
            return None
        self._conn.execute(
            "DELETE FROM entries WHERE guild_id = ? AND word = ?", (gid, word)
        )
        self._conn.commit()
        return row[0]

    def get_entries(self, guild_id: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Returns (normal_items, priority_items), each as list of (word, reading) in added_at DESC order."""
        gid = str(guild_id)
        cur = self._conn.execute(
            "SELECT word, reading, is_priority FROM entries WHERE guild_id = ? ORDER BY added_at DESC",
            (gid,)
        )
        normal = []
        priority = []
        for word, reading, is_pri in cur.fetchall():
            if is_pri:
                priority.append((word, reading))
            else:
                normal.append((word, reading))
        return normal, priority

    def preprocess_text(self, text: str, guild_id: int, guild, attachments, mentions=None) -> tuple[str, list[tuple[int, int]]]:
        segments = [(text, False)]
        gid = str(guild_id)

        # -1. 優先辞書（URL処理より前に適用）
        cur = self._conn.execute(
            "SELECT word, reading FROM entries WHERE guild_id = ? AND is_priority = 1 ORDER BY added_at DESC",
            (gid,)
        )
        d = dict(cur.fetchall())
        if d:
            segments = _apply_dict(segments, d)

        # 0. URL処理（辞書より前に実行してURLを保護）
        for url_re, url_read in _URL_PATTERNS:
            segments = _apply_regex(segments, url_re, url_read + ',')

        # 0b. www pattern → N × わら（URL内の www は protected=True でスキップ済み）
        def _www_replace(m):
            return 'わら' * len(m.group(0)) + ','
        segments = _apply_regex(segments, _WWW_RE, _www_replace)

        # 1a. Spoilers
        segments = _apply_regex(segments, _SPOILER_RE, ',ネタバレ,')
        # 1b. Strikethrough
        segments = _apply_regex(segments, _STRIKE_RE, ',取り消し線,')
        # 1c. Code blocks
        segments = _apply_regex(segments, _CODE_BLOCK_RE, ',コードブロック,')
        # 1d. Inline code
        segments = _apply_regex(segments, _INLINE_CODE_RE, ',コードブロック,')
        # 1e. Time stamp
        segments = _apply_regex(segments, _TIMESTAMP_RE, ',タイムスタンプ,')

        # 1f. User mentions
        mention_map = {str(u.id): u.display_name for u in (mentions or [])}

        def _mention_user(m):
            uid = m.group(1)
            if uid in mention_map:
                return f'{mention_map[uid]}へのメンション,'
            if guild:
                member = guild.get_member(int(uid))
                if member:
                    return f'{member.display_name}へのメンション,'
            return 'ユーザーへのメンション,'

        segments = _apply_regex(segments, _MENTION_USER_RE, _mention_user)

        # 1g. Channel links
        def _channel_link(m):
            if guild:
                ch = guild.get_channel(int(m.group(1)))
                if ch:
                    return f'{ch.name}へのリンク,'
            return 'チャンネルへのリンク,'

        segments = _apply_regex(segments, _MENTION_CH_RE, _channel_link)

        # 1h. Role mentions
        def _mention_role(m):
            if guild:
                role = guild.get_role(int(m.group(1)))
                if role:
                    return f'{role.name}へのメンション,'
            return 'ロールへのメンション,'

        segments = _apply_regex(segments, _MENTION_ROLE_RE, _mention_role)

        # 1i. Newlines
        segments = _apply_regex(segments, re.compile(r'\n'), ',')
        # 1j. Spaces (half-width and full-width)
        segments = _apply_regex(segments, re.compile(r'[ \u3000]+'), ',')

        # 2. 優先辞書 → 通常辞書 → 共通辞書
        cur = self._conn.execute(
            "SELECT word, reading FROM entries WHERE guild_id = ? AND is_priority = 1 ORDER BY added_at DESC",
            (gid,)
        )
        d = dict(cur.fetchall())
        if d:
            segments = _apply_dict(segments, d)

        cur = self._conn.execute(
            "SELECT word, reading FROM entries WHERE guild_id = ? AND is_priority = 0 ORDER BY added_at DESC",
            (gid,)
        )
        d = dict(cur.fetchall())
        if d:
            segments = _apply_dict(segments, d)

        cur = self._conn.execute(
            "SELECT word, reading FROM entries WHERE guild_id = '__common__' ORDER BY added_at DESC"
        )
        d = dict(cur.fetchall())
        if d:
            segments = _apply_dict(segments, d)

        # 3. emoji_ja short_name mapping
        if self._emoji_ja:
            segments = _apply_dict(segments, self._emoji_ja)

        # 4. Join with replaced range tracking
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

        # 5. Attachments (appended after; ranges not tracked)
        for att in attachments:
            _, ext = os.path.splitext(att.filename.lower())
            if ext in _IMAGE_EXTS:
                result += ',画像ファイル'
            elif ext in _VIDEO_EXTS:
                result += ',動画ファイル'
            elif ext in _AUDIO_EXTS:
                result += ',音声ファイル'
            else:
                result += ',添付ファイル'

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
        @discord.app_commands.checks.has_permissions()
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
        @discord.app_commands.checks.has_permissions()
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
                normal_entries, priority_entries = self.dict_manager.get_entries(ctx.guild.id)

                if not normal_entries and not priority_entries:
                    embed = build_embed('dict.view', lang=lang)
                    embed.description = get_desc('dict.view.empty', lang=lang)
                    await ctx.edit_original_response(embed=embed)
                    return

                if search:
                    normal_items   = _filter_entries(dict(normal_entries),   search)
                    priority_items = _filter_entries(dict(priority_entries), search)
                else:
                    normal_items   = normal_entries
                    priority_items = priority_entries

                if not normal_items and not priority_items:
                    await ctx.edit_original_response(
                        embed=build_embed('dict.view.not_found', lang=lang, word=search)
                    )
                    return

                paginator = DictViewPaginator(normal_items, priority_items, lang)
                embed = paginator.build_dict_embed()

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