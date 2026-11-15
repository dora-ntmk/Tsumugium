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


class AutoJoinAfterLeaveTest(unittest.IsolatedAsyncioTestCase):
  """人が残っているVCから抜けたあとは、そのVCが空になるまでAutoJoinしない。"""

  def setUp(self):
    self.voice_service = VoiceService(client=None, error_notifier=RecordingNotifier())
    config = {"AutoJoin": True, "VoiceTarget": DUMMY_VOICE_CHANNEL_ID,
              "TextTarget": None, "AccessNotice": False}
    server_config = mock.MagicMock()
    server_config.get.side_effect = lambda guild_id, key: config[key]
    self.service = ConnectionService(
      server_config=server_config,
      dict_manager=None,
      speech_service=None,
      voice_service=self.voice_service,
      error_notifier=RecordingNotifier(),
    )
    permissions = SimpleNamespace(
      connect=True, speak=True, view_channel=True, send_messages=True,
    )
    self.voice_channel = SimpleNamespace(
      id=DUMMY_VOICE_CHANNEL_ID,
      members=[],
      mention="<#vc>",
      permissions_for=lambda member: permissions,
      send=mock.AsyncMock(),
    )
    self.bot_member = SimpleNamespace(bot=True)
    self.guild = SimpleNamespace(
      id=DUMMY_GUILD_ID,
      me=self.bot_member,
      voice_client=None,
      get_channel=lambda channel_id: self.voice_channel,
    )
    self.bot_member.guild = self.guild
    self.connect = mock.AsyncMock(return_value=object())
    self.service._connect_voice = self.connect
    self.session = self.voice_service.get_session(DUMMY_GUILD_ID)

  def member(self, user_id, bot=False):
    return SimpleNamespace(id=user_id, bot=bot, guild=self.guild, send=mock.AsyncMock())

  async def join(self, member):
    self.voice_channel.members.append(member)
    await self.service.handle_voice_state_update(
      member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.voice_channel),
    )

  async def leave(self, member):
    self.voice_channel.members.remove(member)
    await self.service.handle_voice_state_update(
      member, SimpleNamespace(channel=self.voice_channel), SimpleNamespace(channel=None),
    )

  async def bot_left(self):
    await self.service.handle_voice_state_update(
      self.bot_member,
      SimpleNamespace(channel=self.voice_channel),
      SimpleNamespace(channel=None),
    )

  async def test_auto_join_when_first_human_joins(self):
    with mock.patch("asyncio.sleep", mock.AsyncMock()):
      await self.join(self.member(1))
    self.connect.assert_awaited_once()

  async def test_bot_joining_does_not_trigger_auto_join(self):
    self.voice_channel.members.append(self.member(1))
    with mock.patch("asyncio.sleep", mock.AsyncMock()):
      await self.join(self.member(2, bot=True))
    self.connect.assert_not_awaited()

  async def test_no_auto_join_after_leaving_while_humans_remain(self):
    human = self.member(1)
    self.voice_channel.members.append(human)
    await self.bot_left()
    self.assertEqual(
      self.session.auto_join_suppressed_channel_id, DUMMY_VOICE_CHANNEL_ID,
    )

    with mock.patch("asyncio.sleep", mock.AsyncMock()):
      # 古いキャッシュなどで人数が1人に見えても、つなぎ直さない
      self.voice_channel.members.remove(human)
      await self.join(self.member(2))
      await self.join(self.member(3, bot=True))
    self.connect.assert_not_awaited()

  async def test_auto_join_resumes_after_channel_becomes_empty(self):
    human = self.member(1)
    self.voice_channel.members.append(human)
    await self.bot_left()

    await self.leave(human)
    self.assertIsNone(self.session.auto_join_suppressed_channel_id)

    with mock.patch("asyncio.sleep", mock.AsyncMock()):
      await self.join(self.member(2))
    self.connect.assert_awaited_once()

  async def test_leaving_empty_channel_does_not_suppress(self):
    await self.bot_left()
    self.assertIsNone(self.session.auto_join_suppressed_channel_id)

  async def test_bot_reconnect_clears_suppression(self):
    self.voice_channel.members.append(self.member(1))
    await self.bot_left()
    with mock.patch.object(self.voice_service, "start_keepalive"), \
        mock.patch.object(self.voice_service, "reset_speaking_soon"):
      await self.service.handle_voice_state_update(
        self.bot_member,
        SimpleNamespace(channel=None),
        SimpleNamespace(channel=self.voice_channel),
      )
    self.assertIsNone(self.session.auto_join_suppressed_channel_id)


if __name__ == "__main__":
  unittest.main()
