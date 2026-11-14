"""
ファイル名：word_dict.py
作者：どら
説明：テキスト辞書モジュール。
      単語の読み方・サウンドボード ID を SQLite で管理する DictManager、
      およびスラッシュコマンド /dict (add / del / view / view-common / export / import) を実装する WordDict を提供する。
      テキスト前処理は swap モジュールに委譲する。
依存関係：discord.py
"""
import os
import json
import unicodedata
import discord
from typing import Optional
from config import EMOJI_JA_JSON
from dict_view import DictViewPaginator
from presentation.embeds import EmbedType, make_embed
from presentation.error_handler import handle_internal_error
from repositories.dictionary_repository import DictionaryRepository
from models.preprocess_result import PreprocessResult
from services import dictionary_transfer_service as dict_transfer
from services.error_notification_service import ensure_error_notifier
import io
import swap
from swap import (
  _CUSTOM_EMOJI_RE, _STANDARD_EMOJI_RE,
  _MENTION_USER_RE, _MENTION_CH_RE, _MENTION_ROLE_RE,
  _URL_PATTERNS,
)

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

def replace_word(word: str) -> str:
  def replace(match):
    custom_emoji = match.group(0)
    emoji_name = custom_emoji.rsplit(':', 1)[0].split(':', 1)[1]
    return f':{emoji_name}:'
  return _CUSTOM_EMOJI_RE.sub(replace, word)

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


COMMON_GUILD_ID = '__common__'
COMMON_DICT_TITLE = '共通辞書一覧'


def _normalize(s: str) -> str:
  """大文字小文字・半角全角を統一する。"""
  return unicodedata.normalize('NFKC', s).lower()


def _filter_entries(entries: dict, word: str) -> list[tuple[str, str]]:
  """登録・更新順を維持したまま、キーまたは値の部分一致で絞り込む。"""
  nword = _normalize(word)
  return [
    (key, value)
    for key, value in entries.items()
    if nword in _normalize(key) or nword in _normalize(value)
  ]


class DictManager:
  def __init__(self, db_path, error_notifier=None):
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.repository = DictionaryRepository(db_path, self.error_notifier)
    emoji_ja_data = _load_json(EMOJI_JA_JSON)
    self._emoji_ja: dict = {
      k: v['short_name']
      for k, v in emoji_ja_data.items()
      if isinstance(v, dict) and 'short_name' in v
    }

  def remove_guild(self, guild_id: int):
    self.repository.remove_guild(guild_id)

  def close(self) -> None:
    self.repository.close()

  def add(self, guild_id: int, word: str, read: str) -> bool:
    """Returns True if overwriting an existing entry."""
    if len(read) > 50:
      raise ValueError('too_long')
    return self.repository.add_reading(
      guild_id,
      word,
      read,
      is_priority=_is_priority_word(word),
    )

  def delete(self, guild_id: int, word: str) -> Optional[str]:
    """Returns the removed read string, or None if not found."""
    return self.repository.delete_reading(guild_id, word)

  def get_entries(self, guild_id: int) -> list[tuple[str, str]]:
    """Returns (word, reading) entries in added_at DESC order."""
    return self.repository.get_reading_entries(guild_id)

  def export_text(self, guild_id: int) -> tuple[str, int, int]:
    """このサーバーの読み辞書を、.dictファイルの本文・書き出した件数・
    読みにカンマがあって書き出さなかった件数で返す（古い順）。"""
    entries = list(reversed(self.repository.get_reading_entries(guild_id)))
    exported = sum(1 for _, reading in entries if dict_transfer.is_exportable(reading))
    return dict_transfer.format_export(entries), exported, len(entries) - exported

  def import_data(self, guild_id: int, data: bytes) -> dict_transfer.ImportResult:
    """.dictファイルを読み込む。すでに読みがある単語は上書きしない。

    ファイル全体を断るときは ImportRejected を投げる。
    """
    parsed = dict_transfer.parse_import(dict_transfer.decode_import(data))
    existing = self.repository.import_readings(
      guild_id,
      [
        (entry.word, entry.reading, _is_priority_word(entry.word))
        for entry in parsed.entries
      ],
    )
    failures = list(parsed.failures)
    failures.extend(
      dict_transfer.ImportFailure(
        entry.line_no, entry.line, dict_transfer.REASON_ALREADY_EXISTS
      )
      for entry in parsed.entries
      if entry.word in existing
    )
    return dict_transfer.ImportResult(
      added=len(parsed.entries) - len(existing),
      failures=sorted(failures, key=lambda failure: failure.line_no),
    )

  def get_common_entries(self) -> list[tuple[str, str]]:
    """全サーバー共通辞書を (word, 表示値) の一覧で返す。

    読み方を持つエントリは読み方を、音声のみのエントリは
    「[音声] sound_id」を表示値にする。
    """
    readings = self.repository.get_reading_entries(COMMON_GUILD_ID)
    reading_words = {word for word, _ in readings}
    sounds = [
      (word, f'[音声] {sound_id}')
      for word, sound_id, _, _ in self.repository.get_sound_entries(
        COMMON_GUILD_ID
      )
      if word not in reading_words
    ]
    return list(readings) + sounds

  def add_sound(self, guild_id: int, word: str, sound_id: str,
               full_match: bool = True,
               trigger_user_id: Optional[str] = None) -> bool:
    """Returns True if overwriting an existing sound entry."""
    return self.repository.add_sound(
      guild_id,
      word,
      sound_id,
      is_priority=_is_priority_word(word),
      full_match=full_match,
      trigger_user_id=trigger_user_id,
    )

  def delete_sound(self, guild_id: int, word: str) -> Optional[str]:
    """Returns the removed sound_id, or None if not found."""
    return self.repository.delete_sound(guild_id, word)

  def get_sound_entries(self, guild_id: int) -> list[
    tuple[str, str, int, Optional[str]]
  ]:
    """Returns sound entries in added_at DESC order."""
    return self.repository.get_sound_entries(guild_id)

  def invalidate_sound(self, guild_id, sound_id: str):
    """指定 sound_id を参照する dict レコードを更新する。
    reading が NULL なら行削除、reading が存在すれば sound_id を NULL 化。"""
    self.repository.invalidate_sound(guild_id, sound_id)

  def delete_entry(self, guild_id: int, word: str):
    """reading と sound_id 両方を削除する（行ごと削除）。"""
    self.repository.delete_entry(guild_id, word)

  def preprocess_text(
      self,
      text: str,
      guild_id: int,
      guild,
      attachments,
      mentions=None,
      author_id: Optional[int] = None,
      user_readings=None,
      use_sounds: bool = True,
  ) -> PreprocessResult:
    dictionary = self.repository.get_preprocessing_snapshot(guild_id)
    return swap.preprocess_text(
      text,
      dictionary,
      self._emoji_ja,
      guild,
      attachments,
      mentions,
      author_id=author_id,
      user_readings=user_readings,
      use_sounds=use_sounds,
    )


IMPORT_REPORT_INLINE_LIMIT = 1800


def build_import_failure_report(guild, failures):
  """読み込まなかった行の一覧を、本文と添付ファイル（短いときはNone）にする。"""
  header = f'**{guild.name}** の辞書に読み込まなかった行（{len(failures)}件）'
  body = dict_transfer.format_failures(failures)
  if len(header) + len(body) <= IMPORT_REPORT_INLINE_LIMIT:
    return f'{header}\n```\n{body}```', None
  report = discord.File(
    io.BytesIO(body.encode('utf-8')),
    filename=f'import_failures_{guild.id}.txt',
  )
  return f'{header}\n一覧は添付のファイルにあります', report


async def send_import_failures(user, guild, failures) -> bool:
  """読み込まなかった行の一覧をDMで送る。送れなかったらFalseを返す。"""
  content, report = build_import_failure_report(guild, failures)
  kwargs = {'content': content}
  if report is not None:
    kwargs['file'] = report
  try:
    await user.send(**kwargs)
  except discord.errors.HTTPException:
    return False
  return True


class WordDict:
  def __init__(
      self,
      client,
      tree,
      dict_manager: DictManager,
      server_config,
      error_notifier=None,
  ):
    self.client = client
    self.tree = tree
    self.dict_manager = dict_manager
    self.server_config = server_config
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._register()

  def _register(self):
    dict_group = discord.app_commands.Group(
      name='dict',
      description='読み上げ辞書を管理します'
    )

    @dict_group.command(name='add', description='辞書に単語の読みを追加します')
    @discord.app_commands.describe(
      word='追加する単語',
      read='読み方（50文字以内）'
    )
    @discord.app_commands.checks.has_permissions()
    async def dict_add(ctx, word: str, read: str):
      try:
        await ctx.response.defer()
        try:
          overwrite = self.dict_manager.add(ctx.guild.id, word, read)
        except ValueError:
          await ctx.edit_original_response(
            embed=make_embed(
              '読みが長すぎるため追加できませんでした',
              f'`{read}` は50文字を超えるため追加できません。\n追加する読みは50文字以内にしてください',
              embed_type=EmbedType.ERROR,
            )
          )
          return
        title = '辞書への上書きが成功しました' if overwrite else '辞書への追加が成功しました'
        description = '辞書に単語の読みが上書きされました' if overwrite else '辞書に単語の読みが追加されました'
        embed = make_embed(title, description, embed_type=EmbedType.SUCCESS)
        embed.add_field(name='登録内容', value=f'`{word}` → `{read}`', inline=False)
        await ctx.edit_original_response(
          embed=embed
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f'HTTPException in dict_add: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "dict_add", self.error_notifier)

    @dict_group.command(name='del', description='辞書から単語の読みを削除します')
    @discord.app_commands.describe(
      word='削除する単語',
      both='Trueにすると音声辞書からも削除します'
    )
    @discord.app_commands.checks.has_permissions()
    async def dict_del(ctx, word: str, both: bool = False):
      try:
        await ctx.response.defer()
        read = self.dict_manager.delete(ctx.guild.id, word)
        if read is None:
          await ctx.edit_original_response(
            embed=make_embed(
              '辞書にその単語は見つかりませんでした',
              f'辞書に`{word}`はありません。確認して入れなおしてください。',
              embed_type=EmbedType.ERROR,
            )
          )
          return
        if both:
          self.dict_manager.delete_sound(ctx.guild.id, word)
        embed = make_embed(
          '辞書からの削除が成功しました',
          '辞書から単語と読みが削除されました',
          embed_type=EmbedType.SUCCESS,
        )
        embed.add_field(name='削除内容', value=f'`{word}` → `{read}`', inline=False)
        await ctx.edit_original_response(embed=embed)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f'HTTPException in dict_del: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "dict_del", self.error_notifier)

    # noinspection PyUnusedLocal
    @dict_del.autocomplete("word")
    async def dict_del_word_autocomplete(ctx, current: str):
      entries = self.dict_manager.get_entries(ctx.guild.id)
      all_words = [word for word, _ in entries]
      filtered = [
        discord.app_commands.Choice(name=word, value=word)
        for word in all_words
        if current in word
      ]
      return filtered[:25]

    @dict_group.command(name='view', description='辞書の内容を確認します')
    @discord.app_commands.describe(
      ephemeral='Trueにすると自分にだけ見える形で表示します',
      search='検索する文字列（部分一致）'
    )
    async def dict_view(ctx, search: Optional[str] = None, ephemeral: bool = False):
      try:
        await ctx.response.defer(ephemeral=ephemeral)
        entries = self.dict_manager.get_entries(ctx.guild.id)

        if not entries:
          embed = make_embed('辞書一覧', '辞書に登録された単語はありません')
          await ctx.edit_original_response(embed=embed)
          return

        if search:
          items = _filter_entries(dict(entries), search)
        else:
          items = entries

        if not items:
          await ctx.edit_original_response(
            embed=make_embed(
              '見つかりませんでした',
              f'「{search}」に一致する単語は辞書にありません',
              embed_type=EmbedType.ERROR,
            )
          )
          return

        paginator = DictViewPaginator(
          items,
          'dict',
          self.error_notifier,
          word_formatter=replace_word,
        )
        embed = paginator.build_embed()

        if paginator.total_pages <= 1:
          await ctx.edit_original_response(embed=embed)
        else:
          msg = await ctx.edit_original_response(embed=embed, view=paginator)
          paginator.message = msg

      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f'HTTPException in dict_view: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "dict_view", self.error_notifier)

    @dict_group.command(
      name='view-common',
      description='全サーバー共通の辞書の内容を確認します',
    )
    @discord.app_commands.describe(
      ephemeral='Trueにすると自分にだけ見える形で表示します',
      search='検索する文字列（部分一致）'
    )
    async def dict_view_common(
        ctx,
        search: Optional[str] = None,
        ephemeral: bool = False,
    ):
      try:
        await ctx.response.defer(ephemeral=ephemeral)
        entries = self.dict_manager.get_common_entries()

        if not entries:
          await ctx.edit_original_response(
            embed=make_embed(
              COMMON_DICT_TITLE,
              '共通辞書に登録された単語はありません',
            )
          )
          return

        items = _filter_entries(dict(entries), search) if search else entries
        if not items:
          await ctx.edit_original_response(
            embed=make_embed(
              '見つかりませんでした',
              f'「{search}」に一致する単語は共通辞書にありません',
              embed_type=EmbedType.ERROR,
            )
          )
          return

        paginator = DictViewPaginator(
          items,
          'dict',
          self.error_notifier,
          word_formatter=replace_word,
          title=COMMON_DICT_TITLE,
        )
        embed = paginator.build_embed()

        if paginator.total_pages <= 1:
          await ctx.edit_original_response(embed=embed)
        else:
          msg = await ctx.edit_original_response(embed=embed, view=paginator)
          paginator.message = msg

      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f'HTTPException in dict_view_common: {e}')
      except Exception as e:
        await handle_internal_error(
          ctx, e, "dict_view_common", self.error_notifier
        )

    @dict_group.command(
      name='export',
      description='（ベータ版）このサーバーの辞書を.dictファイル（CSV形式）で書き出します（サーバー管理者のみ）',
    )
    @discord.app_commands.describe(
      ephemeral='Trueにすると自分にだけ見える形で表示します',
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def dict_export(ctx, ephemeral: bool = False):
      try:
        await ctx.response.defer(ephemeral=ephemeral)
        text, count, skipped = self.dict_manager.export_text(ctx.guild.id)
        if count == 0 and skipped == 0:
          await ctx.edit_original_response(
            embed=make_embed('辞書の書き出し', '辞書に登録された単語はありません')
          )
          return
        description = f'{count}件の単語を書き出しました'
        if skipped:
          description += f'\n読みにカンマ（,）が入っている{skipped}件は書き出していません'
        await ctx.edit_original_response(
          embed=make_embed(
            '辞書を書き出しました',
            description,
            embed_type=EmbedType.SUCCESS,
          ),
          attachments=[
            discord.File(
              io.BytesIO(text.encode('utf-8')),
              filename=dict_transfer.export_filename(ctx.guild.id),
            )
          ],
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f'HTTPException in dict_export: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "dict_export", self.error_notifier)

    @dict_group.command(
      name='import',
      description='（ベータ版）.dictファイル（CSV形式）から辞書を読み込みます（サーバー管理者のみ）',
    )
    @discord.app_commands.describe(
      file='「単語, 読み」を1行に1件ずつ書いたファイル（文字コードは自動で判別）',
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def dict_import(ctx, file: discord.Attachment):
      try:
        await ctx.response.defer()
        try:
          dict_transfer.check_import_size(file.size)
          result = self.dict_manager.import_data(ctx.guild.id, await file.read())
        except dict_transfer.ImportRejected as e:
          await ctx.edit_original_response(
            embed=make_embed(
              '辞書を読み込めませんでした',
              str(e),
              embed_type=EmbedType.ERROR,
            )
          )
          return
        failed = len(result.failures)
        embed = make_embed(
          '辞書を読み込みました',
          f'{result.added}件の単語を追加しました',
          embed_type=EmbedType.SUCCESS,
        )
        if failed:
          delivered = await send_import_failures(
            ctx.user, ctx.guild, result.failures
          )
          if delivered:
            note = '一覧をDMで送りました'
          else:
            note = 'DMを送れなかったので、一覧をこのあと自分にだけ見える形で送ります'
          embed.add_field(
            name='読み込まなかった行',
            value=f'{failed}件（{note}）',
            inline=False,
          )
        await ctx.edit_original_response(embed=embed)
        if failed and not delivered:
          content, report = build_import_failure_report(
            ctx.guild, result.failures
          )
          kwargs = {'content': content, 'ephemeral': True}
          if report is not None:
            kwargs['file'] = report
          await ctx.followup.send(**kwargs)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f'HTTPException in dict_import: {e}')
      except Exception as e:
        await handle_internal_error(ctx, e, "dict_import", self.error_notifier)

    @dict_group.error
    async def dict_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=make_embed(
            '権限エラー',
            'サーバー管理権限が必要です',
            embed_type=EmbedType.ERROR,
          ),
          ephemeral=True
        )

    self.tree.add_command(dict_group)
