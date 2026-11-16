"""読む文字のないメッセージで音声を作らないことのテスト。

embedだけのメッセージや、前処理で文字が残らないメッセージを、空の文章のまま
VOICEVOXへ渡さない（speech_service.py・voicevox_client.py）。
"""

import unittest
from models.streaming_audio import StreamingAudio
from types import SimpleNamespace
from unittest import mock

import swap
from models.audio_item import SoundboardItem, TTSItem
from models.dictionary_snapshot import DictionarySnapshot, SoundEntry
from services.speech_service import SpeechService, has_speakable_text
from services.voice_service import VoiceService
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_SOUND_ID,
  DUMMY_USER_ID,
  RecordingNotifier,
  TempDirTestCase,
)


class FakeServerConfig:
  def get(self, guild_id, key):
    return {"Speaker": 8, "MaxChar": 100}[key]

  def volume_to_vvtts(self, guild_id):
    return 1.0

  def speed_to_vvtts(self, guild_id, spaced_out=False):
    return 1.0


class FakeDictManager:
  """本物の前処理（swap.preprocess_text）を、決めた辞書で動かす。"""

  def __init__(self, dictionary):
    self.dictionary = dictionary

  def preprocess_text(self, text, guild_id, guild, attachments, mentions, **kwargs):
    return swap.preprocess_text(
      text, self.dictionary, {}, guild, attachments, mentions, **kwargs
    )


class HasSpeakableTextTest(unittest.TestCase):
  def test_text_without_letters_is_not_speakable(self):
    for text in ("", " ", "　\n", ",", ",,", "、。", "..."):
      with self.subTest(text=text):
        self.assertFalse(has_speakable_text(text))

  def test_text_with_letters_is_speakable(self):
    for text in ("あ", ",画像ファイル", "w", "1", "♪"):
      with self.subTest(text=text):
        self.assertTrue(has_speakable_text(text))


class EmptySpeechTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.voice_service = VoiceService(client=None, error_notifier=RecordingNotifier())
    self.voice_service.enqueue = mock.AsyncMock()
    self.generated_texts = []
    owner = self

    class FakeVoicevox:
      async def generate(self, text, guild_id, message_id, *args, **kwargs):
        owner.generated_texts.append(text)
        path = owner.tmp_path(f"{guild_id}-{message_id}.wav")
        with open(path, "wb") as file:
          file.write(b"RIFF-dummy")
        return StreamingAudio.completed(path)

    dictionary = DictionarySnapshot(sounds=(SoundEntry("やあ", DUMMY_SOUND_ID),))
    self.speech = SpeechService(
      FakeVoicevox(), FakeServerConfig(), FakeDictManager(dictionary), self.voice_service
    )
    self.guild = SimpleNamespace(
      id=DUMMY_GUILD_ID,
      voice_client=None,
      get_member=lambda _: None,
      get_channel=lambda _: None,
      get_role=lambda _: None,
    )

  def message(self, content="", attachments=(), embeds=()):
    return SimpleNamespace(
      id=1, guild=self.guild, content=content, stickers=[], reference=None,
      attachments=list(attachments), embeds=list(embeds), mentions=[],
      author=SimpleNamespace(id=DUMMY_USER_ID),
    )

  async def test_embed_only_message_is_not_generated(self):
    embed = SimpleNamespace(title="お知らせ", description="本文")
    await self.speech.add_message(self.message(embeds=[embed]))
    self.assertEqual(self.generated_texts, [])
    self.voice_service.enqueue.assert_not_called()

  async def test_message_empty_after_preprocess_is_not_generated(self):
    for content in ("<:dummy:111111111111111111>", "   ", "\n\n"):
      with self.subTest(content=content):
        await self.speech.add_message(self.message(content))
    self.assertEqual(self.generated_texts, [])
    self.voice_service.enqueue.assert_not_called()

  async def test_attachment_only_message_is_still_read(self):
    attachment = SimpleNamespace(filename="dummy.png")
    await self.speech.add_message(self.message(attachments=[attachment]))
    self.assertEqual(self.generated_texts, [",画像ファイル"])
    item = self.voice_service.enqueue.await_args.args[1]
    self.assertIsInstance(item, TTSItem)

  async def test_sound_dictionary_is_still_played(self):
    await self.speech.add_message(self.message("やあ"))
    self.assertEqual(self.generated_texts, [])
    item = self.voice_service.enqueue.await_args.args[1]
    self.assertIsInstance(item, SoundboardItem)
    self.assertEqual(item.sound_id, DUMMY_SOUND_ID)

  async def test_tts_file_is_not_made_from_empty_text(self):
    tts_file = await self.speech.speak_and_export(
      self.guild, "<:dummy:111111111111111111>", 1,
      author_id=DUMMY_USER_ID, message_id=1,
    )
    self.assertIsNone(tts_file)
    self.assertEqual(self.generated_texts, [])
    self.voice_service.enqueue.assert_not_called()


if __name__ == "__main__":
  unittest.main()
