"""自動更新（installer/autoupdate.py と upgrade --auto）のテスト。"""

import functools
import io
import json
import os
import unittest.mock
from dataclasses import replace
from pathlib import Path

from installer import autoupdate, cli, rollback, upgrade
from installer.instance import InstanceLock, InstanceRecord, load_record, save_record, with_skipped
from installer.release import ReleaseError
from installer.service import ServiceError, UNIT_MARKER
from installer.system import CommandResult, RecordingRunner, System
from installer.ui import UI
from tests.support import TempDirTestCase
from tests.test_installer_upgrade import NEW, OLD, UpgradeTestCase


def make_record(**changes):
  record = InstanceRecord("tsumugium", "tsumugium", "tsumugium", "/opt/tsumugium", version=OLD)
  return replace(record, **changes)


class RecordDefaultTest(TempDirTestCase):
  def test_default_depends_on_channel(self):
    self.assertTrue(make_record(channel="beta").auto_update_enabled)
    self.assertFalse(make_record(channel="stable").auto_update_enabled)
    self.assertFalse(make_record(channel="beta", auto_update=False).auto_update_enabled)
    self.assertTrue(make_record(channel="stable", auto_update=True).auto_update_enabled)

  def test_old_record_without_new_fields_loads(self):
    system = System(Path(self.tmp_dir), RecordingRunner(), euid=0)
    path = system.path("/etc/tsumugium/tsumugium.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
      "instance": "tsumugium", "user": "tsumugium", "service": "tsumugium",
      "prefix": "/opt/tsumugium", "channel": "beta", "version": OLD, "format": 1,
    }), encoding="utf-8")
    record = load_record(system, "tsumugium")
    self.assertEqual((record.auto_update, record.skip_versions), (None, []))
    self.assertTrue(record.auto_update_enabled)

  def test_with_skipped_keeps_last_ten_without_duplicates(self):
    record = make_record()
    for number in range(12):
      record = with_skipped(record, f"v4.3.0.{number}")
    record = with_skipped(record, "v4.3.0.5")
    self.assertEqual(len(record.skip_versions), 10)
    self.assertEqual(record.skip_versions[-1], "v4.3.0.5")
    self.assertEqual(record.skip_versions.count("v4.3.0.5"), 1)


class UnitTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.system = System(Path(self.tmp_dir), self.runner, euid=0)

  def unit(self, name):
    return self.system.path(f"/etc/systemd/system/{name}")

  def test_service_contents(self):
    text = autoupdate.render_update_service(make_record())
    self.assertTrue(text.startswith(UNIT_MARKER + "\n"))
    for expected in (
        "ConditionPathExists=/opt/tsumugium/app/installer/autoupdate.py",
        "Type=oneshot",
        "ExecStart=/usr/local/sbin/tsumugium-setup upgrade --auto --instance tsumugium",
        "TimeoutStartSec=30min",
    ):
      self.assertIn(expected, text.splitlines())

  def test_timer_contents(self):
    text = autoupdate.render_update_timer(make_record())
    for expected in (
        "OnBootSec=5min",
        "OnUnitActiveSec=10min",
        "RandomizedDelaySec=1min",
        "Unit=tsumugium-update.service",
        "WantedBy=timers.target",
    ):
      self.assertIn(expected, text.splitlines())

  def test_apply_enables_timer(self):
    autoupdate.apply_auto_update(self.system, make_record(auto_update=True))
    self.assertTrue(self.unit("tsumugium-update.service").is_file())
    self.assertTrue(self.unit("tsumugium-update.timer").is_file())
    self.assertEqual(self.runner.calls, [
      ["systemctl", "daemon-reload"],
      ["systemctl", "enable", "--now", "tsumugium-update.timer"],
    ])

  def test_apply_disables_timer_and_skips_reload_when_unchanged(self):
    autoupdate.apply_auto_update(self.system, make_record(auto_update=True))
    self.runner.calls.clear()
    autoupdate.apply_auto_update(self.system, make_record(auto_update=False))
    self.assertEqual(self.runner.calls, [["systemctl", "disable", "--now", "tsumugium-update.timer"]])

  def test_foreign_unit_is_not_overwritten(self):
    path = self.unit("tsumugium-update.timer")
    path.parent.mkdir(parents=True)
    path.write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
    with self.assertRaisesRegex(ServiceError, "上書きしません"):
      autoupdate.apply_auto_update(self.system, make_record(auto_update=True))
    self.assertEqual(path.read_text(encoding="utf-8"), "[Timer]\nOnCalendar=daily\n")

  def test_remove(self):
    self.assertFalse(autoupdate.remove_auto_update(self.system, "tsumugium"))
    autoupdate.apply_auto_update(self.system, make_record(auto_update=True))
    self.runner.calls.clear()
    self.assertTrue(autoupdate.remove_auto_update(self.system, "tsumugium"))
    self.assertFalse(self.unit("tsumugium-update.timer").exists())
    self.assertFalse(self.unit("tsumugium-update.service").exists())
    self.assertEqual(self.runner.calls, [
      ["systemctl", "disable", "--now", "tsumugium-update.timer"],
      ["systemctl", "daemon-reload"],
    ])


class ModeTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.system = System(Path(self.tmp_dir), self.runner, euid=0)
    save_record(self.system, make_record(channel="stable"))

  def run_mode(self, state):
    self.ui = UI(assume_yes=True, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    return cli.main(["auto-update", state], system=self.system, ui=self.ui)

  def test_on_and_off(self):
    self.assertEqual(self.run_mode("on"), cli.EXIT_OK)
    self.assertTrue(load_record(self.system, "tsumugium").auto_update)
    self.assertIn(["systemctl", "enable", "--now", "tsumugium-update.timer"], self.runner.calls)
    self.assertIn("有効にしました", self.ui.stdout.getvalue())
    self.assertEqual(self.run_mode("off"), cli.EXIT_OK)
    self.assertFalse(load_record(self.system, "tsumugium").auto_update)
    self.assertEqual(self.runner.calls[-1], ["systemctl", "disable", "--now", "tsumugium-update.timer"])

  def test_state_is_required(self):
    self.ui = UI(assume_yes=True, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    with unittest.mock.patch("sys.stderr", io.StringIO()):
      self.assertEqual(cli.main(["auto-update"], system=self.system, ui=self.ui), cli.EXIT_USAGE)


class AutoUpgradeTestCase(UpgradeTestCase):
  def setUp(self):
    super().setUp()
    self.record = replace(self.record, auto_update=True)
    save_record(self.system, self.record)

  def auto(self):
    return self.upgrade(["--auto", "--instance", "tsumugium"])

  def state(self, name=autoupdate.STATE_FILE):
    return json.loads(self.read(f"/opt/tsumugium/data/state/{name}"))

  def assert_not_upgraded(self, result):
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "old\n")
    self.assertEqual(load_record(self.system, "tsumugium").version, OLD)
    self.assertEqual(self.state()["result"], result)


class AutoUpgradeTest(AutoUpgradeTestCase):
  def test_upgrades_to_newer_version(self):
    self.assertEqual(self.auto(), cli.EXIT_OK, self.output())
    self.assertEqual(load_record(self.system, "tsumugium").version, NEW)
    self.assertTrue(self.handoff_data["auto"])
    self.assertTrue(self.handoff_data["assume_yes"])
    state = self.state()
    self.assertEqual((state["result"], state["latest"], state["version"]), (autoupdate.RESULT_UPDATED, NEW, NEW))
    last = self.state(autoupdate.LAST_UPDATE_FILE)
    self.assertEqual(
      (last["result"], last["from"], last["to"], last["auto"], last["started"]),
      ("updated", OLD, NEW, True, True),
    )
    self.assertIn(["systemctl", "enable", "--now", "tsumugium-update.timer"], self.runner.calls)

  def test_off(self):
    save_record(self.system, replace(self.record, auto_update=False))
    self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assert_not_upgraded(autoupdate.RESULT_OFF)
    self.assertEqual(self.github.requests, [])

  def test_stable_channel_default_is_off(self):
    save_record(self.system, replace(self.record, auto_update=None))
    self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assert_not_upgraded(autoupdate.RESULT_OFF)

  def test_stopped_bot(self):
    self.runner.responses[("systemctl", "is-active")] = CommandResult(3)
    self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assert_not_upgraded(autoupdate.RESULT_STOPPED)

  def test_unfinished_update(self):
    save_record(self.system, replace(self.record, updating={"from": OLD, "to": NEW}))
    self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assertEqual(self.state()["result"], autoupdate.RESULT_UNFINISHED)
    self.assertIn("repair", self.output())

  def test_latest(self):
    save_record(self.system, replace(self.record, version=NEW))
    self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assertEqual(self.state()["result"], autoupdate.RESULT_LATEST)
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "old\n")
    self.assertFalse(self.exists("/opt/tsumugium/data/state/last-update.json"))
    self.assertEqual(self.waits, [])

  def test_skipped_version(self):
    save_record(self.system, with_skipped(self.record, NEW))
    self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assert_not_upgraded(autoupdate.RESULT_SKIPPED)
    self.assertEqual(self.state()["latest"], NEW)

  def test_busy(self):
    with InstanceLock(self.system, "tsumugium"):
      self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assert_not_upgraded(autoupdate.RESULT_BUSY)

  def test_cannot_check_releases(self):
    with unittest.mock.patch("installer.upgrade.select_release", side_effect=ReleaseError("つながりません")):
      self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assert_not_upgraded(autoupdate.RESULT_ERROR)
    self.assertIn("つながりません", self.state()["message"])


class AutoUpgradeFailureTest(AutoUpgradeTestCase):
  def test_failed_version_is_skipped_next_time(self):
    self.fail_versions = {NEW}
    self.assertEqual(self.auto(), cli.EXIT_ERROR)
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.skip_versions), (OLD, [NEW]))
    self.assertEqual(self.state()["result"], autoupdate.RESULT_FAILED)
    last = self.state(autoupdate.LAST_UPDATE_FILE)
    self.assertEqual((last["result"], last["to"], last["auto"]), ("rolled_back", NEW, True))
    self.assertIn("起動を確認できませんでした", last["reason"])

    self.waits.clear()
    self.assertEqual(self.auto(), cli.EXIT_OK)
    self.assert_not_upgraded(autoupdate.RESULT_SKIPPED)
    self.assertEqual(self.waits, [])

  def test_manual_upgrade_ignores_skip_and_clears_it(self):
    save_record(self.system, with_skipped(self.record, NEW))
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_OK, self.output())
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.skip_versions), (NEW, []))
    self.assertFalse(self.state(autoupdate.LAST_UPDATE_FILE)["auto"])


class RollbackSkipTest(UpgradeTestCase):
  def setUp(self):
    super().setUp()
    self.write("/opt/tsumugium/app/build-info.json", json.dumps({"version": OLD}))
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_OK)
    self.runner.calls.clear()

  def test_rollback_skips_the_version_left(self):
    self.ui = UI(assume_yes=True, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    handler = functools.partial(rollback.run_rollback, wait=self.fake_wait)
    with unittest.mock.patch.dict(cli.HANDLERS, {"rollback": handler}):
      self.assertEqual(cli.main(["rollback", "--yes"], system=self.system, ui=self.ui), cli.EXIT_OK)
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.skip_versions), (OLD, [NEW]))
    last = json.loads(self.read("/opt/tsumugium/data/state/last-update.json"))
    self.assertEqual((last["result"], last["from"], last["to"]), ("rollback", NEW, OLD))


class StatusTest(AutoUpgradeTestCase):
  def test_status_shows_last_check_and_skipped_versions(self):
    save_record(self.system, with_skipped(self.record, NEW))
    self.auto()
    self.ui = UI(assume_yes=True, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    self.assertEqual(cli.main(["status"], system=self.system, ui=self.ui), cli.EXIT_OK)
    output = self.ui.stdout.getvalue()
    self.assertIn("自動更新    オン（10分おき）", output)
    self.assertIn("新しい版は飛ばす版だったので更新しなかった", output)
    self.assertIn(f"飛ばす版    {NEW}", output)


class UninstallTest(AutoUpgradeTestCase):
  def test_uninstall_removes_timer(self):
    self.auto()
    self.assertTrue(self.exists("/etc/systemd/system/tsumugium-update.timer"))
    self.ui = UI(assume_yes=True, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    self.assertEqual(cli.main(["uninstall", "--yes"], system=self.system, ui=self.ui), cli.EXIT_OK)
    self.assertFalse(self.exists("/etc/systemd/system/tsumugium-update.timer"))
    self.assertFalse(self.exists("/etc/systemd/system/tsumugium-update.service"))
    self.assertIn("自動更新の timer を消しました", self.ui.stdout.getvalue())


class UpdatingMarkTest(UpgradeTestCase):
  def test_mark_exists_only_while_updating(self):
    seen = {}
    original = self.launch

    def launch(system, ui, app_new, handoff_file):
      path = system.path("/opt/tsumugium/data/state/updating.json")
      seen["before"] = json.loads(path.read_text(encoding="utf-8"))
      self.on_new_start = lambda: seen.setdefault("at_start", path.exists())
      return original(system, ui, app_new, handoff_file)

    self.launch = launch
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_OK, self.output())
    mark = seen["before"]
    self.assertEqual((mark["from"], mark["to"], mark["mode"]), (OLD, NEW, "upgrade"))
    self.assertEqual(mark["pid"], os.getpid())
    self.assertFalse(seen["at_start"], "新しいBotを起動する前に消す")
    self.assertFalse(self.exists("/opt/tsumugium/data/state/updating.json"))

  def test_mark_is_removed_when_update_fails(self):
    self.fail_versions = {NEW}
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertFalse(self.exists("/opt/tsumugium/data/state/updating.json"))


class RestartReasonTest(UpgradeTestCase):
  REASON = "/opt/tsumugium/data/state/restart-reason.json"

  def test_update_writes_reason_before_stopping(self):
    seen = {}
    original_run = self.runner.run

    def run(args, **kwargs):
      if list(args[:2]) == ["systemctl", "stop"]:
        seen["reason"] = json.loads(self.read(self.REASON))["reason"]
      return original_run(args, **kwargs)

    self.runner.run = run
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_OK, self.output())
    self.assertEqual(seen["reason"], "update")

  def test_manual_stop_writes_reason(self):
    self.ui = UI(assume_yes=True, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    self.assertEqual(cli.main(["stop"], system=self.system, ui=self.ui), cli.EXIT_OK)
    self.assertEqual(json.loads(self.read(self.REASON))["reason"], "manual_stop")
