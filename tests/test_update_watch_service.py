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
  last_update_layout,
)
from tests.support import DUMMY_OTHER_USER_ID, DUMMY_USER_ID, RecordingNotifier, TempDirTestCase


class Shown:
  """Components v2 のメッセージ（LayoutView）から、見出し・本文・枠の色・ボタンを取り出す。"""

  def __init__(self, view):
    self.view = view
    [container] = [item for item in view.children if isinstance(item, discord.ui.Container)]
    heading, _, self.description = container.children[0].content.partition("\n")
    self.title = heading.removeprefix("## ")
    self.color = container.accent_colour
    # 「更新する」ボタンは、枠の外（下）の行に置く
    self.buttons = [
      (item.item.label, item.item.custom_id)
      for row in view.children
      if isinstance(row, discord.ui.ActionRow)
      for item in row.children
    ]


class FakeUser:
  def __init__(self, error=None):
    self.sent = []
    self.error = error

  async def send(self, *, view):
    if self.error is not None:
      raise self.error
    self.sent.append(Shown(view))


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
    options.setdefault("operator_user_ids", (DUMMY_USER_ID,))
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
    watch = self.make_watch(operator_user_ids=None)
    self.write(LAST_UPDATE_FILE, self.UPDATED)
    asyncio.run(watch.notify_last_update())
    self.assertEqual(self.sent(), [])

  def test_without_file(self):
    asyncio.run(self.watch.notify_last_update())
    self.assertEqual(self.sent(), [])


def last_update_shown(data):
  return Shown(last_update_layout(data).view)


class LastUpdateLayoutTest(unittest.TestCase):
  def test_rolled_back(self):
    embed = last_update_shown({"result": "rolled_back", "mode": "upgrade", "from": "v1", "to": "v2",
                               "auto": True, "reason": "起動を確認できませんでした"})
    self.assertEqual(embed.title, "更新できませんでした")
    self.assertEqual(embed.color, discord.Color.red())
    self.assertIn("v2 に更新できなかったため、v1 に戻しました（自動更新）", embed.description)
    self.assertIn("自動更新では入れません", embed.description)
    self.assertIn("理由: 起動を確認できませんでした", embed.description)

  def test_repair_failed(self):
    embed = last_update_shown({"result": "rolled_back", "mode": "repair", "from": "v1", "to": "v1"})
    self.assertIn("入れ直せなかった", embed.description)
    self.assertNotIn("自動更新では入れません", embed.description)

  def test_manual_update_and_others(self):
    embed = last_update_shown({"result": "updated", "from": "v1", "to": "v2", "auto": False})
    self.assertEqual(embed.description, "v1 から v2 に更新しました")
    self.assertEqual(embed.color, discord.Color.green())
    self.assertIn("v1 を入れ直しました", last_update_shown({"result": "repaired", "to": "v1"}).description)
    rollback = last_update_shown({"result": "rollback", "from": "v2", "to": "v1"})
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
    self.write(AUTO_UPDATE_FILE, {"result": "latest", "message": "更新はありません"})
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


class StatusActivityTypeTest(unittest.TestCase):
  def test_normal_and_updating_status_use_the_same_kind(self):
    # ふだんの表示も更新中の表示も、アイコンの横の吹き出し（カスタムステータス）に出す
    activities = []

    class Client(FakeClient):
      async def change_presence(self, *, status, activity):
        activities.append(activity)

    service = StatusService(Client(), "{voice_connections}/{guilds}個のVCに接続中", RecordingNotifier())

    async def scenario():
      await service.update()
      await service.set_override(discord.Status.dnd, UPDATING_TEXT)
      await service.clear_override()

    asyncio.run(scenario())
    self.assertEqual(len(activities), 3)
    for activity in activities:
      self.assertIsInstance(activity, discord.CustomActivity)
      self.assertEqual(activity.type, discord.ActivityType.custom)
    self.assertEqual(activities[0].name, "0/0個のVCに接続中")


from services.update_watch_service import (  # noqa: E402
  AVAILABLE_FILE,
  PENDING_FILE,
  REQUEST_FILE,
  UpdateButton,
)


NEW_VERSION = "v4.3.0.71 (beta 8)"
CURRENT_VERSION = "v4.3.0.71 (beta 7)"


class FakePartialMessage:
  def __init__(self, channel, message_id):
    self.channel = channel
    self.id = message_id

  async def edit(self, *, embed, view):
    if self.channel.error is not None:
      raise self.channel.error
    # Embedで送った前の版のお知らせも、v2へ書き換えられるよう embed を外す
    assert embed is None
    self.channel.edits.append((self.id, Shown(view)))


class FakeDMChannel:
  def __init__(self):
    self.edits = []
    self.error = None

  def get_partial_message(self, message_id):
    return FakePartialMessage(self, message_id)


class ViewUser(FakeUser):
  def __init__(self):
    super().__init__()
    self.views = []
    self.dm_channel = FakeDMChannel()

  async def send(self, *, view):
    self.sent.append(Shown(view))
    self.views.append(view)


class FakeResponse:
  def __init__(self):
    self.edits = []
    self.messages = []

  async def edit_message(self, *, embed, view):
    assert embed is None
    self.edits.append(Shown(view))

  async def send_message(self, text, *, ephemeral):
    self.messages.append((text, ephemeral))


class FakeInteraction:
  def __init__(self, user_id, client=None):
    self.user = type("U", (), {"id": user_id})()
    self.client = client
    self.response = FakeResponse()
    self.message = type("M", (), {"id": 222222222222222222})()


class FakeVoiceClient:
  def __init__(self, connected=True):
    self.connected = connected

  def is_connected(self):
    return self.connected


class UpdateButtonTestCase(WatchTestCase):
  def setUp(self):
    super().setUp()
    self.client.user_obj = ViewUser()
    self.watch = self.make_watch(current_version=CURRENT_VERSION)

  def offer(self, **extra):
    self.write(AVAILABLE_FILE, {"version": NEW_VERSION, "skipped": False, **extra})

  def exists(self, name):
    return os.path.exists(os.path.join(self.tmp_dir, name))

  def read(self, name):
    with open(os.path.join(self.tmp_dir, name), encoding="utf-8") as file:
      return json.load(file)


class OfferTest(UpdateButtonTestCase):
  def test_offers_once_with_two_buttons(self):
    self.offer()

    async def scenario():
      await self.watch.offer_available_update()
      await self.watch.offer_available_update()

    asyncio.run(scenario())
    self.assertEqual(len(self.sent()), 1)
    buttons = self.sent()[0].buttons
    self.assertIn(f"新しい版 {NEW_VERSION} があります", self.sent()[0].description)
    self.assertEqual(buttons, [
      ("今すぐ更新する", f"tsumugium:update:now:{NEW_VERSION}"),
      ("VCが空いたら更新する", f"tsumugium:update:idle:{NEW_VERSION}"),
    ])

  def test_skipped_version_is_mentioned(self):
    self.offer(skipped=True)
    asyncio.run(self.watch.offer_available_update())
    self.assertIn("前に起動を確かめられなかった", self.sent()[0].description)

  def test_nothing_when_no_new_version(self):
    asyncio.run(self.watch.offer_available_update())
    self.write(AVAILABLE_FILE, {"version": CURRENT_VERSION})
    asyncio.run(self.watch.offer_available_update())
    self.assertEqual(self.sent(), [])

  def test_newer_version_is_offered_again(self):
    self.offer()
    asyncio.run(self.watch.offer_available_update())
    self.write(AVAILABLE_FILE, {"version": "v4.3.0.71 (beta 9)"})
    asyncio.run(self.watch.offer_available_update())
    self.assertEqual(len(self.sent()), 2)


class ButtonTest(UpdateButtonTestCase):
  def press(self, mode, version=NEW_VERSION, user_id=DUMMY_USER_ID):
    interaction = FakeInteraction(user_id)

    async def scenario():
      await self.watch.handle_button(interaction, mode, version)

    asyncio.run(scenario())
    return interaction.response

  def test_now_writes_request(self):
    self.offer()
    response = self.press("now")
    request = self.read(REQUEST_FILE)
    self.assertEqual((request["version"], request["mode"], request["requested_by"]), (NEW_VERSION, "now", DUMMY_USER_ID))
    shown = response.edits[0]
    self.assertEqual(shown.title, "更新を受け付けました")
    self.assertEqual(shown.buttons, [])

  def test_idle_waits_until_no_voice_connection(self):
    self.offer()
    response = asyncio.run(self._press_idle())
    self.assertFalse(self.exists(REQUEST_FILE))
    self.assertEqual(self.read(PENDING_FILE)["version"], NEW_VERSION)
    self.assertEqual(response.edits[0].title, "VCが空いたら更新します")

    self.client.voice_clients = [FakeVoiceClient()]
    self.watch.check_pending_update()
    self.assertFalse(self.exists(REQUEST_FILE), "VCにつないでいるあいだは待つ")
    self.client.voice_clients = [FakeVoiceClient(connected=False)]
    self.watch.check_pending_update()
    self.assertEqual(self.read(REQUEST_FILE)["mode"], "idle")
    self.assertFalse(self.exists(PENDING_FILE))

  async def _press_idle(self):
    interaction = FakeInteraction(DUMMY_USER_ID)
    await self.watch.handle_button(interaction, "idle", NEW_VERSION)
    self.assertEqual(
      interaction.response.edits[0].buttons,
      [("今すぐ更新する", f"tsumugium:update:now:{NEW_VERSION}")],
    )
    return interaction.response

  def test_now_after_idle_cancels_waiting(self):
    self.offer()
    asyncio.run(self._press_idle())
    self.press("now")
    self.assertFalse(self.exists(PENDING_FILE))
    self.assertTrue(self.exists(REQUEST_FILE))

  def test_only_operator_can_press(self):
    self.offer()
    response = self.press("now", user_id=DUMMY_USER_ID + 1)
    self.assertFalse(self.exists(REQUEST_FILE))
    self.assertIn("運営している人だけ", response.messages[0][0])
    self.assertTrue(response.messages[0][1])

  def test_already_updated(self):
    response = self.press("now", version=CURRENT_VERSION)
    self.assertEqual(response.edits[0].description, f"すでに {CURRENT_VERSION} です")
    self.assertFalse(self.exists(REQUEST_FILE))

  def test_old_notice_points_to_newer_version(self):
    self.write(AVAILABLE_FILE, {"version": "v4.3.0.71 (beta 9)"})
    response = self.press("now")
    self.assertIn("新しい版 v4.3.0.71 (beta 9) があります", response.messages[0][0])
    self.assertFalse(self.exists(REQUEST_FILE))

  def test_pending_for_installed_version_is_dropped(self):
    self.write(PENDING_FILE, {"version": CURRENT_VERSION})
    self.watch.check_pending_update()
    self.assertFalse(self.exists(PENDING_FILE))
    self.assertFalse(self.exists(REQUEST_FILE))


class CheckRequestTest(UpdateButtonTestCase):
  """/update コマンドの確認の依頼。"""

  def setUp(self):
    super().setUp()
    self.users = {DUMMY_USER_ID: MessageUser(900), DUMMY_OTHER_USER_ID: MessageUser(901)}
    self.client.get_user = self.users.get
    self.now = [1000.0]
    self.watch = self.make_watch(
      operator_user_ids=(DUMMY_USER_ID, DUMMY_OTHER_USER_ID),
      current_version=CURRENT_VERSION,
      check_timeout=1.0,
      check_poll_interval=0.01,
      clock=lambda: self.now[0],
    )

  def run_check(self, installer=None):
    """installer(request) が、依頼を読んだインストーラーとして結果を書く。"""

    async def fake_installer():
      while True:
        request = self.read(REQUEST_FILE) if self.exists(REQUEST_FILE) else {}
        if request.get("requested_by") == DUMMY_USER_ID:
          break
        await asyncio.sleep(0.005)
      self.remove(REQUEST_FILE)
      installer(request)

    async def scenario():
      task = asyncio.create_task(fake_installer()) if installer else None
      layout = await self.watch.request_check(DUMMY_USER_ID)
      if task is not None:
        await task
      return Shown(layout.view)

    return asyncio.run(scenario())

  def answer(self, result, **extra):
    def installer(request):
      self.assertEqual((request["mode"], request["requested_by"]), ("check", DUMMY_USER_ID))
      if result == "notified":
        self.offer()
      self.write(AUTO_UPDATE_FILE, {"result": result, "request_id": request["request_id"], **extra})
    return installer

  def test_new_version_is_offered_to_every_operator(self):
    embed = self.run_check(self.answer("notified", latest=NEW_VERSION))
    self.assertEqual(embed.title, "新しい版の確認")
    self.assertEqual(embed.description, f"新しいバージョン {NEW_VERSION} があります")
    self.assertEqual([len(user.sent) for user in self.users.values()], [1, 1])
    self.assertEqual(self.notice()["offered_version"], NEW_VERSION)
    self.assertEqual([button for button, _ in self.users[DUMMY_USER_ID].sent[0].buttons], ["今すぐ更新する", "VCが空いたら更新する"])

  def test_already_offered_version_is_sent_again_to_requester(self):
    self.offer()
    asyncio.run(self.watch.offer_available_update())
    embed = self.run_check(self.answer("notified", latest=NEW_VERSION))
    self.assertEqual(embed.description, f"新しいバージョン {NEW_VERSION} があります")
    self.assertEqual([len(user.sent) for user in self.users.values()], [2, 1])
    self.assertEqual(
      [record["message_id"] for record in self.notice()["offered_messages"]],
      [900, 901, 900],
    )
    # 見張りからは、もう送らない
    asyncio.run(self.watch.offer_available_update())
    self.assertEqual([len(user.sent) for user in self.users.values()], [2, 1])

  def test_latest(self):
    embed = self.run_check(self.answer("latest"))
    self.assertEqual(embed.description, f"更新はありません（今のバージョン {CURRENT_VERSION}）")
    self.assertEqual([len(user.sent) for user in self.users.values()], [0, 0])

  def test_installer_message_is_shown_on_error(self):
    embed = self.run_check(self.answer("error", message="更新を確かめられませんでした: つながりません"))
    self.assertIn("つながりません", embed.description)

  def test_result_of_other_request_is_ignored(self):
    self.write(AUTO_UPDATE_FILE, {"result": "latest", "request_id": "old"})
    embed = self.run_check()
    self.assertIn("時間内に応答がありませんでした", embed.description)
    self.assertEqual(self.read(REQUEST_FILE)["mode"], "check")

  def test_stale_check_request_is_replaced(self):
    self.write(REQUEST_FILE, {"mode": "check", "request_id": "old", "requested_at": 0, "requested_by": None})
    embed = self.run_check(self.answer("latest"))
    self.assertIn("更新はありません", embed.description)

  def test_update_request_is_not_replaced(self):
    self.write(REQUEST_FILE, {"version": NEW_VERSION, "mode": "now"})
    embed = self.run_check()
    self.assertIn("ほかで更新を問い合わせています", embed.description)
    self.assertEqual(self.read(REQUEST_FILE)["mode"], "now")

  def test_cooldown(self):
    self.run_check(self.answer("latest"))
    self.now[0] += 10
    embed = self.run_check()
    self.assertIn("空けてから", embed.description)
    self.assertFalse(self.exists(REQUEST_FILE))
    self.now[0] += 60
    embed = self.run_check(self.answer("latest"))
    self.assertIn("更新はありません", embed.description)

  def test_refused_while_updating(self):
    self.write(UPDATING_FILE, {"pid": 1234})
    embed = self.run_check()
    self.assertIn("更新中です", embed.description)
    self.assertFalse(self.exists(REQUEST_FILE))

  def test_disabled_without_installer(self):
    watch = UpdateWatchService(self.client, "", self.status, RecordingNotifier())
    embed = Shown(asyncio.run(watch.request_check(DUMMY_USER_ID)).view)
    self.assertIn("手動で導入されているため", embed.description)


class DynamicItemTest(unittest.TestCase):
  def test_custom_id_round_trip(self):
    async def scenario():
      match = UpdateButton.__discord_ui_compiled_template__.fullmatch(f"tsumugium:update:idle:{NEW_VERSION}")
      return await UpdateButton.from_custom_id(None, None, match)

    button = asyncio.run(scenario())
    self.assertEqual((button.mode, button.version), ("idle", NEW_VERSION))
    self.assertLessEqual(len(button.item.custom_id), 100)

  def test_callback_delegates_to_service(self):
    calls = []

    class Watch:
      enabled = True

      async def handle_button(self, interaction, mode, version):
        calls.append((mode, version))

    async def scenario():
      client = type("C", (), {"update_watch": Watch()})()
      await UpdateButton("now", NEW_VERSION).callback(FakeInteraction(DUMMY_USER_ID, client))

    asyncio.run(scenario())
    self.assertEqual(calls, [("now", NEW_VERSION)])


class StartedTest(UpdateButtonTestCase):
  press = ButtonTest.press
  _press_idle = ButtonTest._press_idle

  def edits(self):
    return self.client.user_obj.dm_channel.edits

  def start_updating(self):
    self.write(UPDATING_FILE, {"pid": 4242, "to": NEW_VERSION})
    self.alive.add(4242)

  def test_now_message_is_rewritten_when_update_starts(self):
    self.offer()
    self.press("now")

    async def scenario():
      await self.watch.check_updating()
      await self.watch.mark_update_started()
      self.assertEqual(self.edits(), [], "更新が始まるまでは書き換えない")
      self.start_updating()
      await self.watch.check_updating()
      await self.watch.mark_update_started()
      await self.watch.mark_update_started()

    asyncio.run(scenario())
    self.assertEqual(len(self.edits()), 1)
    message_id, shown = self.edits()[0]
    self.assertEqual(message_id, 222222222222222222)
    self.assertEqual(shown.title, "更新を開始しました")
    self.assertIn(NEW_VERSION, shown.description)
    self.assertEqual(shown.buttons, [])

  def test_idle_message_is_rewritten_when_update_starts(self):
    self.offer()
    asyncio.run(self._press_idle())
    self.watch.check_pending_update()
    self.start_updating()

    async def scenario():
      await self.watch.check_updating()
      await self.watch.mark_update_started()

    asyncio.run(scenario())
    self.assertEqual(self.edits()[0][1].title, "更新を開始しました")

  def test_rewritten_after_restart_when_update_was_missed(self):
    self.offer()
    self.press("now")
    restarted = self.make_watch(current_version=NEW_VERSION)
    asyncio.run(restarted.mark_update_started())
    self.assertEqual(self.edits()[0][1].title, "更新を開始しました")

  def test_retried_when_edit_fails(self):
    self.offer()
    self.press("now")
    self.start_updating()
    self.client.user_obj.dm_channel.error = RuntimeError("temporary")

    async def scenario():
      await self.watch.check_updating()
      await self.watch.mark_update_started()
      self.client.user_obj.dm_channel.error = None
      await self.watch.mark_update_started()

    asyncio.run(scenario())
    self.assertEqual(len(self.edits()), 1)


class MultiOperatorTest(WatchTestCase):
  """運営者を複数人にしたとき。"""

  def setUp(self):
    super().setUp()
    self.users = {DUMMY_USER_ID: MessageUser(900), DUMMY_OTHER_USER_ID: MessageUser(901)}
    self.client.get_user = self.users.get
    self.watch = self.make_watch(
      operator_user_ids=(DUMMY_USER_ID, DUMMY_OTHER_USER_ID),
      current_version=CURRENT_VERSION,
    )

  def test_every_operator_gets_update_result(self):
    self.write(LAST_UPDATE_FILE, {"at": 1, "result": "success", "to": NEW_VERSION})
    asyncio.run(self.watch.notify_last_update())
    asyncio.run(self.watch.notify_last_update())
    self.assertEqual([len(user.sent) for user in self.users.values()], [1, 1])

  def test_one_failure_does_not_stop_others(self):
    self.users[DUMMY_USER_ID].error = RuntimeError("DMを送れません")
    self.write(LAST_UPDATE_FILE, {"at": 1, "result": "success", "to": NEW_VERSION})
    asyncio.run(self.watch.notify_last_update())
    self.assertEqual(len(self.users[DUMMY_OTHER_USER_ID].sent), 1)
    self.assertEqual(self.notice()["last_update_at"], 1)

  def test_any_operator_can_press_and_all_offers_are_rewritten(self):
    self.write(AVAILABLE_FILE, {"version": NEW_VERSION})
    asyncio.run(self.watch.offer_available_update())
    self.assertEqual([len(user.sent) for user in self.users.values()], [1, 1])

    interaction = FakeInteraction(DUMMY_OTHER_USER_ID)
    interaction.message = type("M", (), {"id": 901})()
    asyncio.run(self.watch.handle_button(interaction, "now", NEW_VERSION))
    self.assertEqual(interaction.response.edits[0].title, "更新を受け付けました")
    with open(os.path.join(self.tmp_dir, REQUEST_FILE), encoding="utf-8") as file:
      self.assertEqual(json.load(file)["requested_by"], DUMMY_OTHER_USER_ID)

    restarted = self.make_watch(
      operator_user_ids=(DUMMY_USER_ID, DUMMY_OTHER_USER_ID),
      current_version=NEW_VERSION,
    )
    asyncio.run(restarted.mark_update_started())
    for user_id, message_id in ((DUMMY_USER_ID, 900), (DUMMY_OTHER_USER_ID, 901)):
      [(edited_id, shown)] = self.users[user_id].dm_channel.edits
      self.assertEqual((edited_id, shown.title, shown.buttons), (message_id, "更新を開始しました", []))
    self.assertIsNone(self.notice()["button_message"])

  def test_old_single_operator_record_is_rewritten(self):
    self.write(NOTICE_FILE, {"button_message": {"message_id": 900, "version": NEW_VERSION}})
    restarted = self.make_watch(
      operator_user_ids=(DUMMY_USER_ID, DUMMY_OTHER_USER_ID),
      current_version=NEW_VERSION,
    )
    asyncio.run(restarted.mark_update_started())
    self.assertEqual(self.users[DUMMY_USER_ID].dm_channel.edits[0][0], 900)
    self.assertEqual(self.users[DUMMY_OTHER_USER_ID].dm_channel.edits, [])

  def test_others_still_refused(self):
    self.write(AVAILABLE_FILE, {"version": NEW_VERSION})
    interaction = FakeInteraction(DUMMY_USER_ID + 99)
    asyncio.run(self.watch.handle_button(interaction, "now", NEW_VERSION))
    self.assertIn("運営している人だけ", interaction.response.messages[0][0])


class MessageUser(ViewUser):
  """送ったメッセージを返す（実際の discord.User.send と同じ）。"""

  def __init__(self, message_id):
    super().__init__()
    self.message_id = message_id
    self.error = None

  async def send(self, *, view):
    if self.error is not None:
      raise self.error
    await super().send(view=view)
    return type("M", (), {"id": self.message_id})()
