"""音声キュー（services/voice_service.py）のテスト。

AGENTS.md「音声再生アーキテクチャ」のキューの型と /clear の動きを確かめる。
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from models.audio_item import SoundboardItem, TTSItem
from services.voice_service import VoiceService
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_SOUND_ID,
  RecordingNotifier,
  TempDirTestCase,
)


class FakeVoiceClient:
  def __init__(self, playing=False):
    self.playing = playing
    self.stopped = False

  def is_playing(self):
    return self.playing

  def stop(self):
    self.stopped = True
    self.playing = False


class VoiceServiceTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    super().setUp()
    self.notifier = RecordingNotifier()
    self.service = VoiceService(client=None, error_notifier=self.notifier)
    self.voice_client = FakeVoiceClient()
    self.guild = SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=self.voice_client)
    self.session = self.service.get_session(DUMMY_GUILD_ID)

  def make_wav(self, name):
    path = self.tmp_path("tmp", name)
    with open(path, "wb") as file:
      file.write(b"dummy")
    return path

  def test_session_is_created_once_per_guild(self):
    self.assertIs(self.service.get_session(DUMMY_GUILD_ID), self.session)

  async def test_enqueue_starts_play_loop(self):
    await self.service.enqueue(self.guild, SoundboardItem(DUMMY_SOUND_ID))
    self.assertEqual(self.session.queue.qsize(), 1)
    self.assertEqual(self.notifier.tasks, [f"play loop guild_id={DUMMY_GUILD_ID}"])

  def test_begin_clear_collects_only_tts_files(self):
    first = self.make_wav("first.wav")
    second = self.make_wav("second.wav")
    for item in (TTSItem(first), SoundboardItem(DUMMY_SOUND_ID), TTSItem(second)):
      self.session.queue.put_nowait(item)

    cleared, pending = self.service.begin_clear(self.guild, instant=False)

    self.assertEqual(cleared, 3)
    self.assertEqual(pending, [first, second])
    self.assertTrue(self.session.queue.empty())
    self.assertTrue(self.session.clearing)

  async def test_clear_removes_queued_tts_files(self):
    first = self.make_wav("first.wav")
    second = self.make_wav("second.wav")
    for item in (TTSItem(first), SoundboardItem(DUMMY_SOUND_ID), TTSItem(second)):
      self.session.queue.put_nowait(item)

    with mock.patch("services.voice_service.asyncio.sleep", mock.AsyncMock()):
      cleared = await self.service.clear(self.guild, instant=False)

    self.assertEqual(cleared, 3)
    self.assertFalse(os.path.exists(first))
    self.assertFalse(os.path.exists(second))
    self.assertFalse(self.session.clearing)

  def test_instant_clear_stops_current_audio(self):
    self.voice_client.playing = True
    self.session.current_tts_path = "current.wav"
    self.service.begin_clear(self.guild, instant=True)
    self.assertTrue(self.voice_client.stopped)
    self.assertIn("current.wav", self.session.expected_stop_paths)

  def test_skip_current(self):
    self.assertFalse(self.service.skip_current(self.guild))
    self.voice_client.playing = True
    self.assertTrue(self.service.skip_current(self.guild))
    self.assertTrue(self.voice_client.stopped)

  async def test_discard_queue_counts_items_and_removes_tts_files(self):
    path = self.make_wav("queued.wav")
    self.session.queue.put_nowait(TTSItem(path))
    self.session.queue.put_nowait(SoundboardItem(DUMMY_SOUND_ID))
    self.assertEqual(await self.service.discard_queue(DUMMY_GUILD_ID), 2)
    self.assertFalse(os.path.exists(path))

  def test_cancelled_message_is_consumed_once(self):
    self.service.cancel_message(self.guild, 10)
    self.assertTrue(self.service.consume_cancelled(DUMMY_GUILD_ID, 10))
    self.assertFalse(self.service.consume_cancelled(DUMMY_GUILD_ID, 10))
    self.assertFalse(self.service.consume_cancelled(DUMMY_GUILD_ID, None))

  def test_cancelled_messages_keep_only_latest(self):
    limit = VoiceService.CANCELLED_MESSAGE_LIMIT
    for message_id in range(limit + 1):
      self.service.cancel_message(self.guild, message_id)
    self.assertEqual(len(self.session.cancelled_message_ids), limit)
    self.assertFalse(self.service.consume_cancelled(DUMMY_GUILD_ID, 0))
    self.assertTrue(self.service.consume_cancelled(DUMMY_GUILD_ID, limit))

  def test_cancelling_current_message_skips_it(self):
    self.voice_client.playing = True
    self.session.current_message_id = 20
    self.assertTrue(self.service.cancel_message(self.guild, 20))
    self.assertTrue(self.voice_client.stopped)


if __name__ == "__main__":
  unittest.main()
