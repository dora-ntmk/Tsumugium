"""VOICEVOXの生成記録（services/generation_log_service.py）と、
運営者用の /synthesis-log（cogs/operator_cog.py）のテスト。
"""

import asyncio
from datetime import datetime, timezone
import unittest
from models.streaming_audio import StreamingAudio
from types import SimpleNamespace
from unittest import mock

import discord
from discord import app_commands

from cogs.operator_cog import (
  LOG_COLUMNS,
  OperatorCog,
  build_log_file,
  format_log_table,
  format_summary_fields,
)
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

  def test_summary_of_first(self):
    log = make_log()
    log.record_success(10, 8, 0.0, 2.0, 1, streamed=True).first_seconds = 0.25
    log.record_success(10, 8, 0.0, 3.0, 1, streamed=True).first_seconds = 0.75
    log.record_success(10, 8, 0.0, 1.0, 1).first_seconds = 1.5
    # 再生しなかったもの（入退室通知でない、クリアされたなど）は数えない
    log.record_success(10, 8, 0.0, 1.0, 1)
    summary = log.summarize()
    self.assertEqual(summary.streamed, 2)
    self.assertAlmostEqual(summary.first_average, 2.5 / 3)
    self.assertEqual(summary.first_median, 0.75)
    self.assertEqual(summary.first_max, 1.5)


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
        return StreamingAudio.completed(path)

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

  async def test_received_time_is_passed_to_playback(self):
    self.release.set()
    await self.speech.add_message(self.message())
    [call] = self.voice_service.enqueue.await_args_list
    item = call.args[1]
    self.assertIsNotNone(item.audio.received_at)

  async def test_given_received_time_is_used(self):
    self.release.set()
    await self.speech.add_message(self.message(), received_at=12.5)
    [call] = self.voice_service.enqueue.await_args_list
    self.assertEqual(call.args[1].audio.received_at, 12.5)

  async def test_played_message_is_not_recorded_as_skip(self):
    self.release.set()
    await self.speech.add_message(self.message())
    self.assertEqual(self.log.recent(10), [])
    self.voice_service.enqueue.assert_awaited_once()


class FormatTest(unittest.TestCase):
  def table_lines(self, *records):
    return format_log_table(records).splitlines()

  def test_header_and_success_row(self):
    record = GenerationRecord(
      at=1_800_000_000.0, outcome=SUCCESS, chars=32,
      speaker=2, wait_seconds=0.0, synth_seconds=2.38, attempts=1,
      first_seconds=2.38,
    )
    header, row = self.table_lines(record)
    self.assertEqual(
      header,
      "datetime       | result  | chars | synthesis         | first | wait  | speaker | note",
    )
    self.assertEqual(
      row,
      "01/15 17:00:00 | SUCCESS | 32    | 2.38s (74ms/char) | 2.38s | 0.00s | 2       |",
    )
    self.assertEqual([cell.strip() for cell in header.split("|")], list(LOG_COLUMNS))

  def test_columns_line_up(self):
    records = [
      GenerationRecord(
        at=1_800_000_000.0, outcome=SUCCESS, chars=120, speaker=8,
        wait_seconds=1.2, synth_seconds=6.5, attempts=1, streamed=True,
        first_seconds=0.81,
      ),
      GenerationRecord(at=1_800_000_000.0, outcome=SKIPPED, reason=SKIP_EMPTY),
    ]
    lines = self.table_lines(*records)
    separators = [[i for i, char in enumerate(line) if char == "|"] for line in lines]
    self.assertEqual(separators[0], separators[1])
    self.assertEqual(separators[0], separators[2])

  def test_streamed_row_is_marked(self):
    record = GenerationRecord(
      at=1_800_000_000.0, outcome=SUCCESS, chars=10, speaker=8,
      wait_seconds=0.25, synth_seconds=1.5, attempts=1, streamed=True,
      first_seconds=0.3,
    )
    _, row = self.table_lines(record)
    cells = [cell.strip() for cell in row.split("|")]
    self.assertEqual(cells[4], "0.30s")
    self.assertEqual(cells[7], "stream")

  def test_failure_row_has_attempts_and_error(self):
    record = GenerationRecord(
      at=1_800_000_000.0, outcome=FAILED, chars=3,
      speaker=8, wait_seconds=0.0, synth_seconds=30.0, attempts=4, reason="TimeoutError",
    )
    _, row = self.table_lines(record)
    self.assertEqual(
      [cell.strip() for cell in row.split("|")],
      ["01/15 17:00:00", "FAILED", "3", "30.00s", "-", "0.00s", "8",
       "attempts=4 TimeoutError"],
    )

  def test_skip_row(self):
    record = GenerationRecord(
      at=1_800_000_000.0, outcome=SKIPPED, chars=7, reason=SKIP_CLEARED,
    )
    _, row = self.table_lines(record)
    self.assertEqual(
      [cell.strip() for cell in row.split("|")],
      ["01/15 17:00:00", "SKIPPED", "7", "-", "-", "-", "-", "cleared"],
    )

  def test_file_name_has_time_in_jst(self):
    now = datetime(2026, 11, 19, 2, 18, 30, tzinfo=timezone.utc)
    file = build_log_file([], now)
    self.assertEqual(file.filename, "synthesis-log-20261119-111830.txt")

  def test_summary_fields_match_table(self):
    log = make_log()
    for synth in (1.0, 2.0, 3.0, 4.0):
      log.record_success(10, 8, 0.5, synth, 1).first_seconds = synth / 2
    log.record_success(10, 8, 0.5, 1.0, 1, streamed=True)
    log.record_skip(SKIP_CLEARED, 3)
    log.record_skip(SKIP_EMPTY)
    log.record_failure(5, 8, 2.5, 30.0, 4, "TimeoutError")
    fields = format_summary_fields(log.summarize())
    self.assertEqual(
      fields,
      [
        ("件数", "成功 5 / スキップ 2 / 失敗 1\n（スキップの内訳: クリア 1・読む文字なし 1）"),
        (
          "生成時間（成功のみ）",
          "平均 2.20秒（220ms/字） / 中央値 2.00秒 / 95% 4.00秒 / 最大 4.00秒",
        ),
        (
          "最初の音まで（成功のみ・メッセージから・順番待ちを除く）",
          "平均 1.25秒 / 中央値 1.00秒 / 最大 2.00秒（ストリーミング 1件）",
        ),
        ("順番待ち", "平均 0.83秒 / 最大 2.50秒"),
      ],
    )

  def test_summary_fields_without_records(self):
    fields = dict(format_summary_fields(make_log().summarize()))
    self.assertNotIn("記録の範囲", fields)
    self.assertEqual(fields["件数"], "成功 0 / スキップ 0 / 失敗 0")
    self.assertEqual(
      fields["生成時間（成功のみ）"], "平均 — / 中央値 — / 95% — / 最大 —"
    )


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
    self.edits = []

  async def edit_original_response(self, **kwargs):
    self.edits.append(kwargs)


class SynthesisLogCommandTest(unittest.TestCase):
  def setUp(self):
    client = discord.Client(intents=discord.Intents.none())
    self.tree = app_commands.CommandTree(client)
    self.log = make_log()
    self.log.record_success(10, 8, 0.0, 1.0, 1)
    self.log.record_skip(SKIP_EMPTY)
    OperatorCog(self.tree, self.log, DUMMY_USER_ID, RecordingNotifier())
    self.command = self.tree.get_command("synthesis-log")

  def run_command(self, user_id, count=500, tree=None):
    command = self.command if tree is None else tree.get_command("synthesis-log")
    interaction = FakeInteraction(user_id)
    asyncio.run(command.callback(interaction, count))
    return interaction

  def test_old_name_is_gone(self):
    self.assertIsNone(self.tree.get_command("voicevox-log"))

  def test_command_is_dm_only(self):
    contexts = self.command.allowed_contexts
    self.assertEqual((contexts.guild, contexts.dm_channel, contexts.private_channel), (False, True, False))
    self.assertEqual((self.command.allowed_installs.guild, self.command.allowed_installs.user), (True, False))

  def test_operator_gets_summary_and_file(self):
    interaction = self.run_command(DUMMY_USER_ID)
    self.assertEqual(interaction.response.deferred, {})
    [edit] = interaction.edits
    self.assertIn("直近2件", edit["embed"].description)
    fields = {field.name: field.value for field in edit["embed"].fields}
    self.assertIn("成功 1 / スキップ 1 / 失敗 0", fields["件数"])
    [file] = edit["attachments"]
    self.assertTrue(file.filename.startswith("synthesis-log-"))
    text = file.fp.read().decode("utf-8")
    lines = text.splitlines()
    self.assertEqual(len(lines), 3)
    self.assertTrue(lines[0].startswith("datetime"))
    # 新しい順
    self.assertIn("SKIPPED", lines[1])
    self.assertIn("SUCCESS", lines[2])

  def test_count_limits_rows(self):
    interaction = self.run_command(DUMMY_USER_ID, count=1)
    [edit] = interaction.edits
    [file] = edit["attachments"]
    self.assertEqual(len(file.fp.read().decode("utf-8").splitlines()), 2)

  def test_no_file_without_records(self):
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    OperatorCog(tree, make_log(), DUMMY_USER_ID, RecordingNotifier())
    interaction = self.run_command(DUMMY_USER_ID, tree=tree)
    [edit] = interaction.edits
    self.assertEqual(edit["attachments"], [])
    self.assertEqual(edit["embed"].description, "記録はまだありません")

  def test_others_are_refused(self):
    interaction = self.run_command(DUMMY_OTHER_USER_ID)
    self.assertEqual(interaction.edits, [])
    [sent] = interaction.response.sent
    self.assertTrue(sent["ephemeral"])
    self.assertIn("運営者だけ", sent["embed"].description)

  def test_any_of_several_operators_can_use(self):
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    OperatorCog(tree, self.log, (DUMMY_USER_ID, DUMMY_OTHER_USER_ID), RecordingNotifier())
    interaction = self.run_command(DUMMY_OTHER_USER_ID, tree=tree)
    self.assertEqual(len(interaction.edits), 1)

  def test_refused_without_operator_setting(self):
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    OperatorCog(tree, self.log, None, RecordingNotifier())
    interaction = self.run_command(DUMMY_USER_ID, tree=tree)
    self.assertEqual(interaction.edits, [])


if __name__ == "__main__":
  unittest.main()
