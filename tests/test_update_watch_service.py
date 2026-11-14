"""更新の見える化（services/update_watch_service.py と StatusService の上書き表示）のテスト。"""

import asyncio
import json
import os
import unittest

import discord

from services.error_notification_service import ErrorNotificationService
from services.status_service import StatusService
from services.update_watch_service import (
  AUTO_UPDATE_FILE,
  LAST_UPDATE_FILE,
  NOTICE_FILE,
  UPDATING_FILE,
  UPDATING_TEXT,
  UpdateWatchService,
  last_update_embed,
)
from tests.support import DUMMY_USER_ID, RecordingNotifier, TempDirTestCase


class FakeUser:
  def __init__(self, error=None):
    self.sent = []
    self.error = error

  async def send(self, *, embed):
    if self.error is not None:
      raise self.error
    self.sent.append(embed)


class FakeClient:
  def __init__(self, user=None):
    self.user_obj = user or FakeUser()
    self.presences = []
    self.voice_clients = []
    self.guilds = []

  def get_user(self, user_id):
    return self.user_obj if user_id == DUMMY_USER_ID else None

  async def fetch_user(self, user_id):
    raise discord.NotFound(type("R", (), {"status": 404, "reason": "Not Found"})(), "unknown user")

  async def change_presence(self, *, status, activity):
    self.presences.append((status, activity.name))


class StatusOverrideTest(unittest.TestCase):
  def test_override_blocks_normal_updates_until_cleared(self):
    client = FakeClient()
    service = StatusService(client, "{guilds}個", RecordingNotifier())

    async def scenario():
      await service.update()
      await service.set_override(discord.Status.dnd, UPDATING_TEXT)
      await service.update()
      self.assertTrue(service.overridden)
      await service.clear_override()
      await service.clear_override()

    asyncio.run(scenario())
    self.assertEqual(client.presences, [
      (discord.Status.online, "0個"),
      (discord.Status.dnd, UPDATING_TEXT),
      (discord.Status.online, "0個"),
    ])


class WatchTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.client = FakeClient()
    self.status = StatusService(self.client, "{guilds}個", RecordingNotifier())
    self.alive = {1234}
    self.watch = self.make_watch()

  def make_watch(self, notifier=None, **options):
    options.setdefault("operator_user_id", DUMMY_USER_ID)
    return UpdateWatchService(
      self.client,
      self.tmp_dir,
      self.status,
      notifier or RecordingNotifier(),
      process_exists=lambda pid: pid in self.alive,
      **options,
    )

  def write(self, name, data):
    with open(os.path.join(self.tmp_dir, name), "w", encoding="utf-8") as file:
      json.dump(data, file)

  def remove(self, name):
    os.remove(os.path.join(self.tmp_dir, name))

  def notice(self):
    with open(os.path.join(self.tmp_dir, NOTICE_FILE), encoding="utf-8") as file:
      return json.load(file)

  def sent(self):
    return self.client.user_obj.sent


class EnabledTest(WatchTestCase):
  def test_disabled_without_state_dir(self):
    watch = UpdateWatchService(self.client, "", self.status, RecordingNotifier())
    self.assertFalse(watch.enabled)
    watch.start()
    self.assertIsNone(watch._task)


class UpdatingTest(WatchTestCase):
  def test_is_updating(self):
    self.assertFalse(self.watch.is_updating())
    self.write(UPDATING_FILE, {"pid": 1234})
    self.assertTrue(self.watch.is_updating())
    self.write(UPDATING_FILE, {"pid": 9999})
    self.assertFalse(self.watch.is_updating(), "書いたインストーラーがいなければ無視する")
    self.write(UPDATING_FILE, {})
    self.assertTrue(self.watch.is_updating())

  def test_status_follows_the_file(self):
    async def scenario():
      await self.watch.check_updating()
      self.write(UPDATING_FILE, {"pid": 1234})
      await self.watch.check_updating()
      await self.watch.check_updating()
      self.remove(UPDATING_FILE)
      await self.watch.check_updating()

    asyncio.run(scenario())
    self.assertEqual(self.client.presences, [
      (discord.Status.dnd, UPDATING_TEXT),
      (discord.Status.online, "0個"),
    ])
    self.assertFalse(self.status.overridden)


class LastUpdateTest(WatchTestCase):
  UPDATED = {"at": "2026-11-14T12:00:00+09:00", "result": "updated", "mode": "upgrade",
             "from": "v4.3.0.71 (beta 2)", "to": "v4.3.0.71 (beta 3)", "auto": True}

  def test_sends_once(self):
    self.write(LAST_UPDATE_FILE, self.UPDATED)
    asyncio.run(self.watch.notify_last_update())
    asyncio.run(self.watch.notify_last_update())
    self.assertEqual(len(self.sent()), 1)
    self.assertIn("v4.3.0.71 (beta 3) に更新しました（自動更新）", self.sent()[0].description)
    self.assertEqual(self.notice()["last_update_at"], self.UPDATED["at"])

  def test_new_result_is_sent_again(self):
    self.write(LAST_UPDATE_FILE, self.UPDATED)
    asyncio.run(self.watch.notify_last_update())
    self.write(LAST_UPDATE_FILE, {**self.UPDATED, "at": "2026-11-14T13:00:00+09:00"})
    asyncio.run(self.watch.notify_last_update())
    self.assertEqual(len(self.sent()), 2)

  def test_failed_dm_is_retried_next_time(self):
    self.client.user_obj.error = RuntimeError("DMを送れません")
    self.write(LAST_UPDATE_FILE, self.UPDATED)
    asyncio.run(self.watch.notify_last_update())
    self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, NOTICE_FILE)))
    self.client.user_obj.error = None
    asyncio.run(self.watch.notify_last_update())
    self.assertEqual(len(self.sent()), 1)

  def test_without_operator(self):
    watch = self.make_watch(operator_user_id=None)
    self.write(LAST_UPDATE_FILE, self.UPDATED)
    asyncio.run(watch.notify_last_update())
    self.assertEqual(self.sent(), [])

  def test_without_file(self):
    asyncio.run(self.watch.notify_last_update())
    self.assertEqual(self.sent(), [])


class EmbedTest(unittest.TestCase):
  def test_rolled_back(self):
    embed = last_update_embed({"result": "rolled_back", "mode": "upgrade", "from": "v1", "to": "v2",
                               "auto": True, "reason": "起動を確認できませんでした"})
    self.assertEqual(embed.title, "更新できませんでした")
    self.assertEqual(embed.color, discord.Color.red())
    self.assertIn("v2 に更新できなかったため、v1 に戻しました（自動更新）", embed.description)
    self.assertIn("自動更新では入れません", embed.description)
    self.assertIn("理由: 起動を確認できませんでした", embed.description)

  def test_repair_failed(self):
    embed = last_update_embed({"result": "rolled_back", "mode": "repair", "from": "v1", "to": "v1"})
    self.assertIn("入れ直せなかった", embed.description)
    self.assertNotIn("自動更新では入れません", embed.description)

  def test_manual_update_and_others(self):
    embed = last_update_embed({"result": "updated", "from": "v1", "to": "v2", "auto": False})
    self.assertEqual(embed.description, "v1 から v2 に更新しました")
    self.assertEqual(embed.color, discord.Color.green())
    self.assertIn("v1 を入れ直しました", last_update_embed({"result": "repaired", "to": "v1"}).description)
    rollback = last_update_embed({"result": "rollback", "from": "v2", "to": "v1"})
    self.assertEqual(rollback.title, "1つ前の版に戻しました")
    self.assertEqual(rollback.color, discord.Color.yellow())


class AutoUpdateAttentionTest(WatchTestCase):
  UNFINISHED = {"result": "unfinished", "message": "前回の更新が途中で止まっているため、更新しません"}

  def test_sends_once_while_the_state_lasts(self):
    self.write(AUTO_UPDATE_FILE, self.UNFINISHED)
    asyncio.run(self.watch.check_auto_update())
    asyncio.run(self.watch.check_auto_update())
    self.assertEqual(len(self.sent()), 1)
    self.assertEqual(self.sent()[0].title, "自動更新を止めています")
    self.assertIn("前回の更新が途中で止まっている", self.sent()[0].description)

  def test_sends_again_after_it_is_resolved(self):
    self.write(AUTO_UPDATE_FILE, self.UNFINISHED)
    asyncio.run(self.watch.check_auto_update())
    self.write(AUTO_UPDATE_FILE, {"result": "latest", "message": "新しい版はありません"})
    asyncio.run(self.watch.check_auto_update())
    self.assertIsNone(self.notice()["auto_update_attention"])
    self.write(AUTO_UPDATE_FILE, self.UNFINISHED)
    asyncio.run(self.watch.check_auto_update())
    self.assertEqual(len(self.sent()), 2)

  def test_ordinary_results_are_not_sent(self):
    for result in ("off", "stopped", "busy", "latest", "skipped", "updated", "failed", "error"):
      self.write(AUTO_UPDATE_FILE, {"result": result, "message": "x"})
      asyncio.run(self.watch.check_auto_update())
    self.assertEqual(self.sent(), [])


class LoopTest(WatchTestCase):
  def test_start_runs_and_close_stops(self):
    self.write(LAST_UPDATE_FILE, LastUpdateTest.UPDATED)
    self.write(UPDATING_FILE, {"pid": 1234})
    # RecordingNotifier は裏の処理を動かさないので、本物の（CLIに出すだけの）通知を使う
    watch = self.make_watch(ErrorNotificationService(), interval=0.01)

    async def scenario():
      watch.start()
      watch.start()
      await asyncio.sleep(0.05)
      await watch.close()

    asyncio.run(scenario())
    self.assertEqual(len(self.sent()), 1)
    self.assertIn((discord.Status.dnd, UPDATING_TEXT), self.client.presences)
    self.assertTrue(watch._task.done())


class ResultWrittenAfterStartTest(WatchTestCase):
  OLD_RESULT = {"at": "2026-11-14T10:00:00+09:00", "result": "updated", "mode": "upgrade",
                "from": "v4.3.0.71 (beta 1)", "to": "v4.3.0.71 (beta 2)", "auto": False}
  NEW_RESULT = {"at": "2026-11-14T15:50:00+09:00", "result": "updated", "mode": "upgrade",
                "from": "v4.3.0.71 (beta 2)", "to": "v4.3.0.71 (beta 3)", "auto": True}

  def test_result_written_after_the_bot_started_is_sent(self):
    # インストーラーは、新しいBotの起動を確かめてから last-update.json を書く。
    # 起動した時点では前の結果しかなく、新しい結果はあとから書かれる。
    self.write(LAST_UPDATE_FILE, self.OLD_RESULT)
    asyncio.run(self.watch.check())
    self.write(LAST_UPDATE_FILE, self.NEW_RESULT)
    asyncio.run(self.watch.check())
    asyncio.run(self.watch.check())
    self.assertEqual(len(self.sent()), 2)
    self.assertIn("v4.3.0.71 (beta 3) に更新しました（自動更新）", self.sent()[-1].description)
