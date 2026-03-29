"""
ファイル名：word_dict.py
作者：どら
説明：テキスト辞書モジュール。
      単語の読み方・サウンドボード ID を SQLite で管理する DictManager、
      およびスラッシュコマンド /dict (add / del / view) を実装する WordDict を提供する。
      テキスト前処理は swap モジュールに委譲する。
依存関係：discord.py
"""
import os
import json
import sqlite3
import unicodedata
import discord
from typing import Optional
from messages import build_embed, get_desc, handle_internal_error
from config import EMOJI_JA_JSON
from dict_view import DictViewPaginator
import swap
from swap import (
  _CUSTOM_EMOJI_RE, _STANDARD_EMOJI_RE,
  _MENTION_USER_RE, _MENTION_CH_RE, _MENTION_ROLE_RE,
  _URL_PATTERNS,
)


def _lstr(key: str) -> discord.app_commands.locale_str:
  return discord.app_commands.locale_str(get_desc(key), key=key)


def _is_emoji_word(word: str) -> bool:
  if _CUSTOM_EMOJI_RE.findall(word):
    return True
  if _STANDARD_EMOJI_RE.findall(word):
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


class DictManager:
  def __init__(self, db_path):
    self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute("""
                       CREATE TABLE IF NOT EXISTS dict (
                                                           guild_id    TEXT    NOT NULL,
                                                           word        TEXT    NOT NULL,
                                                           reading     TEXT,
                                                           sound_id    TEXT,
                                                           is_priority INTEGER NOT NULL DEFAULT 0,
                                                           added_at    INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                           PRIMARY KEY (guild_id, word)
                           )
                       """)
    self._conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_dict_guild ON dict (guild_id)"
    )
    self._conn.commit()
    emoji_ja_data = _load_json(EMOJI_JA_JSON)
    self._emoji_ja: dict = {
      k: v['short_name']
      for k, v in emoji_ja_data.items()
      if isinstance(v, dict) and 'short_name' in v
    }

  def remove_guild(self, guild_id: int):
    try:
      self._conn.execute(
        "DELETE FROM dict WHERE guild_id = ?", (str(guild_id),)
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
      "SELECT reading FROM dict WHERE guild_id = ? AND word = ?", (gid, word)
    )
    row = cur.fetchone()
    overwrite = row is not None and row[0] is not None
    self._conn.execute(
      """INSERT INTO dict (guild_id, word, reading, sound_id, is_priority, added_at)
         VALUES (?, ?, ?, NULL, ?, strftime('%s', 'now'))
         ON CONFLICT(guild_id, word) DO UPDATE SET
           reading     = excluded.reading,
           is_priority = excluded.is_priority,
           added_at    = excluded.added_at""",
      (gid, word, read, is_priority)
    )
    self._conn.commit()
    return overwrite

  def delete(self, guild_id: int, word: str) -> Optional[str]:
    """Returns the removed read string, or None if not found."""
    gid = str(guild_id)
    cur = self._conn.execute(
      "SELECT reading, sound_id FROM dict WHERE guild_id = ? AND word = ?", (gid, word)
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
      return None
    reading, sound_id = row
    if sound_id is not None:
      self._conn.execute(
        "UPDATE dict SET reading = NULL WHERE guild_id = ? AND word = ?", (gid, word)
      )
    else:
      self._conn.execute(
        "DELETE FROM dict WHERE guild_id = ? AND word = ?", (gid, word)
      )
    self._conn.commit()
    return reading

  def get_entries(self, guild_id: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Returns (normal_items, priority_items), each as list of (word, reading) in added_at DESC order."""
    gid = str(guild_id)
    cur = self._conn.execute(
      "SELECT word, reading, is_priority FROM dict WHERE guild_id = ? AND reading IS NOT NULL ORDER BY added_at DESC",
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

  def add_sound(self, guild_id: int, word: str, sound_id: str) -> bool:
    """Returns True if overwriting an existing sound entry."""
    is_priority = 1 if _is_priority_word(word) else 0
    gid = str(guild_id)
    cur = self._conn.execute(
      "SELECT sound_id FROM dict WHERE guild_id = ? AND word = ?", (gid, word)
    )
    row = cur.fetchone()
    overwrite = row is not None and row[0] is not None
    self._conn.execute(
      """INSERT INTO dict (guild_id, word, sound_id, reading, is_priority, added_at)
         VALUES (?, ?, ?, NULL, ?, strftime('%s', 'now'))
         ON CONFLICT(guild_id, word) DO UPDATE SET
           sound_id    = excluded.sound_id,
           is_priority = excluded.is_priority,
           added_at    = excluded.added_at""",
      (gid, word, sound_id, is_priority)
    )
    self._conn.commit()
    return overwrite

  def delete_sound(self, guild_id: int, word: str) -> Optional[str]:
    """Returns the removed sound_id, or None if not found."""
    gid = str(guild_id)
    cur = self._conn.execute(
      "SELECT sound_id, reading FROM dict WHERE guild_id = ? AND word = ?", (gid, word)
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
      return None
    sound_id, reading = row
    if reading is not None:
      self._conn.execute(
        "UPDATE dict SET sound_id = NULL WHERE guild_id = ? AND word = ?", (gid, word)
      )
    else:
      self._conn.execute(
        "DELETE FROM dict WHERE guild_id = ? AND word = ?", (gid, word)
      )
    self._conn.commit()
    return sound_id

  def get_sound_entries(self, guild_id: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Returns (normal_items, priority_items), each as list of (word, sound_id) in added_at DESC order."""
    gid = str(guild_id)
    cur = self._conn.execute(
      "SELECT word, sound_id, is_priority FROM dict WHERE guild_id = ? AND sound_id IS NOT NULL ORDER BY added_at DESC",
      (gid,)
    )
    normal = []
    priority = []
    for word, sound_id, is_pri in cur.fetchall():
      if is_pri:
        priority.append((word, sound_id))
      else:
        normal.append((word, sound_id))
    return normal, priority

  def invalidate_sound(self, guild_id, sound_id: str):
    """指定 sound_id を参照する dict レコードを更新する。
    reading が NULL なら行削除、reading が存在すれば sound_id を NULL 化。"""
    gid = str(guild_id)
    sid = str(sound_id)
    self._conn.execute(
      "DELETE FROM dict WHERE guild_id = ? AND sound_id = ? AND reading IS NULL",
      (gid, sid)
    )
    self._conn.execute(
      "UPDATE dict SET sound_id = NULL WHERE guild_id = ? AND sound_id = ? AND reading IS NOT NULL",
      (gid, sid)
    )
    self._conn.commit()

  def delete_entry(self, guild_id: int, word: str):
    """reading と sound_id 両方を削除する（行ごと削除）。"""
    gid = str(guild_id)
    self._conn.execute(
      "DELETE FROM dict WHERE guild_id = ? AND word = ?", (gid, word)
    )
    self._conn.commit()

  def preprocess_text(self, text: str, guild_id: int, guild, attachments, mentions=None) -> tuple[str, list[tuple[int, int]], str | None]:
    return swap.preprocess_text(text, guild_id, self._conn, self._emoji_ja, guild, attachments, mentions)


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
        await handle_internal_error(ctx, e, "dict_add", lang=self.server_config.get(ctx.guild.id, 'Language'))

    @dict_group.command(name='del', description=_lstr('commands.dict.del.description'))
    @discord.app_commands.describe(
      word=_lstr('commands.dict.del.args.word'),
      both=_lstr('commands.dict.del.args.both')
    )
    @discord.app_commands.checks.has_permissions()
    async def dict_del(ctx, word: str, both: bool = False):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, 'Language')
        read = self.dict_manager.delete(ctx.guild.id, word)
        if read is None:
          await ctx.edit_original_response(
            embed=build_embed('dict.del.not_found', lang=lang, word=word)
          )
          return
        if both:
          self.dict_manager.delete_sound(ctx.guild.id, word)
        await ctx.edit_original_response(
          embed=build_embed('dict.del.success', lang=lang, word=word, read=read)
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f'HTTPException in dict_del: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "dict_del", lang=self.server_config.get(ctx.guild.id, 'Language'))

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

        paginator = DictViewPaginator(normal_items, priority_items, lang, 'dict')
        embed = paginator.build_embed()

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
        await handle_internal_error(ctx, e, "dict_view", lang=self.server_config.get(ctx.guild.id, 'Language'))

    @dict_group.error
    async def dict_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=build_embed('dict.error.no_permission'),
          ephemeral=True
        )

    self.tree.add_command(dict_group)