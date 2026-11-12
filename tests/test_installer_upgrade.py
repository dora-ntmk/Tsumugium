"""upgrade モード（installer/upgrade.py、installer/handoff.py）のテスト。

偽のGitHub（127.0.0.1）から新しい版を取り、引き継ぎ先も同じプロセスの中で動かす。
"""

import functools
import io
import json
import os
import unittest
import unittest.mock
from pathlib import Path

from installer import cli, handoff, upgrade
from installer.checks import CheckError
from installer.instance import InstanceRecord, load_record, save_record
from installer.readiness import ReadyError
from installer.service import unit_path, write_unit
from installer.system import CommandResult, RecordingRunner, System
from installer.ui import UI
from tests.support import TempDirTestCase
from tests.test_installer_release import REPO, FakeGitHub, build_zip, sha_line


PYTHON = "/usr/bin/python3"
OLD = "v4.2.0.70"
NEW = "v4.2.0.71"
SHAPED_TOKEN = "dummy-token.for-tests.only"
TEMPLATE = "# 必須\nDISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN\n\n# 新しい項目\nNEW_OPTION=1\n"


def release_zip(tag, template=TEMPLATE):
  top = f"tsumugium-{tag}"
  return build_zip(tag, extra=[
    (f"{top}/installer/__init__.py", "", 0o644),
    (f"{top}/installer/handoff.py", "", 0o644),
    (f"{top}/.env.template", template, 0o644),
    (f"{top}/requirements.lock", "discord.py==0.0.0 --hash=sha256:dummy\n", 0o644),
  ])


class UpgradeTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.system = System(Path(self.tmp_dir) / "root", self.runner, euid=0)
    self.record = InstanceRecord(
      "tsumugium", "tsumugium", "tsumugium", "/opt/tsumugium", repo=REPO, version=OLD
    )
    save_record(self.system, self.record)
    self.write("/opt/tsumugium/app/main.py", "old\n")
    self.write("/opt/tsumugium/.env", f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\n")
    self.write("/opt/tsumugium/data/db/config.db", "old-db")
    write_unit(self.system, self.record)
    self.unit_before = unit_path(self.system, "tsumugium").read_text(encoding="utf-8")
    self.runner.calls.clear()

    self.github = FakeGitHub().__enter__()
    self.addCleanup(self.github.__exit__, None, None, None)
    self.github.add_release(OLD, data=release_zip(OLD))
    self.github.add_release(NEW, data=release_zip(NEW))

    self.probe = unittest.mock.patch("installer.upgrade.probe_voicevox", return_value="0.14.10")
    self.probe_mock = self.probe.start()
    self.addCleanup(self.probe.stop)
    self.waits = []
    self.fail_versions = set()
    self.on_new_start = None

  def write(self, absolute, text):
    path = self.system.path(absolute)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

  def read(self, absolute):
    return self.system.path(absolute).read_text(encoding="utf-8")

  def exists(self, absolute):
    return self.system.path(absolute).exists()

  def fake_wait(self, system, layout, service, version):
    self.waits.append(version)
    if version == NEW and self.on_new_start is not None:
      self.on_new_start()
    if version in self.fail_versions:
      raise ReadyError("60秒待ちましたが、Botの準備が終わりませんでした")

  def launch(self, system, ui, app_new, handoff_file):
    self.handoff_data = json.loads(Path(handoff_file).read_text(encoding="utf-8"))
    return handoff.main([str(handoff_file)], system=system, ui=ui, python=PYTHON, wait=self.fake_wait)

  def upgrade(self, argv=(), answers="", assume_yes=True):
    self.ui = UI(
      assume_yes=assume_yes,
      stdin=io.StringIO(answers),
      stdout=io.StringIO(),
      stderr=io.StringIO(),
    )
    handler = functools.partial(
      upgrade.run_upgrade, client=self.github.client(), launch=self.launch
    )
    with unittest.mock.patch.dict(cli.HANDLERS, {"upgrade": handler}):
      return cli.main(["upgrade", *argv], system=self.system, ui=self.ui)

  def output(self):
    return self.ui.stdout.getvalue() + self.ui.stderr.getvalue()

  def systemctl(self):
    return [call[1:] for call in self.runner.calls if call[0] == "systemctl"]

  def backups(self):
    root = self.system.path("/opt/tsumugium/data/backup")
    return sorted(path.name for path in root.iterdir()) if root.exists() else []

  def assert_unchanged(self):
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "old\n")
    self.assertFalse(self.exists("/opt/tsumugium/app.new"))
    self.assertFalse(self.exists("/opt/tsumugium/app.prev"))
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.updating), (OLD, None))
    self.assertEqual(self.backups(), [])
    self.assertNotIn(["stop", "tsumugium.service"], self.systemctl())


class UpgradeSuccessTest(UpgradeTestCase):
  def test_upgrade_to_latest(self):
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_OK, self.output())
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "print('bot')\n")
    self.assertEqual(self.read("/opt/tsumugium/app.prev/main.py"), "old\n")
    self.assertFalse(self.exists("/opt/tsumugium/app.new"))
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.updating, record.unverified), (NEW, None, None))

    self.assertEqual(self.handoff_data["handoff"], 1)
    self.assertEqual((self.handoff_data["from_version"], self.handoff_data["to_version"]), (OLD, NEW))
    self.assertFalse(self.exists("/opt/tsumugium/data/state/handoff.json"))

    [backup] = self.backups()
    self.assertTrue(backup.startswith("upgrade-") and backup.endswith(f"-{OLD}"))
    self.assertEqual(self.read(f"/opt/tsumugium/data/backup/{backup}/config.db"), "old-db")
    self.assertIn("NEW_OPTION=1", self.read("/opt/tsumugium/.env"))
    self.assertTrue(self.exists("/opt/tsumugium/.env.bak"))

    calls = self.systemctl()
    self.assertLess(calls.index(["stop", "tsumugium.service"]), calls.index(["start", "tsumugium.service"]))
    self.assertEqual(self.waits, [NEW])
    venv = [call for call in self.runner.calls if call[:3] == [PYTHON, "-m", "venv"]]
    self.assertEqual(venv, [[PYTHON, "-m", "venv", "--clear", "/opt/tsumugium/app.new/.venv"]])
    self.assertIn(f"{NEW} に更新しました", self.output())

  def test_venv_and_env_are_prepared_while_bot_runs(self):
    order = []
    original = self.runner.run

    def run(args, **kwargs):
      if args[:2] == ["systemctl", "stop"] or "pip" in args:
        order.append(args[1] if args[0] == "systemctl" else "pip")
      return original(args, **kwargs)

    with unittest.mock.patch.object(self.runner, "run", side_effect=run):
      self.upgrade(["--yes"])
    self.assertEqual(order, ["pip", "stop"])

  def test_interactive_confirmation(self):
    self.assertEqual(self.upgrade([], "y\n", assume_yes=False), cli.EXIT_OK, self.output())
    self.assertIn(f"{OLD} から {NEW} に更新します", self.output())

  def test_downgrade_with_flag(self):
    record = load_record(self.system, "tsumugium")
    save_record(self.system, record.__class__(**{**record.__dict__, "version": "v4.2.0.72"}))
    self.assertEqual(self.upgrade(["--yes", "--version", NEW]), cli.EXIT_ERROR)
    self.assertIn("--allow-downgrade", self.output())
    self.assertEqual(self.upgrade(["--yes", "--version", NEW, "--allow-downgrade"]), cli.EXIT_OK)

  def test_from_file(self):
    data = release_zip(NEW)
    zip_path = Path(self.tmp_path(f"tsumugium-{NEW}.zip"))
    zip_path.write_bytes(data)
    Path(str(zip_path) + ".sha256").write_text(sha_line(data, zip_path.name), encoding="utf-8")
    self.assertEqual(self.upgrade(["--yes", "--from-file", str(zip_path)]), cli.EXIT_OK, self.output())
    self.assertEqual(load_record(self.system, "tsumugium").version, NEW)
    self.assertEqual(self.github.requests, [])

  def test_upgrade_backups_keep_five(self):
    for day in range(1, 7):
      self.write(f"/opt/tsumugium/data/backup/upgrade-2026010{day}-000000-v4.2.0.6{day}/config.db", "x")
    self.write("/opt/tsumugium/data/backup/backup_config_20260101.db", "scheduled")
    self.upgrade(["--yes"])
    names = self.backups()
    upgrades = [name for name in names if name.startswith("upgrade-")]
    self.assertEqual(len(upgrades), 5)
    self.assertNotIn("upgrade-20260102-000000-v4.2.0.62", upgrades)
    self.assertTrue(upgrades[-1].endswith(f"-{OLD}"))
    self.assertIn("backup_config_20260101.db", names)


class UpgradeRollbackTest(UpgradeTestCase):
  def test_ready_failure_rolls_back(self):
    self.fail_versions = {NEW}
    self.on_new_start = lambda: self.write("/opt/tsumugium/data/db/config.db", "migrated-db")
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "old\n")
    self.assertFalse(self.exists("/opt/tsumugium/app.prev"))
    self.assertFalse(self.exists("/opt/tsumugium/app.failed"))
    self.assertEqual(self.read("/opt/tsumugium/data/db/config.db"), "old-db")
    self.assertEqual(unit_path(self.system, "tsumugium").read_text(encoding="utf-8"), self.unit_before)
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.updating), (OLD, None))
    self.assertIn("NEW_OPTION=1", self.read("/opt/tsumugium/.env"))
    self.assertEqual(self.waits, [NEW, OLD])
    self.assertEqual(self.systemctl().count(["start", "tsumugium.service"]), 2)
    self.assertIn(f"{OLD} で起動し直しました", self.output())

  def test_failure_while_switching_rolls_back(self):
    with unittest.mock.patch("installer.upgrade.write_unit", side_effect=OSError("disk full")):
      self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "old\n")
    self.assertIn("disk full", self.output())
    self.assertEqual(load_record(self.system, "tsumugium").updating, None)
    self.assertEqual(self.waits, [OLD])


class UpgradeStopsTest(UpgradeTestCase):
  def test_same_version(self):
    record = load_record(self.system, "tsumugium")
    save_record(self.system, record.__class__(**{**record.__dict__, "version": NEW}))
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_OK)
    self.assertIn("すでに", self.output())
    self.assertFalse(self.exists("/opt/tsumugium/app.new"))
    self.assertEqual(self.runner.calls, [])

  def test_voicevox_unreachable_changes_nothing(self):
    self.probe_mock.side_effect = CheckError("接続できません")
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertIn("VOICEVOXを起動してから", self.output())
    self.assert_unchanged()

  def test_new_required_setting_stops_before_switching(self):
    self.github.add_release("v4.2.0.72", data=release_zip(
      "v4.2.0.72", template=TEMPLATE + "\n# 必須\nNEW_REQUIRED=\n"
    ))
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertIn("NEW_REQUIRED が空欄です", self.output())
    self.assert_unchanged()
    self.assertIn("NEW_REQUIRED=", self.read("/opt/tsumugium/.env"))

  def test_unfinished_update_stops(self):
    record = load_record(self.system, "tsumugium")
    save_record(self.system, record.__class__(**{**record.__dict__, "updating": {"from": OLD, "to": NEW}}))
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertIn("repair", self.output())
    self.assertEqual(self.github.requests, [])

  def test_unknown_handoff_number(self):
    path = self.system.path("/opt/tsumugium/data/state/handoff.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"handoff": 99, "instance": "tsumugium"}), encoding="utf-8")
    ui = UI(stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    self.assertEqual(handoff.main([str(path)], system=self.system, ui=ui), 1)
    self.assertIn("handoff=99", ui.stderr.getvalue())
    self.assertEqual(self.runner.calls, [])

  def test_release_without_handoff_is_refused(self):
    self.github.add_release("v4.2.0.72", data=build_zip("v4.2.0.72"))
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertIn("更新できるインストーラーがありません", self.output())
    self.assert_unchanged()


class StoppedBotTest(UpgradeTestCase):
  def setUp(self):
    super().setUp()
    self.runner.responses[("systemctl", "is-active")] = CommandResult(3)

  def test_yes_does_not_upgrade_stopped_bot(self):
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertIn("止まっているため、更新しません", self.output())
    self.assert_unchanged()

  def test_switch_without_starting(self):
    self.assertEqual(self.upgrade([], "y\nn\n", assume_yes=False), cli.EXIT_OK, self.output())
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.unverified), (NEW, NEW))
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "print('bot')\n")
    self.assertNotIn(["start", "tsumugium.service"], self.systemctl())
    self.assertNotIn(["stop", "tsumugium.service"], self.systemctl())
    self.probe_mock.assert_not_called()
    self.assertEqual(self.waits, [])

  def test_switch_and_start(self):
    self.assertEqual(self.upgrade([], "y\ny\n", assume_yes=False), cli.EXIT_OK, self.output())
    self.assertEqual(load_record(self.system, "tsumugium").unverified, None)
    self.assertIn(["start", "tsumugium.service"], self.systemctl())
    self.assertEqual(self.waits, [NEW])


if __name__ == "__main__":
  unittest.main()
