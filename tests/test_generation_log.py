"""VOICEVOXの生成記録（services/generation_log_service.py）と、
運営者用の /synthesis log（cogs/operator_cog.py）のテスト。
"""

import asyncio
from datetime import datetime, timezone
import unittest
from models.streaming_audio import StreamingAudio
from types import SimpleNamespace
from unittest import mock

import discord
from discord import app_commands

from cogs.operator_cog import OperatorCog
from presentation.generation_log_format import (
  LOG_COLUMNS,
  build_log_file,
  format_log_table,
  format_summary_fields,
  make_log_layout,
)
from presentation.layouts import make_layout
from models.generation_record import (
  FAILED,
  MODE_FALLBACK,
  MODE_STREAM,
  MODE_SYNTHESIS,
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
    log.record_success(10, 8, 0.0, 2.0, 1, mode=MODE_STREAM).first_seconds = 0.25
    log.record_success(10, 8, 0.0, 3.0, 1, mode=MODE_STREAM).first_seconds = 0.75
    log.record_success(10, 8, 0.0, 1.0, 1, mode=MODE_SYNTHESIS).first_seconds = 1.5
    # 再生しなかったもの（入退室通知でない、クリアされたなど）は数えない
    log.record_success(10, 8, 0.0, 1.0, 1, mode=MODE_SYNTHESIS)
    summary = log.summarize()
    self.assertEqual(summary.mode_counts, {MODE_STREAM: 2, MODE_SYNTHESIS: 2})
    self.assertAlmostEqual(summary.first_average, 2.5 / 3)
    self.assertEqual(summary.first_median, 0.75)
    self.assertEqual(summary.first_max, 1.5)

  def test_first_excludes_records_that_waited_for_playback(self):
    log = make_log()
    # 前の読み上げを待たなかった（再生の順番待ちが0.5秒未満）ものだけを数える
    waited_little = log.record_success(20, 8, 0.0, 1.0, 1, mode=MODE_STREAM)
    waited_little.first_seconds, waited_little.playback_wait_seconds = 1.2, 0.1
    waited_long = log.record_success(20, 8, 0.0, 1.0, 1, mode=MODE_STREAM)
    waited_long.first_seconds, waited_long.playback_wait_seconds = 0.5, 20.0
    not_measured = log.record_success(20, 8, 0.0, 1.0, 1, mode=MODE_STREAM)
    not_measured.first_seconds = 0.9  # 再生の順番待ちを測る前の記録
    summary = log.summarize()
    stream = summary.by_mode[MODE_STREAM]
    self.assertEqual(stream.first_count, 2)
    self.assertEqual(stream.first_max, 1.2)
    self.assertAlmostEqual(summary.first_average, 1.05)
    # 一覧（txt）には、待ったものの first もそのまま出す
    self.assertEqual(waited_long.first_seconds, 0.5)

  def test_summary_by_mode(self):
    log = make_log()
    # ストリーミング: 文字数が増えても最初の音はあまり延びない
    for chars, synth, first in ((20, 1.0, 0.5), (80, 3.0, 0.7), (150, 6.0, 1.0)):
      log.record_success(chars, 8, 0.0, synth, 1, mode=MODE_STREAM).first_seconds = first
    for chars, synth in ((20, 0.8), (90, 3.0)):
      log.record_success(chars, 8, 0.0, synth, 1, mode=MODE_SYNTHESIS).first_seconds = synth
    log.record_success(40, 3, 0.0, 2.0, 1, mode=MODE_FALLBACK)
    log.record_failure(10, 8, 0.0, 30.0, 4, "TimeoutError", mode=MODE_STREAM)
    log.record_skip(SKIP_EMPTY)
    summary = log.summarize()
    self.assertEqual(
      summary.mode_counts, {MODE_STREAM: 4, MODE_SYNTHESIS: 2, MODE_FALLBACK: 1}
    )
    self.assertEqual(list(summary.by_mode), [MODE_STREAM, MODE_SYNTHESIS, MODE_FALLBACK])
    stream = summary.by_mode[MODE_STREAM]
    self.assertEqual(stream.success, 3)
    self.assertEqual(stream.synth_median, 3.0)
    self.assertEqual(stream.first_median_by_chars, (0.5, 0.7, 1.0))
    synthesis = summary.by_mode[MODE_SYNTHESIS]
    self.assertEqual(synthesis.first_median_by_chars, (0.8, 3.0, None))
    fallback = summary.by_mode[MODE_FALLBACK]
    self.assertIsNone(fallback.first_median)
    self.assertEqual(fallback.first_median_by_chars, (None, None, None))


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
        wait_seconds=1.2, synth_seconds=6.5, attempts=1, mode=MODE_STREAM,
        first_seconds=0.81,
      ),
      GenerationRecord(at=1_800_000_000.0, outcome=SKIPPED, reason=SKIP_EMPTY),
    ]
    lines = self.table_lines(*records)
    separators = [[i for i, char in enumerate(line) if char == "|"] for line in lines]
    self.assertEqual(separators[0], separators[1])
    self.assertEqual(separators[0], separators[2])

  def test_row_shows_mode(self):
    for mode in (MODE_STREAM, MODE_SYNTHESIS, MODE_FALLBACK):
      with self.subTest(mode=mode):
        record = GenerationRecord(
          at=1_800_000_000.0, outcome=SUCCESS, chars=10, speaker=8,
          wait_seconds=0.25, synth_seconds=1.5, attempts=1, mode=mode,
          first_seconds=0.3,
        )
        _, row = self.table_lines(record)
        cells = [cell.strip() for cell in row.split("|")]
        self.assertEqual(cells[4], "0.30s")
        self.assertEqual(cells[7], mode)

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
    for chars, synth, first in ((20, 0.9, 0.5), (80, 3.0, 0.7), (150, 6.0, 1.0)):
      log.record_success(chars, 8, 0.5, synth, 1, mode=MODE_STREAM).first_seconds = first
    for chars, synth in ((20, 0.8), (80, 2.9), (150, 5.5)):
      log.record_success(chars, 8, 0.5, synth, 1, mode=MODE_SYNTHESIS).first_seconds = synth
    log.record_skip(SKIP_CLEARED, 3)
    log.record_skip(SKIP_EMPTY)
    log.record_failure(5, 8, 2.5, 30.0, 4, "TimeoutError", mode=MODE_FALLBACK)
    fields = format_summary_fields(log.summarize())
    self.assertEqual(
      fields,
      [
        (
          "件数",
          "成功 6 / スキップ 2 / 失敗 1"
          "\n（スキップの内訳: クリア 1・読む文字なし 1）"
          "\n（作り方: ストリーミング 3・通常 3・非対応→通常 1）",
        ),
        (
          "生成時間（成功のみ）",
          "ストリーミング: 平均 3.30秒（41ms/字） / 中央値 3.00秒 / 95% 6.00秒 / 最大 6.00秒"
          "\n通常: 平均 3.07秒（38ms/字） / 中央値 2.90秒 / 95% 5.50秒 / 最大 5.50秒",
        ),
        (
          "最初の音まで（成功のみ・前の読み上げを待たなかったもの）",
          "ストリーミング: 平均 0.73秒 / 中央値 0.70秒 / 最大 1.00秒（3件）"
          "\n　文字数別の中央値: 〜30字 0.50秒 / 31〜100字 0.70秒 / 101字〜 1.00秒"
          "\n通常: 平均 3.07秒 / 中央値 2.90秒 / 最大 5.50秒（3件）"
          "\n　文字数別の中央値: 〜30字 0.80秒 / 31〜100字 2.90秒 / 101字〜 5.50秒",
        ),
        ("待ち時間（生成と再生の順番待ち）", "平均 0.79秒 / 最大 2.50秒"),
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
    self.command = self.tree.get_command("synthesis").get_command("log")

  def run_command(self, user_id, count=500, tree=None):
    command = self.command if tree is None else tree.get_command("synthesis").get_command("log")
    interaction = FakeInteraction(user_id)
    asyncio.run(command.callback(interaction, count))
    return interaction

  def test_old_names_are_gone(self):
    self.assertIsNone(self.tree.get_command("voicevox-log"))
    self.assertIsNone(self.tree.get_command("synthesis-log"))

  def test_command_is_dm_only(self):
    group = self.tree.get_command("synthesis")
    contexts = group.allowed_contexts
    self.assertEqual((contexts.guild, contexts.dm_channel, contexts.private_channel), (False, True, False))
    self.assertEqual((group.allowed_installs.guild, group.allowed_installs.user), (True, False))

  def test_operator_gets_summary_and_file(self):
    interaction = self.run_command(DUMMY_USER_ID)
    self.assertEqual(interaction.response.deferred, {})
    [edit] = interaction.edits
    self.assertNotIn("embed", edit)
    texts = layout_texts(edit["view"])
    self.assertEqual(texts[0], "## 音声生成の記録\n直近2件（新しい順）を添付のファイルに書き出しました")
    self.assertIn("**件数**\n成功 1 / スキップ 1 / 失敗 0", texts[1])
    [file] = edit["attachments"]
    [component] = [
      item for item in edit["view"].walk_children() if isinstance(item, discord.ui.File)
    ]
    self.assertEqual(component.url, f"attachment://{file.filename}")
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
    self.assertEqual(layout_texts(edit["view"])[0], "## 音声生成の記録\n記録はまだありません")
    self.assertFalse(any(
      isinstance(item, discord.ui.File) for item in edit["view"].walk_children()
    ))

  def test_others_are_refused(self):
    interaction = self.run_command(DUMMY_OTHER_USER_ID)
    self.assertEqual(interaction.edits, [])
    [sent] = interaction.response.sent
    self.assertTrue(sent["ephemeral"])
    self.assertNotIn("embed", sent)
    self.assertIn("運営者だけ", layout_texts(sent["view"])[0])

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


class FakeUpdateWatch:
  def __init__(self):
    self.requests = []

  async def request_check(self, user_id):
    self.requests.append(user_id)
    return make_layout("新しいバージョンの確認", "更新はありません")


def layout_texts(view):
  return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


class UpdateCommandTest(unittest.TestCase):
  def setUp(self):
    client = discord.Client(intents=discord.Intents.none())
    self.tree = app_commands.CommandTree(client)
    self.watch = FakeUpdateWatch()
    OperatorCog(self.tree, make_log(), DUMMY_USER_ID, RecordingNotifier(), update_watch=self.watch)
    self.command = self.tree.get_command("update")

  def run_command(self, user_id):
    interaction = FakeInteraction(user_id)
    asyncio.run(self.command.callback(interaction))
    return interaction

  def test_command_is_dm_only(self):
    contexts = self.command.allowed_contexts
    self.assertEqual((contexts.guild, contexts.dm_channel, contexts.private_channel), (False, True, False))

  def test_operator_requests_check(self):
    interaction = self.run_command(DUMMY_USER_ID)
    self.assertEqual(interaction.response.deferred, {})
    self.assertEqual(self.watch.requests, [DUMMY_USER_ID])
    [edit] = interaction.edits
    self.assertNotIn("embed", edit)
    self.assertEqual(layout_texts(edit["view"]), ["## 新しいバージョンの確認\n更新はありません"])
    self.assertEqual(edit["attachments"], [])

  def test_others_are_refused(self):
    interaction = self.run_command(DUMMY_OTHER_USER_ID)
    self.assertEqual(self.watch.requests, [])
    [sent] = interaction.response.sent
    self.assertTrue(sent["ephemeral"])
    self.assertNotIn("embed", sent)
    self.assertEqual(
      layout_texts(sent["view"]),
      ["## Bot運営者専用コマンドです\nこのコマンドはBotの運営者以外は使用できません"],
    )


if __name__ == "__main__":
  unittest.main()
