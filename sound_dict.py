"""
ファイル名：sound_dict.py
作者：どら
説明：音声辞書モジュール。
      キーワードとサウンドボード音声の紐付け管理 (SoundDict)、
      Discord サウンドボード一覧の DB 同期 (UpdateSoundBoards)、
      およびスラッシュコマンド /sounddict (add / del / view) の実装 (SoundDictView) を提供する。
      view サブコマンドでは DictViewPaginator によるページング表示に対応する。
依存関係：discord.py, requests
"""
import sqlite3
import discord
import requests
from typing import Optional
from messages import build_embed, get_desc, handle_internal_error
from word_dict import DictManager, _filter_entries
from dict_view import DictViewPaginator


def _lstr(key: str) -> discord.app_commands.locale_str:
  return discord.app_commands.locale_str(get_desc(key), key=key)


class SoundDict:
  def __init__(self, dict_manager: DictManager):
    self._dm = dict_manager

  def add(self, guild_id: int, word: str, sound_id: str) -> bool:
    return self._dm.add_sound(guild_id, word, sound_id)

  def delete(self, guild_id: int, word: str) -> Optional[str]:
    return self._dm.delete_sound(guild_id, word)

  def get_entries(self, guild_id: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    return self._dm.get_sound_entries(guild_id)


class UpdateSoundBoards:
  def __init__(self, db_path, dict_manager=None):
    self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute("""
      CREATE TABLE IF NOT EXISTS soundboards (
        guild_id  TEXT NOT NULL,
        sound_id  TEXT NOT NULL,
        name      TEXT NOT NULL,
        PRIMARY KEY (guild_id, sound_id)
      )
    """)
    self._conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_soundboards_guild ON soundboards (guild_id)"
    )
    self._conn.commit()
    self._dict_manager = dict_manager

  def remove_guild(self, guild_id: int):
    try:
      self._conn.execute(
        "DELETE FROM soundboards WHERE guild_id = ?", (str(guild_id),)
      )
      self._conn.commit()
    except sqlite3.Error as e:
      print(f'サウンドボード一覧削除失敗 guild_id={guild_id}: {e}')

  def add(self, guild_id: int, sound_id: int, name: str):
    gid = str(guild_id)
    sid = str(sound_id)
    self._conn.execute(
      """INSERT OR REPLACE INTO soundboards (guild_id, sound_id, name)
         VALUES (?, ?, ?)""",
      (gid, sid, name)
    )
    self._conn.commit()

  def delete(self, guild_id: int, sound_id: int):
    gid = str(guild_id)
    sid = str(sound_id)
    self._conn.execute(
      "DELETE FROM soundboards WHERE guild_id = ? AND sound_id = ?", (gid, sid)
    )
    self._conn.commit()
    if self._dict_manager:
      self._dict_manager.invalidate_sound(gid, sid)

  def get_sounds(self, guild_id: int) -> list[tuple[str, str]]:
    """Returns list of (sound_id, name) for the guild."""
    gid = str(guild_id)
    cur = self._conn.cursor()
    cur.execute("SELECT sound_id, name FROM soundboards WHERE guild_id = ?", (gid,))
    return cur.fetchall()

  def refresh(self, gid: str, token: str):
    res = requests.get(
      f'https://discord.com/api/v10/guilds/{gid}/soundboard-sounds',
      headers={
        'Authorization': f'Bot {token}',
        'Content-Type': 'application/json',
      })
    res.raise_for_status()
    d = res.json()
    current_sound_names = list(s["name"] for s in d["items"])
    current_sound_ids = list(str(s["sound_id"]) for s in d["items"])
    cur = self._conn.cursor()
    cur.execute(
      "SELECT sound_id FROM soundboards WHERE guild_id = ?", (gid,)
    )
    rows = cur.fetchall()
    db_sound_ids = list(row[0] for row in rows)
    db_insert = []
    db_delete = []
    for n in range(len(current_sound_ids)):
      if current_sound_ids[n] not in db_sound_ids:
        db_insert.append((gid, current_sound_ids[n], current_sound_names[n]))
    for n in range(len(db_sound_ids)):
      if db_sound_ids[n] not in current_sound_ids:
        db_delete.append((gid, db_sound_ids[n]))
    cur.executemany(
      "INSERT INTO soundboards (guild_id, sound_id, name) VALUES (?, ?, ?)", db_insert
    )
    cur.executemany(
      "DELETE FROM soundboards WHERE guild_id = ? AND sound_id = ?", db_delete
    )
    self._conn.commit()
    if self._dict_manager:
      for gid_del, sid_del in db_delete:
        self._dict_manager.invalidate_sound(gid_del, sid_del)

class SoundDictView:
  def __init__(self, client, tree, sound_dict: SoundDict, dict_manager: DictManager, server_config, sound_boards: UpdateSoundBoards):
    self.client = client
    self.tree = tree
    self.sound_dict = sound_dict
    self.dict_manager = dict_manager
    self.server_config = server_config
    self.sound_boards = sound_boards
    self._register()

  def _register(self):
    sounddict_group = discord.app_commands.Group(
      name='sounddict',
      description=_lstr('commands.sounddict._group')
    )

    @sounddict_group.command(name='add', description=_lstr('commands.sounddict.add.description'))
    @discord.app_commands.describe(
      word=_lstr('commands.sounddict.add.args.word'),
      sound=_lstr('commands.sounddict.add.args.sound'),
      read=_lstr('commands.sounddict.add.args.read')
    )
    @discord.app_commands.checks.has_permissions()
    async def sounddict_add(ctx, word: str, sound: str, read: Optional[str] = None):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, 'Language')
        sounds = self.sound_boards.get_sounds(ctx.guild.id)
        sound_id = next((sid for sid, name in sounds if name == sound), None)
        if sound_id is None:
          await ctx.edit_original_response(
            embed=build_embed('sounddict.add.not_found', lang=lang, sound=sound)
          )
          return
        sound_overwrite = self.sound_dict.add(ctx.guild.id, word, sound_id)
        if read is not None:
          try:
            dict_overwrite = self.dict_manager.add(ctx.guild.id, word, read)
          except ValueError:
            dict_overwrite = False
          overwrite = sound_overwrite or dict_overwrite
          key = 'sounddict.add.overwrite_both' if overwrite else 'sounddict.add.success_both'
          await ctx.edit_original_response(
            embed=build_embed(key, lang=lang, word=word, sound=sound, read=read)
          )
        else:
          key = 'sounddict.add.overwrite' if sound_overwrite else 'sounddict.add.success'
          await ctx.edit_original_response(
            embed=build_embed(key, lang=lang, word=word, sound=sound)
          )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f'HTTPException in sounddict_add: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "sounddict_add", lang=self.server_config.get(ctx.guild.id, 'Language'))

    # noinspection PyUnusedLocal
    @sounddict_add.autocomplete("sound")
    async def sound_autocomplete(ctx, current: str):
      sounds = self.sound_boards.get_sounds(ctx.guild.id)
      filtered = [
        discord.app_commands.Choice(name=name, value=name)
        for _, name in sounds
        if current in name
      ]
      return filtered[:25]

    @sounddict_group.command(name='del', description=_lstr('commands.sounddict.del.description'))
    @discord.app_commands.describe(
      word=_lstr('commands.sounddict.del.args.word'),
      both=_lstr('commands.sounddict.del.args.both')
    )
    @discord.app_commands.checks.has_permissions()
    async def sounddict_del(ctx, word: str, both: bool = False):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, 'Language')
        sound_id = self.sound_dict.delete(ctx.guild.id, word)
        if sound_id is None:
          await ctx.edit_original_response(
            embed=build_embed('sounddict.del.not_found', lang=lang, word=word)
          )
          return
        if both:
          self.dict_manager.delete(ctx.guild.id, word)
        await ctx.edit_original_response(
          embed=build_embed('sounddict.del.success', lang=lang, word=word)
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f'HTTPException in sounddict_del: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "sounddict_del", lang=self.server_config.get(ctx.guild.id, 'Language'))

    @sounddict_group.command(name='view', description=_lstr('commands.sounddict.view.description'))
    @discord.app_commands.describe(
      ephemeral=_lstr('commands.sounddict.view.args.ephemeral'),
      search=_lstr('commands.sounddict.view.args.search')
    )
    async def sounddict_view(ctx, search: Optional[str] = None, ephemeral: bool = False):
      try:
        await ctx.response.defer(ephemeral=ephemeral)
        lang = self.server_config.get(ctx.guild.id, 'Language')
        normal_entries, priority_entries = self.sound_dict.get_entries(ctx.guild.id)

        if not normal_entries and not priority_entries:
          embed = build_embed('sounddict.view', lang=lang)
          embed.description = get_desc('sounddict.view.empty', lang=lang)
          await ctx.edit_original_response(embed=embed)
          return

        sounds_map = {sid: name for sid, name in self.sound_boards.get_sounds(ctx.guild.id)}

        def resolve(entries):
          return [(w, sounds_map.get(sid, sid)) for w, sid in entries]

        if search:
          normal_items   = _filter_entries(dict(resolve(normal_entries)),   search)
          priority_items = _filter_entries(dict(resolve(priority_entries)), search)
        else:
          normal_items   = resolve(normal_entries)
          priority_items = resolve(priority_entries)

        if not normal_items and not priority_items:
          await ctx.edit_original_response(
            embed=build_embed('sounddict.view.not_found', lang=lang, word=search)
          )
          return

        paginator = DictViewPaginator(normal_items, priority_items, lang, 'sounddict')
        embed = paginator.build_embed()

        if paginator.total_pages <= 1:
          await ctx.edit_original_response(embed=embed)
        else:
          msg = await ctx.edit_original_response(embed=embed, view=paginator)
          paginator.message = msg

      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f'HTTPException in sounddict_view: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "sounddict_view", lang=self.server_config.get(ctx.guild.id, 'Language'))

    @sounddict_group.error
    async def sounddict_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=build_embed('sounddict.error.no_permission'),
          ephemeral=True
        )

    self.tree.add_command(sounddict_group)