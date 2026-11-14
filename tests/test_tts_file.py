"""`!t`・`/tsumugi-tts`（読み上げて、その音声ファイルを送る）のテスト。"""

import asyncio
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import swap
from models.audio_item import TTSItem
from models.dictionary_snapshot import DictionarySnapshot, SoundEntry
from services.message_service import MessageService, parse_tts_file_command
from services.speech_service import SpeechService, build_tts_file_name
from services.voice_service import VoiceService
from tests.support import (
  DUMMY_BOT_USER_ID,
  DUMMY_GUILD_ID,
  DUMMY_SOUND_ID,
  DUMMY_USER_ID,
  DUMMY_VOICE_CHANNEL_ID,
  RecordingNotifier,
  TempDirTestCase,
)


CREATED_AT = datetime(2026, 11, 14, 9, 11, 27, tzinfo=timezone.utc)


class ParseCommandTest(unittest.TestCase):
  def test_prefixes(self):
    self.assertEqual(parse_tts_file_command("!t おはよう"), "おはよう")
    self.assertEqual(parse_tts_file_command("!tts おはよう"), "おはよう")
    self.assertEqual(parse_tts_file_command("!t\nおはよう\nみんな"), "おはよう\nみんな")

  def test_not_command(self):
    for content in ("!t", "!tts", "!t   ", "!test おはよう", "!ttsおはよう", "おはよう !t"):
      with self.subTest(content=content):
        self.assertIsNone(parse_tts_file_command(content))


class FileNameTest(unittest.TestCase):
  def test_name_uses_japan_time_and_text(self):
    self.assertEqual(
      build_tts_file_name("おはよう", CREATED_AT), "20261114-181127-おはよう.wav"
    )

  def test_unsafe_characters_are_replaced(self):
    self.assertEqual(
      build_tts_file_name('a/b:c*"d"\nえ  お', CREATED_AT),
      "20261114-181127-a_b_c_d_え_お.wav",
    )

  def test_long_text_is_shortened(self):
    name = build_tts_file_name("あ" * 100, CREATED_AT)
    self.assertEqual(name, "20261114-181127-" + "あ" * 40 + ".wav")

  def test_text_without_usable_characters(self):
    self.assertEqual(build_tts_file_name("///", CREATED_AT), "20261114-181127.wav")


class SoundDictionaryIsSkippedTest(unittest.TestCase):
  def test_use_sounds_false_ignores_sound_dictionary(self):
    guild = SimpleNamespace(get_member=lambda _: None, get_channel=lambda _: None, get_role=lambda _: None)
    dictionary = DictionarySnapshot(sounds=(SoundEntry("やあ", DUMMY_SOUND_ID),))
    self.assertEqual(
      swap.preprocess_text("やあ", dictionary, {}, guild, []).sound_id, DUMMY_SOUND_ID
    )
    result = swap.preprocess_text("やあ", dictionary, {}, guild, [], use_sounds=False)
    self.assertIsNone(result.sound_id)
    self.assertEqual(result.text, "やあ")


class FakeServerConfig:
  def __init__(self, max_char=100):
    self.max_char = max_char

  def get(self, guild_id, key):
    return {"Speaker": 8, "MaxChar": self.max_char}[key]

  def volume_to_vvtts(self, guild_id):
    return 1.0

  def speed_to_vvtts(self, guild_id, spaced_out=False):
    return 1.0


class SpeakAndExportTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.voice_service = VoiceService(client=None, error_notifier=RecordingNotifier())
    self.voice_service.enqueue = mock.AsyncMock()
    self.generated_texts = []
    self.release = asyncio.Event()
    self.release.set()
    owner = self

    class FakeVoicevox:
      async def generate(self, text, guild_id, message_id, *args, **kwargs):
        owner.generated_texts.append(text)
        await owner.release.wait()
        path = owner.tmp_path(f"{guild_id}-{message_id}.wav")
        with open(path, "wb") as file:
          file.write(b"RIFF-dummy")
        return path

    self.config = FakeServerConfig()
    self.speech = SpeechService(FakeVoicevox(), self.config, None, self.voice_service)
    self.guild = SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=None)

  async def export(self, text="おはよう", message_id=1):
    return await self.speech.speak_and_export(
      self.guild, text, message_id, author_id=DUMMY_USER_ID,
      created_at=CREATED_AT, message_id=message_id,
    )

  async def test_file_is_returned_and_audio_is_queued(self):
    tts_file = await self.export()
    self.assertEqual(tts_file.name, "20261114-181127-おはよう.wav")
    self.assertEqual(tts_file.data, b"RIFF-dummy")
    self.voice_service.enqueue.assert_awaited_once()
    item = self.voice_service.enqueue.await_args.args[1]
    self.assertIsInstance(item, TTSItem)
    self.assertEqual(item.message_id, 1)

  async def test_max_char_is_applied_like_normal_speech(self):
    self.config.max_char = 30
    await self.export("あ" * 50)
    self.assertEqual(self.generated_texts, ["あ" * 30 + ",以下省略"])

  async def test_cleared_during_generation_returns_file_without_playing(self):
    self.release.clear()
    task = asyncio.create_task(self.export())
    await asyncio.sleep(0)
    self.voice_service.begin_clear(self.guild, instant=True)
    self.release.set()
    tts_file = await task
    self.assertEqual(tts_file.data, b"RIFF-dummy")
    self.voice_service.enqueue.assert_not_called()
    self.assertFalse(os.path.exists(self.tmp_path(f"{DUMMY_GUILD_ID}-1.wav")))

  async def test_deleted_message_is_neither_played_nor_sent(self):
    self.release.clear()
    task = asyncio.create_task(self.export())
    await asyncio.sleep(0)
    self.voice_service.cancel_message(self.guild, 1)
    self.release.set()
    self.assertIsNone(await task)
    self.voice_service.enqueue.assert_not_called()

  async def test_generation_failure_returns_none(self):
    self.speech.vvtts = SimpleNamespace(generate=mock.AsyncMock(return_value=None))
    self.assertIsNone(await self.export())
    self.voice_service.enqueue.assert_not_called()


class MessageCommandTest(unittest.IsolatedAsyncioTestCase):
  def make_service(self):
    self.notifier = RecordingNotifier()
    self.voice_service = VoiceService(client=None, error_notifier=self.notifier)
    self.speech_service = mock.MagicMock()
    self.speech_service.add_message = mock.AsyncMock()
    self.speech_service.speak_and_export = mock.AsyncMock()
    client = SimpleNamespace(user=SimpleNamespace(id=DUMMY_BOT_USER_ID))
    server_config = SimpleNamespace(get=lambda guild_id, key: None)
    return MessageService(
      client, server_config, self.speech_service, self.voice_service,
      mock.MagicMock(), self.notifier,
    )

  def make_message(self, content):
    voice_client = SimpleNamespace(channel=SimpleNamespace(id=DUMMY_VOICE_CHANNEL_ID))
    return SimpleNamespace(
      id=1,
      guild=SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=voice_client),
      channel=SimpleNamespace(id=DUMMY_VOICE_CHANNEL_ID, send=mock.AsyncMock()),
      author=SimpleNamespace(id=DUMMY_USER_ID, bot=False),
      content=content,
      flags=SimpleNamespace(silent=False),
      mentions=[],
      created_at=CREATED_AT,
      reply=mock.AsyncMock(),
    )

  async def test_prefix_starts_tts_file_task(self):
    service = self.make_service()
    await service.handle(self.make_message("!t おはよう"))
    self.assertEqual(self.notifier.tasks, ["message tts file"])
    self.speech_service.add_message.assert_not_called()

  async def test_file_is_sent_as_reply(self):
    service = self.make_service()
    message = self.make_message("!t おはよう")
    self.speech_service.speak_and_export.return_value = SimpleNamespace(
      to_discord_file=lambda: "file"
    )
    await service.speak_and_send_file(message, "おはよう")
    self.speech_service.speak_and_export.assert_awaited_once()
    self.assertEqual(self.speech_service.speak_and_export.await_args.args[1], "おはよう")
    message.reply.assert_awaited_once_with(file="file", mention_author=False)

  async def test_nothing_is_sent_without_file(self):
    service = self.make_service()
    message = self.make_message("!t おはよう")
    self.speech_service.speak_and_export.return_value = None
    await service.speak_and_send_file(message, "おはよう")
    message.reply.assert_not_called()
    message.channel.send.assert_not_called()


if __name__ == "__main__":
  unittest.main()
