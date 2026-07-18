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
from word_dict import DictManager, _filter_entries
from dict_view import DictViewPaginator
from presentation.embeds import EmbedType, make_embed
from presentation.error_handler import handle_internal_error


class SoundDict:
  def __init__(self, dict_manager: DictManager):
    self._dm = dict_manager

  def add(self, guild_id: int, word: str, sound_id: str,
          full_match: bool = True, trigger_user_id: Optional[str] = None) -> bool:
    return self._dm.add_sound(guild_id, word, sound_id, full_match=full_match, trigger_user_id=trigger_user_id)

  def delete(self, guild_id: int, word: str) -> Optional[str]:
    return self._dm.delete_sound(guild_id, word)

  def get_entries(self, guild_id: int):
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
      description='音声辞書を管理します'
    )

    @sounddict_group.command(name='add', description='音声辞書に単語を追加します')
    @discord.app_commands.describe(
      word='追加する単語',
      sound='サウンドボードのID',
      read='読み方（省略可・50文字以内）',
      full_match='Falseにすると単語が含まれているだけで再生されます（デフォルト: True=完全一致）',
      trigger_user='このユーザー/Botが発言したときのみ再生します（省略可）'
    )
    @discord.app_commands.checks.has_permissions()
    async def sounddict_add(ctx, word: str, sound: str, read: Optional[str] = None,
                            full_match: bool = True, trigger_user: Optional[discord.Member] = None):
      try:
        await ctx.response.defer()
        sounds = self.sound_boards.get_sounds(ctx.guild.id)
        sound_id = next((sid for sid, name in sounds if name == sound), None)
        if sound_id is None:
          await ctx.edit_original_response(
            embed=make_embed(
              'サウンドが見つかりませんでした',
              f'サウンドボードに「{sound}」はありません。候補から選択してください。',
              embed_type=EmbedType.ERROR,
            )
          )
          return
        trigger_user_id = str(trigger_user.id) if trigger_user else None
        match_mode = '完全一致' if full_match else '部分一致'
        trigger_label = trigger_user.display_name if trigger_user else 'なし'
        sound_overwrite = self.sound_dict.add(ctx.guild.id, word, sound_id, full_match=full_match, trigger_user_id=trigger_user_id)
        if read is not None:
          try:
            dict_overwrite = self.dict_manager.add(ctx.guild.id, word, read)
          except ValueError:
            dict_overwrite = False
          overwrite = sound_overwrite or dict_overwrite
          title = '辞書への上書きが成功しました' if overwrite else '辞書への追加が成功しました'
          description = '音声辞書と読み上げ辞書の単語が上書きされました' if overwrite else '音声辞書と読み上げ辞書に単語が追加されました'
          embed = make_embed(title, description, embed_type=EmbedType.SUCCESS)
          embed.add_field(
            name='登録内容',
            value=f'`{word}` → 音声: `{sound}`　読み: `{read}`',
            inline=False,
          )
        else:
          title = '音声辞書への上書きが成功しました' if sound_overwrite else '音声辞書への追加が成功しました'
          description = '音声辞書の単語が上書きされました' if sound_overwrite else '音声辞書に単語が追加されました'
          embed = make_embed(title, description, embed_type=EmbedType.SUCCESS)
          embed.add_field(
            name='登録内容',
            value=f'`{word}` → `{sound}`',
            inline=False,
          )
        embed.add_field(
          name='オプション',
          value=f'一致モード: `{match_mode}`　発話ユーザー: `{trigger_label}`',
          inline=False,
        )
        await ctx.edit_original_response(embed=embed)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f'HTTPException in sounddict_add: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "sounddict_add")

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

    @sounddict_group.command(name='del', description='音声辞書から単語を削除します')
    @discord.app_commands.describe(
      word='削除する単語',
      both='Trueにすると読み上げ辞書からも削除します'
    )
    @discord.app_commands.checks.has_permissions()
    async def sounddict_del(ctx, word: str, both: bool = False):
      try:
        await ctx.response.defer()
        sound_id = self.sound_dict.delete(ctx.guild.id, word)
        if sound_id is None:
          await ctx.edit_original_response(
            embed=make_embed(
              '音声辞書にその単語は見つかりませんでした',
              f'音声辞書に`{word}`はありません。確認して入れなおしてください。',
              embed_type=EmbedType.ERROR,
            )
          )
          return
        if both:
          self.dict_manager.delete(ctx.guild.id, word)
        embed = make_embed(
          '音声辞書からの削除が成功しました',
          '音声辞書から単語が削除されました',
          embed_type=EmbedType.SUCCESS,
        )
        embed.add_field(name='削除内容', value=f'`{word}`', inline=False)
        await ctx.edit_original_response(embed=embed)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f'HTTPException in sounddict_del: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "sounddict_del")

    # noinspection PyUnusedLocal
    @sounddict_del.autocomplete("word")
    async def sounddict_del_word_autocomplete(ctx, current: str):
      normal, priority = self.sound_dict.get_entries(ctx.guild.id)
      all_words = [word for word, _, _, _ in priority + normal]
      filtered = [
        discord.app_commands.Choice(name=word, value=word)
        for word in all_words
        if current in word
      ]
      return filtered[:25]

    @sounddict_group.command(name='view', description='音声辞書の内容を確認します')
    @discord.app_commands.describe(
      ephemeral='Trueにすると自分にだけ見える形で表示します',
      search='検索する文字列（部分一致）'
    )
    async def sounddict_view(ctx, search: Optional[str] = None, ephemeral: bool = False):
      try:
        await ctx.response.defer(ephemeral=ephemeral)
        normal_entries, priority_entries = self.sound_dict.get_entries(ctx.guild.id)

        if not normal_entries and not priority_entries:
          embed = make_embed('音声辞書一覧', '音声辞書に登録された単語はありません')
          await ctx.edit_original_response(embed=embed)
          return

        sounds_map = {sid: name for sid, name in self.sound_boards.get_sounds(ctx.guild.id)}

        def make_label(fm, uid):
          parts = []
          if not fm:
            parts.append('部分一致')
          if uid:
            m = ctx.guild.get_member(int(uid))
            parts.append(f"@{m.display_name}" if m else f"uid:{uid}")
          return f" [{', '.join(parts)}]" if parts else ""

        def resolve(entries):
          return [(w, sounds_map.get(sid, sid) + make_label(fm, uid)) for w, sid, fm, uid in entries]

        if search:
          normal_items   = _filter_entries(dict(resolve(normal_entries)),   search)
          priority_items = _filter_entries(dict(resolve(priority_entries)), search)
        else:
          normal_items   = resolve(normal_entries)
          priority_items = resolve(priority_entries)

        if not normal_items and not priority_items:
          await ctx.edit_original_response(
            embed=make_embed(
              '見つかりませんでした',
              f'「{search}」に一致する単語は音声辞書にありません',
              embed_type=EmbedType.ERROR,
            )
          )
          return

        paginator = DictViewPaginator(normal_items, priority_items, 'sounddict')
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
        await handle_internal_error(ctx, e, "sounddict_view")

    @sounddict_group.error
    async def sounddict_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=make_embed(
            '権限エラー',
            'サーバー管理権限が必要です',
            embed_type=EmbedType.ERROR,
          ),
          ephemeral=True
        )

    self.tree.add_command(sounddict_group)
