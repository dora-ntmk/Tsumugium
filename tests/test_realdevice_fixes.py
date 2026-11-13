"""実機確認（Discord）で見つかった不具合の修正のテスト。

- 文中の、前後の語にくっついた1文字ずつの区切りを詰める（swap.py）
- 音声を作っている途中にクリアされたメッセージを読まない（speech_service.py）
- /setting text-target で一時的な読み上げ先を消す、AutoJoin の接続先を決める（setting.py）
- 接続し直したあとに「話していない」を送り直す（voice_service.py）
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import swap
from services.speech_service import SpeechService
from services.voice_service import OPUS_SILENCE, VoiceService
from setting import Setting
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_TEXT_CHANNEL_ID,
  DUMMY_VOICE_CHANNEL_ID,
  RecordingNotifier,
  TempDirTestCase,
)


def normalize(text):
  return swap._normalize_spaced_text(text, {})


class WordAttachedSpacingTest(unittest.TestCase):
  def test_spaced_letters_attached_to_words_are_joined(self):
    self.assertEqual(normalize("今日はとてもよ　く　な　い天気です"), ("今日はとてもよくない天気です", False))
    self.assertEqual(normalize("今日はとてもよ く な い天気です"), ("今日はとてもよくない天気です", False))

  def test_one_side_attached(self):
    self.assertEqual(normalize("とてもよ く な"), ("とてもよくな", False))
    self.assertEqual(normalize("今日は よ く ない"), ("今日はよくない", False))

  def test_existing_rules_are_kept(self):
    self.assertEqual(normalize("今日は あ い う え お です"), ("今日はあいうえおです", False))
    self.assertEqual(normalize("あ い う"), ("あいう", True))

  def test_too_few_letters_are_not_joined(self):
    # 区切られた1文字が1つだけなら、ふつうの語の区切りとみなす
    self.assertEqual(normalize("なんで も いい"), ("なんで も いい", False))
    self.assertEqual(normalize("とてもよ　く"), ("とてもよ　く", False))

  def test_english_is_not_joined(self):
    self.assertEqual(normalize("This is a pen"), ("This is a pen", False))
    self.assertEqual(normalize("これは a b ペン"), ("これは a b ペン", False))

  def test_through_preprocess(self):
    from models.dictionary_snapshot import DictionarySnapshot

    guild = SimpleNamespace(get_member=lambda _: None, get_channel=lambda _: None, get_role=lambda _: None)
    result = swap.preprocess_text("今日はとてもよ　く　な　い天気です", DictionarySnapshot(), {}, guild, [])
    self.assertEqual(result.text, "今日はとてもよくない天気です")


class FakeServerConfig:
  def get(self, guild_id, key):
    return {"Speaker": 8, "MaxChar": 100}[key]

  def volume_to_vvtts(self, guild_id):
    return 1.0

  def speed_to_vvtts(self, guild_id, spaced_out=False):
    return 1.0


class ClearWhileGeneratingTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.voice_service = VoiceService(client=None, error_notifier=RecordingNotifier())
    self.voice_service.enqueue = mock.AsyncMock()
    self.generation_started = asyncio.Event()
    self.release_generation = asyncio.Event()
    self.wav = self.tmp_path("speech.wav")
    owner = self

    class SlowVoicevox:
      async def generate(self, *args, **kwargs):
        with open(owner.wav, "wb") as file:
          file.write(b"RIFF")
        owner.generation_started.set()
        await owner.release_generation.wait()
        return owner.wav

    self.speech = SpeechService(SlowVoicevox(), FakeServerConfig(), None, self.voice_service)
    self.guild = SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=None)

  def message(self):
    return SimpleNamespace(
      id=1, guild=self.guild, content="こんにちは", stickers=[], reference=None,
      attachments=[], mentions=[], author=SimpleNamespace(id=1),
    )

  async def test_message_cleared_during_generation_is_not_queued(self):
    task = asyncio.create_task(self.speech.add_message(self.message()))
    await self.generation_started.wait()
    self.voice_service.begin_clear(self.guild, instant=True)
    self.release_generation.set()
    await task
    self.voice_service.enqueue.assert_not_called()
    self.assertFalse(os.path.exists(self.wav))

  async def test_message_without_clear_is_queued(self):
    task = asyncio.create_task(self.speech.add_message(self.message()))
    await self.generation_started.wait()
    self.release_generation.set()
    await task
    self.voice_service.enqueue.assert_called_once()


class FakeSettingConfig:
  def __init__(self, voice_target=None):
    self.values = {"VoiceTarget": voice_target}

  def get(self, guild_id, key):
    return self.values[key]


def make_setting(voice_service=None, voice_target=None):
  with mock.patch.object(Setting, "_register", lambda self: None):
    return Setting(
      client=None, tree=None, server_config=FakeSettingConfig(voice_target),
      error_notifier=RecordingNotifier(), speaker_service=mock.MagicMock(),
      voice_service=voice_service,
    )


def make_channel(connect=True, speak=True):
  perms = SimpleNamespace(connect=connect, speak=speak)
  return SimpleNamespace(id=DUMMY_VOICE_CHANNEL_ID, mention="#vc", permissions_for=lambda _: perms)


class TextTargetTest(unittest.TestCase):
  def test_setting_text_target_clears_temporary_target(self):
    voice_service = VoiceService(client=None, error_notifier=RecordingNotifier())
    session = voice_service.get_session(DUMMY_GUILD_ID)
    session.temporary_text_channel_id = DUMMY_TEXT_CHANNEL_ID
    session.pending_text_channel_id = DUMMY_TEXT_CHANNEL_ID
    make_setting(voice_service).clear_temporary_text_target(DUMMY_GUILD_ID)
    self.assertIsNone(session.temporary_text_channel_id)
    self.assertIsNone(session.pending_text_channel_id)


class AutoJoinTargetTest(unittest.TestCase):
  def setUp(self):
    self.guild = SimpleNamespace(id=DUMMY_GUILD_ID, me=object())

  def test_executor_voice_channel_is_recorded(self):
    channel = make_channel()
    user = SimpleNamespace(voice=SimpleNamespace(channel=channel))
    self.assertEqual(make_setting().resolve_auto_join_target(self.guild, user), (channel, None))

  def test_rejected_when_not_in_voice_channel(self):
    user = SimpleNamespace(voice=None)
    channel, problem = make_setting().resolve_auto_join_target(self.guild, user)
    self.assertIsNone(channel)
    self.assertIn("voice-target", problem)

  def test_rejected_without_permission(self):
    user = SimpleNamespace(voice=SimpleNamespace(channel=make_channel(speak=False)))
    channel, problem = make_setting().resolve_auto_join_target(self.guild, user)
    self.assertIsNone(channel)
    self.assertIn("権限", problem)

  def test_existing_voice_target_is_kept(self):
    user = SimpleNamespace(voice=None)
    setting = make_setting(voice_target=DUMMY_VOICE_CHANNEL_ID)
    self.assertEqual(setting.resolve_auto_join_target(self.guild, user), (None, None))


class SpeakingResetTest(unittest.IsolatedAsyncioTestCase):
  async def test_silence_and_not_speaking_are_sent(self):
    service = VoiceService(client=None, error_notifier=RecordingNotifier())
    service.SPEAKING_RESET_DELAY = 0
    sent = []
    voice_client = SimpleNamespace(
      is_connected=lambda: True,
      is_playing=lambda: False,
      send_audio_packet=lambda data, encode: sent.append(data),
      ws=SimpleNamespace(speak=mock.AsyncMock()),
    )
    await service._reset_speaking(SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=voice_client))
    self.assertEqual(sent, [OPUS_SILENCE] * 5)
    voice_client.ws.speak.assert_awaited_once()

  async def test_nothing_is_sent_while_playing(self):
    service = VoiceService(client=None, error_notifier=RecordingNotifier())
    service.SPEAKING_RESET_DELAY = 0
    voice_client = SimpleNamespace(
      is_connected=lambda: True,
      is_playing=lambda: True,
      send_audio_packet=mock.MagicMock(),
      ws=SimpleNamespace(speak=mock.AsyncMock()),
    )
    await service._reset_speaking(SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=voice_client))
    voice_client.send_audio_packet.assert_not_called()
    voice_client.ws.speak.assert_not_awaited()


if __name__ == "__main__":
  unittest.main()
