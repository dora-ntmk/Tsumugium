"""音声キュー（services/voice_service.py）のテスト。

AGENTS.md「音声再生アーキテクチャ」のキューの型と /clear の動きを確かめる。
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import discord

from models.audio_item import SoundboardItem, TTSItem
from models.streaming_audio import StreamingAudio
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


class PlayingVoiceClient:
  """再生を始めたら、渡した音源と終わったときの処理を覚えておく。"""

  def __init__(self):
    self.source = None
    self.after = None

  def is_connected(self):
    return True

  def is_playing(self):
    return False

  def play(self, source, after=None):
    self.source = source
    self.after = after


class StreamingPlaybackTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  """書きかけの音声は、届いた分からFFmpegへパイプで渡す。"""

  async def asyncSetUp(self):
    self.notifier = RecordingNotifier()
    loop = asyncio.get_running_loop()
    self.service = VoiceService(
      client=SimpleNamespace(loop=loop), error_notifier=self.notifier
    )
    self.voice_client = PlayingVoiceClient()
    self.guild = SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=self.voice_client)
    self.session = self.service.get_session(DUMMY_GUILD_ID)

  def streaming_audio(self):
    audio = StreamingAudio(self.tmp_path("tmp", "stream.wav"))
    audio.open_for_write()
    audio.write(b"RIFF-head")
    self.addCleanup(audio.finish)
    return audio

  async def test_unfinished_audio_is_piped_to_ffmpeg(self):
    audio = self.streaming_audio()
    source = mock.Mock()
    with mock.patch(
        "services.voice_service.discord.FFmpegOpusAudio", return_value=source
    ) as ffmpeg:
      await self.service.play(self.guild, audio.path, 1, audio=audio)
    args, kwargs = ffmpeg.call_args
    reader = args[0]
    self.assertTrue(kwargs["pipe"])
    # 少しずつ届く音声を、ためずにすぐ出す（ためると数秒遅れる）
    self.assertEqual(
      kwargs["before_options"].split(),
      ["-f", "wav", "-probesize", "32", "-analyzeduration", "0"],
    )
    self.assertIs(self.voice_client.source.original, source)
    self.assertEqual(self.session.current_message_id, 1)
    self.assertFalse(reader.closed)

    # 再生が終わったら読み手を閉じ、ファイルを消す
    self.voice_client.after(None)
    await asyncio.sleep(0.05)
    self.assertTrue(reader.closed)
    self.assertFalse(os.path.exists(audio.path))
    self.assertIsNone(self.session.current_message_id)

  async def test_finished_audio_is_played_from_file(self):
    path = self.tmp_path("tmp", "done.wav")
    with open(path, "wb") as file:
      file.write(b"RIFF")
    audio = StreamingAudio.completed(path)
    source = mock.Mock()
    with mock.patch(
        "services.voice_service.discord.FFmpegOpusAudio"
    ) as ffmpeg:
      ffmpeg.from_probe = mock.AsyncMock(return_value=source)
      await self.service.play(self.guild, path, 2, audio=audio)
    ffmpeg.from_probe.assert_awaited_once_with(path)
    ffmpeg.assert_not_called()
    self.assertIs(self.voice_client.source.original, source)
    self.voice_client.after(None)
    await asyncio.sleep(0.05)

  async def test_skipped_stream_stops_ffmpeg_and_reader(self):
    audio = self.streaming_audio()
    self.session.skipping = True
    source = mock.Mock()
    with mock.patch(
        "services.voice_service.discord.FFmpegOpusAudio", return_value=source
    ) as ffmpeg:
      await self.service.play(self.guild, audio.path, 3, audio=audio)
    reader = ffmpeg.call_args.args[0]
    self.assertIsNone(self.voice_client.source)
    self.assertTrue(reader.closed)
    source.cleanup.assert_called_once()
    await asyncio.sleep(0.05)
    self.assertFalse(os.path.exists(audio.path))

  async def test_first_packet_marks_played(self):
    path = self.tmp_path("tmp", "first.wav")
    with open(path, "wb") as file:
      file.write(b"RIFF")
    audio = StreamingAudio.completed(path)
    loop = asyncio.get_running_loop()
    audio.received_at = loop.time() - 2.0
    audio.wait_seconds = 0.5
    source = mock.Mock()
    source.read.side_effect = [b"", b"opus-packet", b"opus-packet"]
    with mock.patch(
        "services.voice_service.discord.FFmpegOpusAudio"
    ) as ffmpeg:
      ffmpeg.from_probe = mock.AsyncMock(return_value=source)
      await self.service.play(self.guild, path, 4, audio=audio)
    played = self.voice_client.source
    self.assertIsInstance(played, discord.AudioSource)

    # 空のデータでは決めず、最初の音のデータを渡したときに決める
    await asyncio.to_thread(played.read)
    await asyncio.sleep(0.01)
    self.assertIsNone(audio.first_seconds)
    await asyncio.to_thread(played.read)
    await asyncio.sleep(0.01)
    first = audio.first_seconds
    self.assertGreaterEqual(first, 1.5)
    self.assertLess(first, 2.5)
    await asyncio.to_thread(played.read)
    await asyncio.sleep(0.01)
    self.assertEqual(audio.first_seconds, first)

    played.cleanup()
    source.cleanup.assert_called_once()
    self.voice_client.after(None)
    await asyncio.sleep(0.05)

  async def test_enqueue_and_play_measure_playback_wait(self):
    path = self.tmp_path("tmp", "queued.wav")
    with open(path, "wb") as file:
      file.write(b"RIFF")
    audio = StreamingAudio.completed(path)
    self.service.play_loop = mock.AsyncMock()
    await self.service.enqueue(self.guild, TTSItem(path, message_id=5, audio=audio))
    self.assertIsNotNone(audio.enqueued_at)
    self.assertIsNone(audio.playback_wait_seconds)
    audio.enqueued_at -= 3.0  # キューで3秒待ったことにする
    with mock.patch(
        "services.voice_service.discord.FFmpegOpusAudio"
    ) as ffmpeg:
      ffmpeg.from_probe = mock.AsyncMock(return_value=mock.Mock())
      await self.service.play(self.guild, path, 5, audio=audio)
    self.assertGreaterEqual(audio.playback_wait_seconds, 3.0)
    self.assertLess(audio.playback_wait_seconds, 4.0)
    self.voice_client.after(None)
    await asyncio.sleep(0.05)
