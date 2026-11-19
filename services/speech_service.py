"""メッセージの前処理、音声種別の選択、TTS生成を担当する。"""

import asyncio
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord

from models.audio_item import SoundboardItem, TTSItem
from models.generation_record import SKIP_CLEARED, SKIP_DELETED, SKIP_EMPTY


# 音声ファイルの名前に付ける時刻は日本時間にする（サーバーの時間帯に左右されない）
JST = timezone(timedelta(hours=9), "JST")
TTS_FILE_NAME_TEXT_LIMIT = 40
# ファイル名に使えない文字・使うと紛らわしい文字と、空白・制御文字
_UNSAFE_FILE_NAME_RE = re.compile(r'[\\/:*?"<>|\s\x00-\x1f\x7f]+')


def has_speakable_text(text: str) -> bool:
  """読み上げる文字があるかを返す。

  空白・区切り記号（前処理で入る`,`など）・制御文字しかない文章は、
  VOICEVOXへ渡しても声にならないので読まない。
  """
  return any(
    unicodedata.category(char)[0] not in ("Z", "P", "C")
    for char in text
  )


def build_tts_file_name(text: str, created_at: datetime | None = None) -> str:
  """`20261114-181127-おはよう.wav`の形の名前を作る。"""
  if created_at is None:
    created_at = datetime.now(timezone.utc)
  if created_at.tzinfo is None:
    created_at = created_at.replace(tzinfo=timezone.utc)
  stamp = created_at.astimezone(JST).strftime("%Y%m%d-%H%M%S")
  label = _UNSAFE_FILE_NAME_RE.sub("_", text).strip("_.")
  label = label[:TTS_FILE_NAME_TEXT_LIMIT].rstrip("_.")
  if not label:
    return f"{stamp}.wav"
  return f"{stamp}-{label}.wav"


@dataclass(frozen=True, slots=True)
class TTSFile:
  """`!t`・`/tsumugi-tts`で作った音声ファイル。"""

  name: str
  data: bytes

  def to_discord_file(self):
    return discord.File(io.BytesIO(self.data), filename=self.name)


class SpeechService:
  def __init__(
      self,
      vvtts,
      server_config,
      dict_manager,
      voice_service,
      user_reading_service=None,
      generation_log=None,
  ):
    self.vvtts = vvtts
    self.server_config = server_config
    self.dict_manager = dict_manager
    self.voice_service = voice_service
    self.user_reading_service = user_reading_service
    # 生成の記録（GenerationLogService）。スキップだけをここで記録する
    self.generation_log = generation_log

  def _record_skip(self, reason, chars: int = 0) -> None:
    if self.generation_log is not None:
      self.generation_log.record_skip(reason, chars)

  def _preprocess(
      self,
      text,
      guild,
      attachments,
      mentions,
      author_id,
      *,
      use_sounds: bool = True,
  ):
    """辞書などの前処理をし、`(文章, 置換範囲, 空白区切り, sound_id)`を返す。"""
    if self.dict_manager is None:
      return text, [], False, None
    user_readings = {}
    if self.user_reading_service is not None:
      for user in mentions:
        user_readings[str(user.id)] = (
          self.user_reading_service.get_reading(user)
        )
    result = self.dict_manager.preprocess_text(
      text,
      guild.id,
      guild,
      attachments,
      mentions,
      author_id=author_id,
      user_readings=user_readings,
      use_sounds=use_sounds,
    )
    return result.text, result.replaced_ranges, result.spaced_out, result.sound_id

  def _limit_length(self, guild_id, text, replaced_ranges) -> str:
    max_char = self.server_config.get(guild_id, "MaxChar")
    if 0 < max_char < len(text):
      cut = max_char
      for start, end in replaced_ranges:
        if start < cut < end:
          cut = end
          break
      text = text[:cut] + ",以下省略"
    return text

  async def _synthesize(self, guild_id, request_id, text, spaced_out):
    return await self.generate(
      text,
      guild_id,
      request_id,
      self.server_config.get(guild_id, "Speaker"),
      speed=self.server_config.speed_to_vvtts(guild_id, spaced_out=spaced_out),
      volume=self.server_config.volume_to_vvtts(guild_id),
    )

  async def add_message(
      self,
      message,
      *,
      sounddict_only: bool = False,
      received_at: float | None = None,
  ) -> None:
    # /synthesis-log の first を数え始める時刻（loop.time()）。ふつうは on_message に
    # 入った時刻を受け取る。渡されなかったときはここから数える
    if received_at is None:
      received_at = asyncio.get_running_loop().time()
    guild_id = message.guild.id
    session = self.voice_service.get_session(guild_id)
    if session.clearing:
      return
    # 音声を作っている間にクリアされたら、作り終えても読まない
    clear_generation = session.clear_generation
    reference = getattr(message, "reference", None)
    if reference is not None and reference.type is discord.MessageReferenceType.forward:
      text = "転送済みメッセージ"
    else:
      text = message.content
    if message.stickers:
      for i in range(len(message.stickers)):
        text += f" {message.stickers[i].name}"
    text, replaced_ranges, spaced_out, sound_id = self._preprocess(
      text,
      message.guild,
      message.attachments,
      message.mentions,
      message.author.id,
    )
    if sound_id is not None:
      await self.voice_service.enqueue(message.guild, SoundboardItem(sound_id))
      return
    if sounddict_only:
      return
    # embedだけのメッセージなど、読む文字がなければ音声を作らない
    if not has_speakable_text(text):
      self._record_skip(SKIP_EMPTY)
      return
    text = self._limit_length(guild_id, text, replaced_ranges)
    # ストリーミングできるときは、最初の音が届いた時点で戻る。残りは裏で届く
    audio = await self._synthesize(guild_id, message.id, text, spaced_out)
    if audio is None:
      return
    if session.clear_generation != clear_generation:
      self._record_skip(SKIP_CLEARED, len(text))
      await self.voice_service.safe_remove(audio.path)
      return
    if self.voice_service.consume_cancelled(guild_id, message.id):
      self._record_skip(SKIP_DELETED, len(text))
      await self.voice_service.safe_remove(audio.path)
      return
    audio.received_at = received_at
    await self.voice_service.enqueue(
      message.guild,
      TTSItem(audio.path, message_id=message.id, audio=audio),
    )

  async def speak_and_export(
      self,
      guild,
      text: str,
      request_id: int,
      *,
      author_id=None,
      mentions=(),
      attachments=(),
      created_at: datetime | None = None,
      message_id: int | None = None,
      received_at: float | None = None,
  ) -> TTSFile | None:
    """文章を読み上げ、同じ音声をファイルとして返す（`!t`・`/tsumugi-tts`）。

    ふだんの読み上げと同じ設定・前処理を通す。ただし音声辞書には反応させない
    （Soundboardはファイルにできないため）。音声を作れなかったときと、
    元のメッセージが消されたときはNoneを返す。クリアされたときは
    読み上げないが、ファイルは返す。前処理のあとに読む文字が残らなかったときも
    音声を作らずにNoneを返す。
    """
    if received_at is None:
      received_at = asyncio.get_running_loop().time()
    session = self.voice_service.get_session(guild.id)
    clear_generation = session.clear_generation
    original_text = text
    text, replaced_ranges, spaced_out, _ = self._preprocess(
      text,
      guild,
      attachments,
      mentions,
      author_id,
      use_sounds=False,
    )
    if not has_speakable_text(text):
      self._record_skip(SKIP_EMPTY)
      return None
    text = self._limit_length(guild.id, text, replaced_ranges)
    audio = await self._synthesize(guild.id, request_id, text, spaced_out)
    if audio is None:
      return None
    path = audio.path
    # ファイルにするので、最後まで届くのを待つ。途中で切れたら送らない
    if not await audio.wait():
      await self.voice_service.safe_remove(path)
      return None
    # 再生が終わるとWAVは消えるので、キューへ入れる前に中身を読んでおく
    try:
      with open(path, "rb") as file:
        data = file.read()
    except OSError:
      await self.voice_service.safe_remove(path)
      raise
    if self.voice_service.consume_cancelled(guild.id, message_id):
      self._record_skip(SKIP_DELETED, len(text))
      await self.voice_service.safe_remove(path)
      return None
    tts_file = TTSFile(build_tts_file_name(original_text, created_at), data)
    if session.clearing or session.clear_generation != clear_generation:
      await self.voice_service.safe_remove(path)
      return tts_file
    audio.received_at = received_at
    await self.voice_service.enqueue(
      guild, TTSItem(path, message_id=message_id, audio=audio)
    )
    return tts_file

  async def generate(self, message, guild_id, message_id, speaker, speed=1.0, volume=1.0):
    """音声を作る。書きかけでも返る`StreamingAudio`か、作れなかったときはNone。"""
    return await self.vvtts.generate(
      message,
      guild_id,
      message_id,
      speaker,
      speed=speed,
      volume=volume,
    )
