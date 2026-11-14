"""止めるときにVCから抜け、再起動・更新のあとにつなぎ直す（services/reconnect_service.py）のテスト。"""

import asyncio
import json
import os
import unittest
import unittest.mock

import discord

from clients.managed_discord_client import ManagedDiscordClient
from models.guild_session import GuildSession
from services.reconnect_service import (
  REASON_MANUAL_STOP,
  REASON_UPDATE,
  RESTART_REASON_FILE,
  SESSIONS_FILE,
  ReconnectService,
)
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_OTHER_GUILD_ID,
  DUMMY_TEXT_CHANNEL_ID,
  DUMMY_VOICE_CHANNEL_ID,
  RecordingNotifier,
  TempDirTestCase,
)


OTHER_VOICE_ID = DUMMY_VOICE_CHANNEL_ID + 1
OTHER_TEXT_ID = DUMMY_TEXT_CHANNEL_ID + 1


class FakeMember:
  def __init__(self, bot=False, name="つむぎ"):
    self.bot = bot
    self.display_name = name


class FakeChannel:
  def __init__(self, channel_id, guild, members=()):
    self.id = channel_id
    self.guild = guild
    self.members = list(members)
    self.mention = f"<#{channel_id}>"
    self.sent = []

  async def send(self, *, embed):
    self.sent.append(embed)

  async def connect(self, timeout=60):
    client = FakeVoiceClient(self)
    self.guild.voice_client = client
    self.guild.client.voice_clients.append(client)
    return client


class FakeVoiceClient:
  def __init__(self, channel):
    self.channel = channel
    self.guild = channel.guild
    self.connected = True
    self.disconnects = []

  def is_connected(self):
    return self.connected

  async def disconnect(self, *, force=False):
    self.disconnects.append(force)
    self.connected = False


class FakeGuild:
  def __init__(self, guild_id, client):
    self.id = guild_id
    self.client = client
    self.me = FakeMember(bot=True, name="めたんちゃん")
    self.channels = {}
    self.voice_client = None

  def add(self, channel_id, members=()):
    channel = FakeChannel(channel_id, self, members)
    self.channels[channel_id] = channel
    return channel

  def get_channel(self, channel_id):
    return self.channels.get(channel_id)


class FakeClient:
  def __init__(self):
    self.guilds = {}
    self.voice_clients = []

  def add_guild(self, guild_id):
    guild = FakeGuild(guild_id, self)
    self.guilds[guild_id] = guild
    return guild

  def get_guild(self, guild_id):
    return self.guilds.get(guild_id)


class FakeVoiceService:
  def __init__(self):
    self.sessions = {}

  def get_session(self, guild_id):
    return self.sessions.setdefault(guild_id, GuildSession())


class FakeConnectionService:
  """ConnectionService のうち、つなぎ直しで使う部分だけ。"""

  def __init__(self, voice_service, text_targets=None):
    self.voice_service = voice_service
    self.text_targets = text_targets or {}
    self.voluntary_disconnects = set()
    self.connected = []

  async def connect_voice(self, voice_channel):
    self.connected.append(voice_channel.id)
    return await voice_channel.connect()

  def get_notify_channel(self, guild, voice_channel=None):
    text_id = self.voice_service.get_session(guild.id).temporary_text_channel_id
    if text_id is None:
      text_id = self.text_targets.get(guild.id)
    if text_id:
      return guild.get_channel(text_id)
    return voice_channel


class ReconnectTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.now = 10_000.0
    self.client = FakeClient()
    self.voice_service = FakeVoiceService()
    self.connection = FakeConnectionService(self.voice_service)
    self.guild = self.client.add_guild(DUMMY_GUILD_ID)
    self.voice = self.guild.add(DUMMY_VOICE_CHANNEL_ID, members=[FakeMember()])
    self.text = self.guild.add(DUMMY_TEXT_CHANNEL_ID)
    self.service = self.make_service()

  def make_service(self, state_dir=None):
    return ReconnectService(
      self.client,
      self.tmp_dir if state_dir is None else state_dir,
      self.connection,
      self.voice_service,
      RecordingNotifier(),
      clock=lambda: self.now,
    )

  def write(self, name, data):
    with open(os.path.join(self.tmp_dir, name), "w", encoding="utf-8") as file:
      json.dump(data, file)

  def read(self, name):
    with open(os.path.join(self.tmp_dir, name), encoding="utf-8") as file:
      return json.load(file)

  def exists(self, name):
    return os.path.exists(os.path.join(self.tmp_dir, name))

  def connect_now(self, guild, channel, text_id=None):
    asyncio.run(channel.connect())
    self.voice_service.get_session(guild.id).temporary_text_channel_id = text_id
    return guild.voice_client

  def previous_sessions(self, alive_at=None, text_id=DUMMY_TEXT_CHANNEL_ID):
    self.write(SESSIONS_FILE, {
      "alive_at": self.now - 60 if alive_at is None else alive_at,
      "sessions": [{
        "guild_id": DUMMY_GUILD_ID,
        "voice_channel_id": DUMMY_VOICE_CHANNEL_ID,
        "text_channel_id": text_id,
      }],
    })


class SaveTest(ReconnectTestCase):
  def test_nothing_is_saved_before_restore(self):
    self.previous_sessions()
    self.service.save()
    self.assertEqual(self.read(SESSIONS_FILE)["alive_at"], self.now - 60, "前回の記録を消さない")

  def test_saves_connected_voice_channels(self):
    asyncio.run(self.service.restore())
    self.connect_now(self.guild, self.voice, DUMMY_TEXT_CHANNEL_ID)
    self.service.save()
    self.assertEqual(self.read(SESSIONS_FILE), {
      "alive_at": self.now,
      "sessions": [{
        "guild_id": DUMMY_GUILD_ID,
        "voice_channel_id": DUMMY_VOICE_CHANNEL_ID,
        "text_channel_id": DUMMY_TEXT_CHANNEL_ID,
      }],
    })

  def test_disabled_without_state_dir(self):
    service = self.make_service(state_dir="")
    self.assertFalse(service.enabled)
    self.assertEqual(asyncio.run(service.restore()), 0)
    service.save()
    service.start()
    self.assertIsNone(service._task)


class ShutdownTest(ReconnectTestCase):
  def setUp(self):
    super().setUp()
    asyncio.run(self.service.restore())
    self.voice_client = self.connect_now(self.guild, self.voice, DUMMY_TEXT_CHANNEL_ID)

  def test_update_announces_and_leaves(self):
    self.write(RESTART_REASON_FILE, {"reason": REASON_UPDATE})
    asyncio.run(self.service.shutdown())
    self.assertEqual(len(self.text.sent), 1)
    self.assertEqual(
      self.text.sent[0].description,
      "アップデートのため再起動しています。再起動が完了したら自動で再接続されます。",
    )
    self.assertEqual(self.voice_client.disconnects, [True])
    self.assertIn(DUMMY_GUILD_ID, self.connection.voluntary_disconnects)
    self.assertEqual(len(self.read(SESSIONS_FILE)["sessions"]), 1, "つなぎ直すために残す")

  def test_restart_without_reason_uses_bot_name(self):
    asyncio.run(self.service.shutdown())
    self.assertEqual(
      self.text.sent[0].description,
      "めたんちゃんは再起動しています。再起動が完了したら自動で再接続されます。",
    )

  def test_manual_stop_leaves_silently_and_forgets(self):
    self.write(RESTART_REASON_FILE, {"reason": REASON_MANUAL_STOP})
    asyncio.run(self.service.shutdown())
    self.assertEqual(self.text.sent, [])
    self.assertEqual(self.voice_client.disconnects, [True])
    self.assertFalse(self.exists(SESSIONS_FILE))

  def test_disconnect_events_during_shutdown_do_not_overwrite(self):
    asyncio.run(self.service.shutdown())
    self.client.voice_clients.clear()
    self.service.save()
    self.assertEqual(len(self.read(SESSIONS_FILE)["sessions"]), 1)

  def test_notice_goes_to_voice_chat_without_text_channel(self):
    self.voice_service.get_session(DUMMY_GUILD_ID).temporary_text_channel_id = None
    asyncio.run(self.service.shutdown())
    self.assertEqual(len(self.voice.sent), 1)
    self.assertEqual(self.text.sent, [])


class RestoreTest(ReconnectTestCase):
  def test_reconnects_and_restores_text_channel(self):
    self.previous_sessions()
    self.write(RESTART_REASON_FILE, {"reason": REASON_UPDATE})
    self.assertEqual(asyncio.run(self.service.restore()), 1)
    self.assertEqual(self.connection.connected, [DUMMY_VOICE_CHANNEL_ID])
    self.assertEqual(
      self.voice_service.get_session(DUMMY_GUILD_ID).temporary_text_channel_id, DUMMY_TEXT_CHANNEL_ID
    )
    self.assertEqual(self.text.sent[0].title, "再接続しました")
    self.assertFalse(self.exists(RESTART_REASON_FILE))
    self.assertEqual(self.read(SESSIONS_FILE)["alive_at"], self.now)

  def test_crash_without_reason_reconnects(self):
    self.previous_sessions()
    self.assertEqual(asyncio.run(self.service.restore()), 1)

  def test_manual_stop_does_not_reconnect(self):
    self.previous_sessions()
    self.write(RESTART_REASON_FILE, {"reason": REASON_MANUAL_STOP})
    self.assertEqual(asyncio.run(self.service.restore()), 0)
    self.assertEqual(self.connection.connected, [])
    self.assertFalse(self.exists(RESTART_REASON_FILE))

  def test_more_than_three_minutes_down(self):
    self.previous_sessions(alive_at=self.now - 181)
    self.assertEqual(asyncio.run(self.service.restore()), 0)
    self.previous_sessions(alive_at=self.now - 180)
    self.assertEqual(asyncio.run(self.make_service().restore()), 1)

  def test_empty_voice_channel_is_skipped(self):
    self.voice.members = [FakeMember(bot=True)]
    self.previous_sessions()
    self.assertEqual(asyncio.run(self.service.restore()), 0)
    self.assertEqual(self.text.sent, [])

  def test_already_connected_guild_is_skipped(self):
    self.previous_sessions()
    self.connect_now(self.guild, self.voice)
    self.assertEqual(asyncio.run(self.service.restore()), 0)
    self.assertEqual(self.connection.connected, [])

  def test_missing_text_channel_falls_back(self):
    self.previous_sessions(text_id=999)
    self.assertEqual(asyncio.run(self.service.restore()), 1)
    self.assertIsNone(self.voice_service.get_session(DUMMY_GUILD_ID).temporary_text_channel_id)
    self.assertEqual(self.voice.sent[0].title, "再接続しました")

  def test_one_failure_does_not_stop_others(self):
    other = self.client.add_guild(DUMMY_OTHER_GUILD_ID)
    other_voice = other.add(OTHER_VOICE_ID, members=[FakeMember()])
    self.write(SESSIONS_FILE, {"alive_at": self.now, "sessions": [
      {"guild_id": DUMMY_GUILD_ID, "voice_channel_id": DUMMY_VOICE_CHANNEL_ID, "text_channel_id": None},
      {"guild_id": DUMMY_OTHER_GUILD_ID, "voice_channel_id": OTHER_VOICE_ID, "text_channel_id": None},
    ]})

    async def fail_first(channel):
      if channel.id == DUMMY_VOICE_CHANNEL_ID:
        raise RuntimeError("つなげません")
      return await channel.connect()

    self.connection.connect_voice = fail_first
    self.assertEqual(asyncio.run(self.service.restore()), 1)
    self.assertIsNotNone(other.voice_client)
    self.assertEqual([context for _, context in self.service.error_notifier.exceptions], ["voice reconnect"])
    self.assertEqual(len(other_voice.sent), 1)

  def test_no_file(self):
    self.assertEqual(asyncio.run(self.service.restore()), 0)


class ManagedClientShutdownTest(unittest.TestCase):
  def test_hooks_run_before_closing_and_close_is_idempotent(self):
    order = []

    async def scenario():
      client = ManagedDiscordClient(intents=discord.Intents.none())

      async def hook():
        order.append("hook")

      class Resource:
        def close(self):
          order.append("resource")

      async def fake_super_close(self):
        order.append("gateway")

      client.register_shutdown_hook(hook)
      client.register_closeable(Resource())
      with unittest.mock.patch.object(discord.Client, "close", fake_super_close):
        await client.close()
        await client.close()

    asyncio.run(scenario())
    self.assertEqual(order, ["hook", "gateway", "resource", "gateway"])

  def test_failing_hook_does_not_stop_closing(self):
    notifier = RecordingNotifier()
    order = []

    async def scenario():
      client = ManagedDiscordClient(intents=discord.Intents.none(), error_notifier=notifier)

      async def hook():
        raise RuntimeError("抜けられません")

      async def fake_super_close(self):
        order.append("gateway")

      client.register_shutdown_hook(hook)
      with unittest.mock.patch.object(discord.Client, "close", fake_super_close):
        await client.close()

    asyncio.run(scenario())
    self.assertEqual(order, ["gateway"])
    self.assertIn("抜けられません", notifier.reports[0])
