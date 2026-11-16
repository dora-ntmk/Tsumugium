"""書きかけの音声（models/streaming_audio.py）のテスト。

FFmpegへデータを渡すdiscord.pyのスレッドと同じく、別のスレッドから読む。
"""

import asyncio
import threading
import unittest

from models.streaming_audio import StreamingAudio, StreamingAudioReader
from tests.support import TempDirTestCase


class ReaderThread(threading.Thread):
  """空のバイト列が返るまで読み続ける。"""

  def __init__(self, reader, block_size=4):
    super().__init__(daemon=True)
    self.reader = reader
    self.block_size = block_size
    self.data = bytearray()
    self.got_data = threading.Event()

  def run(self):
    while True:
      chunk = self.reader.read(self.block_size)
      if not chunk:
        return
      self.data.extend(chunk)
      self.got_data.set()


class StreamingAudioTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  def make_audio(self, name="a.wav"):
    audio = StreamingAudio(self.tmp_path("tmp", name))
    audio.open_for_write()
    return audio

  async def test_reader_waits_for_data_and_reads_until_finished(self):
    audio = self.make_audio()
    audio.write(b"RIFF")
    reader = ReaderThread(audio.open_reader())
    reader.start()
    await asyncio.to_thread(reader.got_data.wait, 2)
    # まだ書き終えていないので、読み手は続きを待っている
    self.assertTrue(reader.is_alive())
    audio.write(b"-rest-of-wav")
    audio.finish()
    await asyncio.to_thread(reader.join, 2)
    self.assertFalse(reader.is_alive())
    self.assertEqual(bytes(reader.data), b"RIFF-rest-of-wav")
    self.assertTrue(await audio.wait())

  async def test_close_wakes_waiting_reader(self):
    audio = self.make_audio()
    audio.write(b"RIFF")
    stream_reader = audio.open_reader()
    reader = ReaderThread(stream_reader)
    reader.start()
    await asyncio.to_thread(reader.got_data.wait, 2)
    stream_reader.close()
    await asyncio.to_thread(reader.join, 2)
    self.assertFalse(reader.is_alive())
    self.assertTrue(stream_reader.closed)
    audio.finish()

  async def test_failed_stream_ends_reader_and_reports_failure(self):
    audio = self.make_audio()
    audio.write(b"RIFF")
    reader = ReaderThread(audio.open_reader())
    reader.start()
    audio.finish(succeeded=False)
    await asyncio.to_thread(reader.join, 2)
    self.assertEqual(bytes(reader.data), b"RIFF")
    self.assertFalse(await audio.wait())
    self.assertTrue(audio.finished)

  async def test_reader_gives_up_when_nothing_arrives(self):
    audio = self.make_audio()
    stream_reader = audio.open_reader()
    stream_reader.IDLE_TIMEOUT_SECONDS = 0.05
    self.assertEqual(await asyncio.to_thread(stream_reader.read, 10), b"")
    stream_reader.close()
    audio.finish()

  async def test_completed_reads_existing_file(self):
    path = self.tmp_path("tmp", "done.wav")
    with open(path, "wb") as file:
      file.write(b"RIFF-done")
    audio = StreamingAudio.completed(path)
    self.assertTrue(audio.finished)
    self.assertTrue(await audio.wait())
    self.assertEqual(audio.size, len(b"RIFF-done"))
    reader = audio.open_reader()
    self.assertIsInstance(reader, StreamingAudioReader)
    self.assertEqual(reader.read(), b"RIFF-done")
    self.assertEqual(reader.read(), b"")
    reader.close()

  async def test_finish_twice_keeps_first_result(self):
    audio = self.make_audio()
    audio.finish()
    audio.finish(succeeded=False)
    self.assertTrue(audio.succeeded)


if __name__ == "__main__":
  unittest.main()
