"""VOICEVOXの生成記録（services/generation_log_service.py）と、
運営者用の /voicevox-log（cogs/operator_cog.py）のテスト。
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import discord
from discord import app_commands

from cogs.operator_cog import OperatorCog, format_record, format_summary_fields
from models.generation_record import (
  FAILED,
  SKIP_CLEARED,
  SKIP_DELETED,
  SKIP_EMPTY,
  SKIPPED,
  SUCCESS,
  GenerationRecord,
)
from services.generation_log_service import GenerationLogService
from services.speech_service import SpeechService
from services.voice_service import VoiceService
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_OTHER_USER_ID,
  DUMMY_USER_ID,
  RecordingNotifier,
  TempDirTestCase,
)


class FakeClock:
  def __init__(self, now=1_800_000_000.0):
    self.now = now

  def __call__(self):
    return self.now


def make_log(capacity=500):
  return GenerationLogService(capacity, clock=FakeClock())


class GenerationLogServiceTest(unittest.TestCase):
  def test_record_has_no_server_or_text(self):
    # 運営者が内容を推し量れないよう、サーバーと文章は持たない
    fields = set(GenerationRecord.__slots__)
    self.assertNotIn("guild_id", fields)
    self.assertNotIn("text", fields)

  def test_empty_summary(self):
    summary = make_log().summarize()
    self.assertEqual(summary.total, 0)
    self.assertIsNone(summary.synth_average)
    self.assertIsNone(summary.first_at)

  def test_old_records_are_dropped(self):
    log = make_log(capacity=3)
    for chars in range(1, 6):
      log.record_success(chars, 8, 0.0, 1.0, 1)
    self.assertEqual([record.chars for record in log.recent(10)], [5, 4, 3])

  def test_recent_returns_newest_first(self):
    log = make_log()
    log.record_success(1, 8, 0.0, 1.0, 1)
    log.record_skip(SKIP_EMPTY)
    self.assertEqual([r.outcome for r in log.recent(10)], [SKIPPED, SUCCESS])
    self.assertEqual(len(log.recent(1)), 1)
    self.assertEqual(log.recent(0), [])

  def test_summary_uses_only_successes_for_time(self):
    log = make_log()
    for synth in (1.0, 2.0, 3.0, 4.0):
      log.record_success(10, 8, 0.5, synth, 1)
    log.record_failure(10, 8, 2.5, 30.0, 5, "TimeoutError")
    log.record_skip(SKIP_EMPTY)
    log.record_skip(SKIP_CLEARED, 10)
    log.record_skip(SKIP_CLEARED, 10)
    summary = log.summarize()
    self.assertEqual(
      (summary.total, summary.success, summary.failed, summary.skipped), (8, 4, 1, 3)
    )
    self.assertEqual(summary.skip_reasons, {SKIP_EMPTY: 1, SKIP_CLEARED: 2})
    self.assertEqual(summary.synth_average, 2.5)
    self.assertEqual(summary.synth_median, 2.0)
    self.assertEqual(summary.synth_p95, 4.0)
    self.assertEqual(summary.synth_max, 4.0)
    self.assertAlmostEqual(summary.seconds_per_char_average, 0.25)
    # 順番待ちは、生成したもの（成功・失敗）で出す
    self.assertAlmostEqual(summary.wait_average, 0.9)
    self.assertEqual(summary.wait_max, 2.5)


class SpeechServiceSkipRecordTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.voice_service = VoiceService(client=None, error_notifier=RecordingNotifier())
    self.voice_service.enqueue = mock.AsyncMock()
    self.log = make_log()
    self.release = asyncio.Event()
    self.started = asyncio.Event()
    owner = self

    class FakeVoicevox:
      async def generate(self, text, guild_id, message_id, *args, **kwargs):
        owner.started.set()
        await owner.release.wait()
        path = owner.tmp_path(f"{message_id}.wav")
        with open(path, "wb") as file:
          file.write(b"RIFF")
        return path

    config = SimpleNamespace(
      get=lambda guild_id, key: {"Speaker": 8, "MaxChar": 100}[key],
      volume_to_vvtts=lambda guild_id: 1.0,
      speed_to_vvtts=lambda guild_id, spaced_out=False: 1.0,
    )
    self.speech = SpeechService(
      FakeVoicevox(), config, None, self.voice_service, generation_log=self.log
    )
    self.guild = SimpleNamespace(id=DUMMY_GUILD_ID, voice_client=None)

  def message(self, content="こんにちは"):
    return SimpleNamespace(
      id=1, guild=self.guild, content=content, stickers=[], reference=None,
      attachments=[], mentions=[], author=SimpleNamespace(id=DUMMY_USER_ID),
    )

  async def test_empty_message_is_recorded(self):
    await self.speech.add_message(self.message(""))
    [record] = self.log.recent(10)
    self.assertEqual((record.outcome, record.reason, record.chars), (SKIPPED, SKIP_EMPTY, 0))

  async def test_cleared_message_is_recorded(self):
    task = asyncio.create_task(self.speech.add_message(self.message()))
    await self.started.wait()
    self.voice_service.begin_clear(self.guild, instant=True)
    self.release.set()
    await task
    [record] = self.log.recent(10)
    self.assertEqual((record.outcome, record.reason, record.chars), (SKIPPED, SKIP_CLEARED, 5))

  async def test_deleted_message_is_recorded(self):
    task = asyncio.create_task(self.speech.add_message(self.message()))
    await self.started.wait()
    self.voice_service.cancel_message(self.guild, 1)
    self.release.set()
    await task
    [record] = self.log.recent(10)
    self.assertEqual((record.outcome, record.reason), (SKIPPED, SKIP_DELETED))

  async def test_played_message_is_not_recorded_as_skip(self):
    self.release.set()
    await self.speech.add_message(self.message())
    self.assertEqual(self.log.recent(10), [])
    self.voice_service.enqueue.assert_awaited_once()


class FormatTest(unittest.TestCase):
  def test_success_line(self):
    record = GenerationRecord(
      at=1_800_000_000.0, outcome=SUCCESS, chars=10,
      speaker=8, wait_seconds=0.25, synth_seconds=1.5, attempts=1,
    )
    self.assertEqual(
      format_record(record),
      "01/15 17:00:00 成功 10字 生成1.50秒 (150ms/字) 待ち0.25秒 話者8",
    )

  def test_failure_line_has_attempts_and_error(self):
    record = GenerationRecord(
      at=1_800_000_000.0, outcome=FAILED, chars=3,
      speaker=8, wait_seconds=0.0, synth_seconds=30.0, attempts=4, reason="TimeoutError",
    )
    self.assertEqual(
      format_record(record),
      "01/15 17:00:00 失敗 3字 生成30.00秒 待ち0.00秒 話者8 試行4回 TimeoutError",
    )

  def test_skip_line(self):
    record = GenerationRecord(
      at=1_800_000_000.0, outcome=SKIPPED, chars=7,
      reason=SKIP_CLEARED,
    )
    self.assertEqual(format_record(record), "01/15 17:00:00 スキップ クリア 7字")

  def test_summary_fields_without_records(self):
    fields = dict(format_summary_fields(make_log().summarize(), 500))
    self.assertIn("記録はまだありません", fields["記録の範囲"])
    self.assertIn("平均 —", fields["生成時間（成功のみ）"])


class FakeResponse:
  def __init__(self):
    self.sent = []
    self.deferred = None

  async def send_message(self, **kwargs):
    self.sent.append(kwargs)

  async def defer(self, **kwargs):
    self.deferred = kwargs


class FakeInteraction:
  def __init__(self, user_id):
    self.user = SimpleNamespace(id=user_id)
    self.response = FakeResponse()
    self.embeds = []

  async def edit_original_response(self, *, embed):
    self.embeds.append(embed)


class VoicevoxLogCommandTest(unittest.TestCase):
  def setUp(self):
    client = discord.Client(intents=discord.Intents.none())
    self.tree = app_commands.CommandTree(client)
    self.log = make_log()
    self.log.record_success(10, 8, 0.0, 1.0, 1)
    self.log.record_skip(SKIP_EMPTY)
    OperatorCog(self.tree, self.log, DUMMY_USER_ID, RecordingNotifier())
    self.command = self.tree.get_command("voicevox-log")

  def run_command(self, user_id, count=10):
    interaction = FakeInteraction(user_id)
    asyncio.run(self.command.callback(interaction, count))
    return interaction

  def test_command_is_dm_only(self):
    contexts = self.command.allowed_contexts
    self.assertEqual((contexts.guild, contexts.dm_channel, contexts.private_channel), (False, True, False))
    self.assertEqual((self.command.allowed_installs.guild, self.command.allowed_installs.user), (True, False))

  def test_operator_sees_log(self):
    interaction = self.run_command(DUMMY_USER_ID)
    self.assertEqual(interaction.response.deferred, {})
    [embed] = interaction.embeds
    self.assertIn("直近2件", embed.description)
    self.assertIn("成功 10字", embed.description)
    fields = {field.name: field.value for field in embed.fields}
    self.assertIn("成功 1 / スキップ 1 / 失敗 0", fields["件数"])

  def test_others_are_refused(self):
    interaction = self.run_command(DUMMY_OTHER_USER_ID)
    self.assertEqual(interaction.embeds, [])
    [sent] = interaction.response.sent
    self.assertTrue(sent["ephemeral"])
    self.assertIn("運営者だけ", sent["embed"].description)

  def test_refused_without_operator_setting(self):
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    OperatorCog(tree, self.log, None, RecordingNotifier())
    interaction = FakeInteraction(DUMMY_USER_ID)
    asyncio.run(tree.get_command("voicevox-log").callback(interaction, 10))
    self.assertEqual(interaction.embeds, [])


if __name__ == "__main__":
  unittest.main()
