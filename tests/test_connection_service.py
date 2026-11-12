"""接続状態の退避と復元（services/connection_service.py）のテスト。

AGENTS.md「チャンネル判定ロジック」の後半（強制切断と自発的な切断）を確かめる。
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from models.audio_item import TTSItem
from services.connection_service import ConnectionService
from services.voice_service import VoiceService
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_TEXT_CHANNEL_ID,
  DUMMY_VOICE_CHANNEL_ID,
  RecordingNotifier,
  TempDirTestCase,
)


class ConnectionServiceTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    super().setUp()
    self.notifier = RecordingNotifier()
    self.voice_service = VoiceService(client=None, error_notifier=self.notifier)
    self.service = ConnectionService(
      server_config=mock.MagicMock(),
      dict_manager=None,
      speech_service=None,
      voice_service=self.voice_service,
      error_notifier=self.notifier,
    )
    self.bot_member = SimpleNamespace(bot=True)
    self.voice_channel = SimpleNamespace(id=DUMMY_VOICE_CHANNEL_ID)
    self.guild = SimpleNamespace(
      id=DUMMY_GUILD_ID,
      me=self.bot_member,
      voice_client=None,
    )
    self.bot_member.guild = self.guild
    self.session = self.voice_service.get_session(DUMMY_GUILD_ID)

  async def bot_left(self):
    await self.service.handle_voice_state_update(
      self.bot_member,
      SimpleNamespace(channel=self.voice_channel),
      SimpleNamespace(channel=None),
    )

  async def bot_joined(self):
    await self.service.handle_voice_state_update(
      self.bot_member,
      SimpleNamespace(channel=None),
      SimpleNamespace(channel=self.voice_channel),
    )

  async def test_forced_disconnect_saves_and_reconnect_restores_text_channel(self):
    self.session.temporary_text_channel_id = DUMMY_TEXT_CHANNEL_ID

    await self.bot_left()
    self.assertIsNone(self.session.temporary_text_channel_id)
    self.assertEqual(self.session.pending_text_channel_id, DUMMY_TEXT_CHANNEL_ID)

    await self.bot_joined()
    self.assertEqual(self.session.temporary_text_channel_id, DUMMY_TEXT_CHANNEL_ID)
    self.assertIsNone(self.session.pending_text_channel_id)

  async def test_voluntary_disconnect_discards_text_channel(self):
    self.session.temporary_text_channel_id = DUMMY_TEXT_CHANNEL_ID
    self.guild.voice_client = SimpleNamespace(disconnect=mock.AsyncMock())

    await self.service.disconnect(self.guild)
    self.assertIn(DUMMY_GUILD_ID, self.service.voluntary_disconnects)

    await self.bot_left()
    self.assertIsNone(self.session.temporary_text_channel_id)
    self.assertIsNone(self.session.pending_text_channel_id)
    self.assertNotIn(DUMMY_GUILD_ID, self.service.voluntary_disconnects)

    await self.bot_joined()
    self.assertIsNone(self.session.temporary_text_channel_id)

  async def test_disconnect_without_voice_client_is_not_marked_voluntary(self):
    await self.service.disconnect(self.guild)
    self.assertNotIn(DUMMY_GUILD_ID, self.service.voluntary_disconnects)

  async def test_bot_disconnect_discards_queue_and_audio_files(self):
    path = self.tmp_path("tmp", "queued.wav")
    with open(path, "wb") as file:
      file.write(b"dummy")
    await self.session.queue.put(TTSItem(path))

    await self.bot_left()

    self.assertTrue(self.session.queue.empty())
    self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
  unittest.main()
