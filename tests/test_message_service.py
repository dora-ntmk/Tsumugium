"""読み上げ対象の判定（services/message_service.py）のテスト。

AGENTS.md「チャンネル判定ロジック」に沿って確かめる。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from services.message_service import MessageService
from services.voice_service import VoiceService
from tests.support import (
  DUMMY_BOT_USER_ID,
  DUMMY_GUILD_ID,
  DUMMY_TEXT_CHANNEL_ID,
  DUMMY_USER_ID,
  DUMMY_VOICE_CHANNEL_ID,
  RecordingNotifier,
)


TEMP_CHANNEL_ID = 333333333333333334
OTHER_CHANNEL_ID = 333333333333333399


class FakeServerConfig:
  def __init__(self, text_target=None):
    self.text_target = text_target

  def get(self, guild_id, key):
    if key == "TextTarget":
      return self.text_target
    raise KeyError(key)


def make_message(
    channel_id,
    content="こんにちは",
    *,
    bot=False,
    silent=False,
    in_voice=True,
    in_guild=True,
):
  voice_client = (
    SimpleNamespace(channel=SimpleNamespace(id=DUMMY_VOICE_CHANNEL_ID))
    if in_voice
    else None
  )
  guild = (
    SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=voice_client)
    if in_guild
    else None
  )
  return SimpleNamespace(
    guild=guild,
    channel=SimpleNamespace(id=channel_id),
    author=SimpleNamespace(id=DUMMY_USER_ID, bot=bot),
    content=content,
    flags=SimpleNamespace(silent=silent),
  )


class MessageServiceTestCase(unittest.IsolatedAsyncioTestCase):
  def make_service(self, text_target=None, temporary=None):
    self.notifier = RecordingNotifier()
    self.voice_service = VoiceService(client=None, error_notifier=self.notifier)
    if temporary is not None:
      self.voice_service.get_session(DUMMY_GUILD_ID).temporary_text_channel_id = temporary
    self.speech_service = mock.MagicMock()
    self.speech_service.add_message = mock.AsyncMock()
    self.connection_service = mock.MagicMock()
    self.connection_service.handle_mention_toggle = mock.AsyncMock()
    client = SimpleNamespace(user=SimpleNamespace(id=DUMMY_BOT_USER_ID))
    return MessageService(
      client,
      FakeServerConfig(text_target),
      self.speech_service,
      self.voice_service,
      self.connection_service,
      error_notifier=self.notifier,
    )


class TargetChannelTest(MessageServiceTestCase):
  def test_without_targets_only_voice_text_chat_is_read(self):
    service = self.make_service()
    self.assertTrue(service.is_target_channel(make_message(DUMMY_VOICE_CHANNEL_ID)))
    self.assertFalse(service.is_target_channel(make_message(DUMMY_TEXT_CHANNEL_ID)))

  def test_text_target_and_voice_text_chat_are_read(self):
    service = self.make_service(text_target=DUMMY_TEXT_CHANNEL_ID)
    self.assertTrue(service.is_target_channel(make_message(DUMMY_TEXT_CHANNEL_ID)))
    self.assertTrue(service.is_target_channel(make_message(DUMMY_VOICE_CHANNEL_ID)))
    self.assertFalse(service.is_target_channel(make_message(OTHER_CHANNEL_ID)))

  def test_temporary_target_is_used_before_text_target(self):
    service = self.make_service(
      text_target=DUMMY_TEXT_CHANNEL_ID,
      temporary=TEMP_CHANNEL_ID,
    )
    self.assertTrue(service.is_target_channel(make_message(TEMP_CHANNEL_ID)))
    self.assertTrue(service.is_target_channel(make_message(DUMMY_VOICE_CHANNEL_ID)))
    self.assertFalse(service.is_target_channel(make_message(DUMMY_TEXT_CHANNEL_ID)))


class HandleTest(MessageServiceTestCase):
  async def test_normal_message_is_spoken(self):
    service = self.make_service()
    message = make_message(DUMMY_VOICE_CHANNEL_ID)
    await service.handle(message)
    self.speech_service.add_message.assert_called_once_with(message)
    self.assertEqual(self.notifier.tasks, ["message speech"])

  async def test_message_outside_target_is_ignored(self):
    service = self.make_service()
    await service.handle(make_message(OTHER_CHANNEL_ID))
    self.speech_service.add_message.assert_not_called()

  async def test_skip_prefix_and_silent_message_are_ignored(self):
    service = self.make_service()
    for message in (
        make_message(DUMMY_VOICE_CHANNEL_ID, "!s よまない"),
        make_message(DUMMY_VOICE_CHANNEL_ID, "!skip よまない"),
        make_message(DUMMY_VOICE_CHANNEL_ID, "よまない", silent=True),
    ):
      with self.subTest(content=message.content, silent=message.flags.silent):
        await service.handle(message)
    self.speech_service.add_message.assert_not_called()

  async def test_message_without_voice_connection_is_ignored(self):
    service = self.make_service()
    await service.handle(make_message(DUMMY_VOICE_CHANNEL_ID, in_voice=False))
    self.speech_service.add_message.assert_not_called()

  async def test_direct_message_is_ignored(self):
    service = self.make_service()
    await service.handle(make_message(DUMMY_VOICE_CHANNEL_ID, in_guild=False))
    self.speech_service.add_message.assert_not_called()

  async def test_s_skips_current_audio(self):
    service = self.make_service()
    message = make_message(DUMMY_VOICE_CHANNEL_ID, "s")
    with mock.patch.object(self.voice_service, "skip_current") as skip_current:
      await service.handle(message)
    skip_current.assert_called_once_with(message.guild)
    self.speech_service.add_message.assert_not_called()

  async def test_bot_message_is_checked_only_against_sound_dictionary(self):
    service = self.make_service()
    message = make_message(DUMMY_VOICE_CHANNEL_ID, bot=True)
    await service.handle(message)
    self.speech_service.add_message.assert_called_once_with(
      message, sounddict_only=True
    )

  async def test_bot_message_outside_target_is_ignored(self):
    service = self.make_service()
    await service.handle(make_message(OTHER_CHANNEL_ID, bot=True))
    self.speech_service.add_message.assert_not_called()

  async def test_bot_mention_alone_toggles_connection(self):
    service = self.make_service()
    for content in (f"<@{DUMMY_BOT_USER_ID}>", f" <@!{DUMMY_BOT_USER_ID}> "):
      with self.subTest(content=content):
        self.connection_service.handle_mention_toggle.reset_mock()
        message = make_message(OTHER_CHANNEL_ID, content, in_voice=False)
        await service.handle(message)
        self.connection_service.handle_mention_toggle.assert_awaited_once_with(message)
    self.speech_service.add_message.assert_not_called()


if __name__ == "__main__":
  unittest.main()
