"""ストリーミング合成の設定（repositories/bot_setting_repository.py・
services/streaming_setting_service.py）と、運営者用の /synthesis view・set・reset のテスト。
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import discord
from discord import app_commands

import config
from clients.voicevox_client import VoicevoxClient
from cogs.operator_cog import OperatorCog, format_streaming_setting
from repositories.bot_setting_repository import BotSettingRepository
from services.generation_log_service import GenerationLogService
from services.streaming_setting_service import (
  KEY_ENV_IMPORTED,
  KEY_MODE,
  KEY_SEGMENT,
  KEY_THRESHOLD,
  StreamingSettingService,
)
from tests.support import (
  DUMMY_OTHER_USER_ID,
  DUMMY_USER_ID,
  RecordingNotifier,
  TempDirTestCase,
)


def make_client(**kwargs):
  return VoicevoxClient("http://127.0.0.1:1", **kwargs)


class BotSettingRepositoryTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.path = self.tmp_path("config.db")
    self.repository = BotSettingRepository(self.path)
    self.addCleanup(self.repository.close)

  def test_set_get_and_delete(self):
    self.assertIsNone(self.repository.get("a"))
    self.repository.set_many({"a": "1", "b": "2"})
    self.repository.set_many({"a": "3"})
    self.assertEqual(self.repository.get("a"), "3")
    self.assertEqual(self.repository.get_many(["a", "b", "c"]), {"a": "3", "b": "2"})
    self.repository.delete(["a", "c"])
    self.assertEqual(self.repository.get_many(["a", "b"]), {"b": "2"})

  def test_values_survive_reopen(self):
    self.repository.set_many({"a": "1"})
    reopened = BotSettingRepository(self.path)
    self.addCleanup(reopened.close)
    self.assertEqual(reopened.get("a"), "1")


class StreamingSettingServiceTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.path = self.tmp_path("config.db")
    self.repository = BotSettingRepository(self.path)
    self.addCleanup(self.repository.close)
    self.client = make_client()

  def make_service(self, legacy_env=None):
    return StreamingSettingService(self.repository, self.client, legacy_env)

  def test_defaults_without_saved_values(self):
    service = self.make_service()
    with mock.patch("builtins.print") as printed:
      self.assertEqual(service.load(), [])
    printed.assert_not_called()
    self.assertEqual(service.mode_setting, "auto")
    self.assertEqual(self.client.streaming, config.DEFAULT_VOICEVOX_STREAMING)
    self.assertEqual(self.client.stream_threshold_chars, 0)
    self.assertEqual(self.client.stream_segment_seconds, 0.0)
    # 取り込みは済んだものとして印を付ける
    self.assertEqual(self.repository.get(KEY_ENV_IMPORTED), "1")

  def test_auto_follows_version_default(self):
    with mock.patch("services.streaming_setting_service.DEFAULT_VOICEVOX_STREAMING", "off"):
      service = self.make_service()
      service.load()
      self.assertEqual(self.client.streaming, "off")
      self.assertEqual(service.current().mode, "off")

  def test_legacy_env_is_imported_once(self):
    env = {
      "VOICEVOX_STREAMING": "true",
      "VOICEVOX_STREAM_THRESHOLD": "40",
      "VOICEVOX_STREAM_SEGMENT": "0.5",
    }
    with mock.patch("builtins.print") as printed:
      imported = self.make_service(env).load()
    self.assertEqual(
      imported,
      ["VOICEVOX_STREAMING", "VOICEVOX_STREAM_THRESHOLD", "VOICEVOX_STREAM_SEGMENT"],
    )
    printed.assert_called_once()
    self.assertEqual(
      self.repository.get_many([KEY_MODE, KEY_THRESHOLD, KEY_SEGMENT]),
      {KEY_MODE: "on", KEY_THRESHOLD: "40", KEY_SEGMENT: "0.5"},
    )
    self.assertEqual(
      (self.client.streaming, self.client.stream_threshold_chars, self.client.stream_segment_seconds),
      ("on", 40, 0.5),
    )

    # 2回目からは .env を見ない
    client = make_client()
    service = StreamingSettingService(self.repository, client, {"VOICEVOX_STREAMING": "off"})
    self.assertEqual(service.load(), [])
    self.assertEqual(client.streaming, "on")

  def test_default_values_in_env_are_not_saved(self):
    env = {
      "VOICEVOX_STREAMING": "auto",
      "VOICEVOX_STREAM_THRESHOLD": "0",
      "VOICEVOX_STREAM_SEGMENT": "",
    }
    self.assertEqual(self.make_service(env).load(), [])
    self.assertEqual(self.repository.get_many([KEY_MODE, KEY_THRESHOLD, KEY_SEGMENT]), {})

  def test_unreadable_env_mode_is_imported_as_off(self):
    with mock.patch("builtins.print"):
      self.make_service({"VOICEVOX_STREAMING": "enable"}).load()
    self.assertEqual(self.repository.get(KEY_MODE), "off")

  def test_unreadable_saved_values_fall_back(self):
    self.repository.set_many({
      KEY_ENV_IMPORTED: "1",
      KEY_MODE: "fast",
      KEY_THRESHOLD: "many",
      KEY_SEGMENT: "-1",
    })
    with mock.patch("builtins.print") as printed:
      service = self.make_service()
      service.load()
    self.assertEqual(printed.call_count, 3)
    self.assertEqual(self.client.streaming, "off")
    self.assertEqual(self.client.stream_threshold_chars, 0)
    self.assertEqual(self.client.stream_segment_seconds, 0.0)

  def test_update_changes_only_given_items(self):
    service = self.make_service()
    service.load()
    service.update(mode="hybrid", threshold_chars=30)
    setting = service.update(segment_seconds=0.5)
    self.assertEqual(
      (setting.mode_setting, setting.threshold_chars, setting.segment_seconds),
      ("hybrid", 30, 0.5),
    )
    self.assertEqual(setting.effective_segment_seconds, 0.5)
    self.assertEqual(
      (self.client.streaming, self.client.stream_threshold_chars, self.client.stream_segment_seconds),
      ("hybrid", 30, 0.5),
    )
    # 再起動しても残る
    client = make_client()
    StreamingSettingService(self.repository, client).load()
    self.assertEqual(
      (client.streaming, client.stream_threshold_chars, client.stream_segment_seconds),
      ("hybrid", 30, 0.5),
    )

  def test_auto_and_zero_remove_saved_values(self):
    service = self.make_service()
    service.load()
    service.update(mode="on", threshold_chars=30, segment_seconds=0.5)
    service.update(mode="auto", threshold_chars=0, segment_seconds=0)
    self.assertEqual(self.repository.get_many([KEY_MODE, KEY_THRESHOLD, KEY_SEGMENT]), {})
    self.assertEqual(service.mode_setting, "auto")

  def test_update_rejects_invalid_values(self):
    service = self.make_service()
    service.load()
    for kwargs in ({"mode": "fast"}, {"threshold_chars": -1}, {"segment_seconds": -0.5}):
      with self.subTest(kwargs=kwargs):
        with self.assertRaises(ValueError):
          service.update(**kwargs)
    self.assertEqual(self.repository.get_many([KEY_MODE, KEY_THRESHOLD, KEY_SEGMENT]), {})

  def test_reset_returns_to_defaults_and_keeps_import_mark(self):
    service = self.make_service({"VOICEVOX_STREAMING": "on"})
    with mock.patch("builtins.print"):
      service.load()
    setting = service.reset()
    self.assertEqual(setting.mode_setting, "auto")
    self.assertEqual(self.repository.get_many([KEY_MODE, KEY_THRESHOLD, KEY_SEGMENT]), {})
    self.assertEqual(self.repository.get(KEY_ENV_IMPORTED), "1")
    # .env を取り込み直さない
    client = make_client()
    StreamingSettingService(self.repository, client, {"VOICEVOX_STREAMING": "on"}).load()
    self.assertEqual(client.streaming, config.DEFAULT_VOICEVOX_STREAMING)

  def test_current_reports_unsupported_engine_and_speakers(self):
    service = self.make_service()
    service.load()
    self.client._streaming_supported = False
    self.client._non_streaming_speakers.update({3, 1})
    setting = service.current()
    self.assertFalse(setting.engine_supported)
    self.assertEqual(setting.non_streaming_speakers, (1, 3))


class ConfigureStreamingTest(unittest.TestCase):
  def test_change_applies_to_next_choice(self):
    client = make_client(streaming="off", choose=lambda: 0.0)
    self.assertFalse(client._wants_stream(10))
    client.configure_streaming("on")
    self.assertTrue(client._wants_stream(10))
    client.configure_streaming("hybrid", 20, 0.5)
    self.assertFalse(client._wants_stream(10))
    self.assertTrue(client._wants_stream(20))
    self.assertEqual(client.segment_seconds(), 0.5)
    client.configure_streaming("hybrid", 0, 0)
    self.assertEqual(client.segment_seconds(), VoicevoxClient.STREAM_SEGMENT_SECONDS)

  def test_invalid_values_are_rejected(self):
    client = make_client(streaming="off")
    for args in (("fast",), ("on", -1), ("on", 0, -1.0), ("on", 0, float("nan"))):
      with self.subTest(args=args):
        with self.assertRaises(ValueError):
          client.configure_streaming(*args)
    self.assertEqual(client.streaming, "off")


class FakeResponse:
  def __init__(self):
    self.sent = []

  async def send_message(self, **kwargs):
    self.sent.append(kwargs)


class FakeInteraction:
  def __init__(self, user_id):
    self.user = SimpleNamespace(id=user_id)
    self.response = FakeResponse()


def layout_texts(view):
  return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


class SynthesisSettingCommandTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.repository = BotSettingRepository(self.tmp_path("config.db"))
    self.addCleanup(self.repository.close)
    self.client = make_client()
    self.service = StreamingSettingService(self.repository, self.client)
    self.service.load()
    discord_client = discord.Client(intents=discord.Intents.none())
    self.tree = app_commands.CommandTree(discord_client)
    OperatorCog(
      self.tree,
      GenerationLogService(),
      DUMMY_USER_ID,
      RecordingNotifier(),
      streaming_setting=self.service,
    )
    self.group = self.tree.get_command("synthesis")

  def call(self, name, user_id=DUMMY_USER_ID, **kwargs):
    interaction = FakeInteraction(user_id)
    asyncio.run(self.group.get_command(name).callback(interaction, **kwargs))
    [sent] = interaction.response.sent
    return sent

  def run_set(self, user_id=DUMMY_USER_ID, mode=None, threshold=None, segment=None):
    choice = None if mode is None else app_commands.Choice(name=mode, value=mode)
    return self.call("set", user_id, mode=choice, threshold=threshold, segment=segment)

  def test_group_has_subcommands_and_is_dm_only(self):
    self.assertEqual(
      sorted(command.name for command in self.group.commands),
      ["log", "reset", "set", "view"],
    )
    contexts = self.group.allowed_contexts
    self.assertEqual((contexts.guild, contexts.dm_channel, contexts.private_channel), (False, True, False))

  def test_view_shows_current_setting(self):
    sent = self.call("view")
    self.assertNotIn("embed", sent)
    [text] = layout_texts(sent["view"])
    self.assertTrue(text.startswith("## ストリーミング合成の設定\n"))
    self.assertIn("**モード**: auto（今の既定:", text)
    self.assertIn("自動調整（今は1秒）", text)

  def test_set_saves_and_applies(self):
    sent = self.run_set(mode="on", threshold=30, segment=0.5)
    [text] = layout_texts(sent["view"])
    self.assertIn("設定を変えました", text)
    self.assertIn("常に使う（on）", text)
    self.assertIn("30文字以上", text)
    self.assertIn("0.5秒", text)
    self.assertEqual(self.client.streaming, "on")
    self.assertEqual(self.repository.get(KEY_MODE), "on")

  def test_set_without_items_is_refused(self):
    sent = self.run_set()
    [text] = layout_texts(sent["view"])
    self.assertIn("1つ以上指定", text)
    self.assertEqual(self.repository.get(KEY_MODE), None)

  def test_reset(self):
    self.service.update(mode="on")
    sent = self.call("reset")
    [text] = layout_texts(sent["view"])
    self.assertIn("既定に戻しました", text)
    self.assertEqual(self.service.mode_setting, "auto")

  def test_others_are_refused(self):
    for name, kwargs in (("view", {}), ("reset", {})):
      with self.subTest(name=name):
        sent = self.call(name, DUMMY_OTHER_USER_ID, **kwargs)
        self.assertTrue(sent["ephemeral"])
        self.assertIn("Bot運営者専用コマンドです", layout_texts(sent["view"])[0])
    sent = self.run_set(DUMMY_OTHER_USER_ID, mode="off")
    self.assertTrue(sent["ephemeral"])
    self.assertIsNone(self.repository.get(KEY_MODE))

  def test_unavailable_without_service(self):
    discord_client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(discord_client)
    OperatorCog(tree, GenerationLogService(), DUMMY_USER_ID, RecordingNotifier())
    interaction = FakeInteraction(DUMMY_USER_ID)
    asyncio.run(tree.get_command("synthesis").get_command("view").callback(interaction))
    [sent] = interaction.response.sent
    self.assertIn("今は設定を変えることができません", layout_texts(sent["view"])[0])


class FormatStreamingSettingTest(unittest.TestCase):
  def test_notes_for_unsupported(self):
    from services.streaming_setting_service import StreamingSetting

    text = format_streaming_setting(StreamingSetting(
      mode_setting="hybrid",
      mode="hybrid",
      threshold_chars=0,
      segment_seconds=0.0,
      effective_segment_seconds=1.0,
      engine_supported=False,
      non_streaming_speakers=(1, 3),
    ))
    self.assertIn("文字数で振り分ける（hybrid）", text)
    self.assertIn("五分五分", text)
    self.assertIn("対応していないため", text)
    self.assertIn("1, 3", text)


if __name__ == "__main__":
  unittest.main()
