"""音声生成の記録の自動送信（services/generation_log_report_service.py）と、
GenerationLogService の「まだ送っていない記録」のテスト。
"""

import asyncio
import unittest

import discord

from models.generation_record import SKIP_EMPTY
from services.error_notification_service import ErrorNotificationService
from services.generation_log_report_service import GenerationLogReportService
from services.generation_log_service import GenerationLogService
from tests.support import DUMMY_OTHER_USER_ID, DUMMY_USER_ID


def layout_texts(view):
  return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


class FakeUser:
  def __init__(self, user_id, fail=False):
    self.id = user_id
    self.fail = fail
    self.sent = []

  async def send(self, **kwargs):
    if self.fail:
      raise RuntimeError("DMを送れません")
    self.sent.append(kwargs)
    return object()


class FakeClient:
  def __init__(self, *users):
    self.users = {user.id: user for user in users}

  def get_user(self, user_id):
    return self.users.get(user_id)

  async def fetch_user(self, user_id):
    raise RuntimeError("見つかりません")


def add_success(log, count):
  for index in range(count):
    log.record_success(10 + index, 8, 0.0, 1.0, 1)


def file_rows(kwargs):
  return kwargs["file"].fp.read().decode("utf-8").splitlines()[1:]


def sent_pairs(user):
  """（集計のメッセージ, 一覧のファイルだけのメッセージ）の組にする。"""
  messages = user.sent
  pairs = list(zip(messages[0::2], messages[1::2]))
  for layout, file in pairs:
    assert layout["files"] == [], "Components v2 のメッセージにはファイルを載せない"
    assert set(file) == {"file"}, "一覧はファイルだけのメッセージで送る"
  return pairs


class UnsentRecordsTest(unittest.TestCase):
  def test_take_unsent_returns_only_new_records_oldest_first(self):
    log = GenerationLogService(5)
    add_success(log, 2)
    self.assertEqual([record.chars for record in log.take_unsent()], [10, 11])
    self.assertEqual(log.take_unsent(), [])
    log.record_skip(SKIP_EMPTY)
    self.assertEqual(len(log.take_unsent()), 1)
    # 送ったあともメモリには残る
    self.assertEqual(len(log.recent(10)), 3)

  def test_listener_is_called_when_capacity_is_reached(self):
    log = GenerationLogService(3)
    taken = []
    log.set_full_listener(lambda: taken.append(log.take_unsent()))
    add_success(log, 2)
    self.assertEqual(taken, [])
    add_success(log, 1)
    self.assertEqual(len(taken), 1)
    self.assertEqual(len(taken[0]), 3)
    add_success(log, 3)
    self.assertEqual(len(taken), 2)
    self.assertEqual([record.chars for record in taken[1]], [10, 11, 12])

  def test_unsent_count_never_exceeds_capacity(self):
    log = GenerationLogService(3)
    add_success(log, 5)
    self.assertEqual(log.unsent_count, 3)
    self.assertEqual(len(log.take_unsent()), 3)

  def test_summarize_given_records(self):
    log = GenerationLogService(10)
    add_success(log, 2)
    log.record_skip(SKIP_EMPTY)
    summary = log.summarize(log.recent(1)[::-1])
    self.assertEqual((summary.total, summary.success, summary.skipped), (1, 0, 1))
    self.assertEqual(log.summarize().total, 3)


class ReportServiceTest(unittest.TestCase):
  def setUp(self):
    self.user = FakeUser(DUMMY_USER_ID)
    self.other = FakeUser(DUMMY_OTHER_USER_ID)
    self.client = FakeClient(self.user, self.other)

  def make_service(self, log, operators=(DUMMY_USER_ID, DUMMY_OTHER_USER_ID), **kwargs):
    kwargs.setdefault("delay", 0)
    return GenerationLogReportService(
      self.client, log, operators, ErrorNotificationService(), **kwargs,
    )

  def test_sends_when_capacity_is_reached(self):
    async def scenario():
      log = GenerationLogService(3)
      self.make_service(log)
      add_success(log, 3)
      await asyncio.sleep(0.01)
      return log

    log = asyncio.run(scenario())
    for user in (self.user, self.other):
      [(summary, listing)] = sent_pairs(user)
      self.assertNotIn("embed", summary)
      texts = layout_texts(summary["view"])
      self.assertEqual(
        texts[0],
        "## 音声生成の記録\n記録が3件たまったため送ります"
        "\n-# 一覧（新しい順）は次のメッセージのファイルに書き出しました",
      )
      self.assertIn("成功 3 / スキップ 0 / 失敗 0", texts[1])
      rows = file_rows(listing)
      # 新しい順
      self.assertEqual([row.split("|")[2].strip() for row in rows], ["12", "11", "10"])
    # 人ごとに別のファイルを作る
    self.assertIsNot(self.user.sent[1]["file"], self.other.sent[1]["file"])
    self.assertEqual(log.unsent_count, 0)

  def test_shutdown_sends_only_unsent_records(self):
    async def scenario():
      log = GenerationLogService(3)
      service = self.make_service(log, reason_reader=lambda: "update")
      add_success(log, 4)
      await asyncio.sleep(0.01)
      await service.shutdown()

    asyncio.run(scenario())
    (_, first_file), (second, second_file) = sent_pairs(self.user)
    self.assertEqual(len(file_rows(first_file)), 3)
    self.assertTrue(layout_texts(second["view"])[0].startswith(
      "## 音声生成の記録\nアップデートの前に、まだ送っていない記録を送ります",
    ))
    self.assertEqual(len(file_rows(second_file)), 1)
    self.assertIn("成功 1 / スキップ 0 / 失敗 0", layout_texts(second["view"])[1])

  def test_shutdown_without_unsent_records_sends_nothing(self):
    async def scenario():
      log = GenerationLogService(3)
      service = self.make_service(log)
      await service.shutdown()

    asyncio.run(scenario())
    self.assertEqual(self.user.sent, [])

  def test_shutdown_sends_waiting_batch_at_once(self):
    async def scenario():
      log = GenerationLogService(2)
      service = self.make_service(log, delay=60)
      add_success(log, 3)
      await service.shutdown()

    asyncio.run(scenario())
    (first, first_file), (second, second_file) = sent_pairs(self.user)
    self.assertIn("2件たまった", layout_texts(first["view"])[0])
    self.assertEqual(len(file_rows(first_file)), 2)
    self.assertIn("終了する前に", layout_texts(second["view"])[0])
    self.assertEqual(len(file_rows(second_file)), 1)

  def test_shutdown_descriptions(self):
    cases = {
      None: "再起動する前に",
      "manual_stop": "停止する前に",
      "update": "アップデートの前に",
    }
    for reason, expected in cases.items():
      with self.subTest(reason=reason):
        user = FakeUser(DUMMY_USER_ID)
        self.client = FakeClient(user)

        async def scenario():
          log = GenerationLogService(10)
          service = self.make_service(log, operators=DUMMY_USER_ID, reason_reader=lambda: reason)
          add_success(log, 1)
          await service.shutdown()

        asyncio.run(scenario())
        self.assertIn(expected, layout_texts(user.sent[0]["view"])[0])

  def test_one_failing_operator_does_not_stop_others(self):
    self.user.fail = True

    async def scenario():
      log = GenerationLogService(10)
      service = self.make_service(log)
      add_success(log, 1)
      await service.shutdown()

    asyncio.run(scenario())
    self.assertEqual(len(sent_pairs(self.other)), 1)

  def test_does_nothing_without_operators(self):
    async def scenario():
      log = GenerationLogService(2)
      service = self.make_service(log, operators=None)
      add_success(log, 3)
      await service.shutdown()
      return log

    log = asyncio.run(scenario())
    self.assertEqual(self.user.sent, [])
    self.assertEqual(log.unsent_count, 2)


if __name__ == "__main__":
  unittest.main()
