"""読み上げのコマンドとメッセージイベント（cogs/playback_cog.py）のテスト。"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import discord
from discord import app_commands

from cogs.playback_cog import PlaybackCog
from tests.support import DUMMY_GUILD_ID, DUMMY_USER_ID, RecordingNotifier


class ReceivedTimeTest(unittest.IsolatedAsyncioTestCase):
  """/synthesis-log の first を、このプログラムに届いた時刻から数える。"""

  async def asyncSetUp(self):
    self.client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(self.client)
    self.message_service = SimpleNamespace(
      handle=mock.AsyncMock(),
      speech_service=SimpleNamespace(speak_and_export=mock.AsyncMock(return_value=None)),
    )
    self.cog = PlaybackCog(
      self.client, tree, self.message_service, voice_service=None,
      error_notifier=RecordingNotifier(),
    )
    self.tree = tree

  async def test_on_message_passes_time_it_was_received(self):
    message = object()
    before = asyncio.get_running_loop().time()
    await self.client.on_message(message)
    after = asyncio.get_running_loop().time()
    self.message_service.handle.assert_awaited_once()
    call = self.message_service.handle.await_args
    self.assertIs(call.args[0], message)
    self.assertTrue(before <= call.kwargs["received_at"] <= after)

  async def test_tts_command_passes_time_it_was_received(self):
    ctx = SimpleNamespace(
      id=1,
      guild=SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=object()),
      user=SimpleNamespace(id=DUMMY_USER_ID),
      created_at=None,
      response=SimpleNamespace(defer=mock.AsyncMock()),
      edit_original_response=mock.AsyncMock(),
    )
    before = asyncio.get_running_loop().time()
    await self.tree.get_command("t").callback(ctx, "おはよう")
    export = self.message_service.speech_service.speak_and_export
    export.assert_awaited_once()
    self.assertGreaterEqual(export.await_args.kwargs["received_at"], before)


if __name__ == "__main__":
  unittest.main()
