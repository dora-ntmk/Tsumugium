"""status・start・stop・uninstall モードと、入口のスクリプト（installer/manage.py、entry.py）のテスト。"""

import io
import json
import os
import types
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

from installer import cli
from installer.entry import ENTRY_PATH, ENTRY_SCRIPT, install_entry, is_managed_entry, remove_entry
from installer.instance import InstanceRecord, list_instances, load_record, save_record
from installer.readiness import ReadyError
from installer.service import ServiceError, unit_path, write_unit
from installer.system import CommandResult, RecordingRunner, System
from installer.ui import UI
from tests.support import TempDirTestCase


VERSION = "v4.2.0.70"


class ManageTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.system = System(Path(self.tmp_dir) / "root", self.runner, euid=0)
    self.waits = []
    self.wait_error = None

  def add_instance(self, name="tsumugium", **changes):
    record = replace(InstanceRecord(name, name, name, f"/opt/{name}", version=VERSION), **changes)
    save_record(self.system, record)
    self.write(f"/opt/{name}/app/main.py", "bot\n")
    self.write(f"/opt/{name}/.env", "DISCORD_BOT_TOKEN=x\n")
    self.write(f"/opt/{name}/data/db/config.db", "db")
    write_unit(self.system, record)
    return record

  def write(self, absolute, text):
    path = self.system.path(absolute)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

  def exists(self, absolute):
    return self.system.path(absolute).exists()

  def fake_wait(self, system, layout, service, version):
    self.waits.append(version)
    if self.wait_error:
      raise self.wait_error

  def run_cli(self, argv, answers="", assume_yes=None):
    self.ui = UI(
      assume_yes=("--yes" in argv) if assume_yes is None else assume_yes,
      stdin=io.StringIO(answers),
      stdout=io.StringIO(),
      stderr=io.StringIO(),
    )
    self.runner.calls.clear()
    with unittest.mock.patch("installer.manage.wait_ready", self.fake_wait):
      return cli.main(argv, system=self.system, ui=self.ui)

  def output(self):
    return self.ui.stdout.getvalue() + self.ui.stderr.getvalue()

  def systemctl(self):
    return [call[1:] for call in self.runner.calls if call[0] == "systemctl"]

  def set_state(self, active, failed=False, enabled=True):
    self.runner.responses[("systemctl", "is-active")] = CommandResult(0 if active else 3)
    self.runner.responses[("systemctl", "is-failed")] = CommandResult(0 if failed else 1)
    self.runner.responses[("systemctl", "is-enabled")] = CommandResult(0 if enabled else 1)


class StatusTest(ManageTestCase):
  def test_states(self):
    self.add_instance()
    for state, expected in (
        ({"active": True}, "稼働中"),
        ({"active": False}, "停止中（手動）"),
        ({"active": False, "enabled": False}, "停止中（自動起動も無効）"),
        ({"active": False, "failed": True}, "異常で停止"),
    ):
      with self.subTest(expected=expected):
        self.set_state(**state)
        self.assertEqual(self.run_cli(["status"]), cli.EXIT_OK)
        self.assertIn(f"状態        {expected}", self.output())

  def test_all_instances_and_notes(self):
    self.add_instance("tsumugium", unverified="v4.2.0.71", version="v4.2.0.71")
    self.add_instance("tsumugium-beta", channel="beta", updating={"from": VERSION, "to": "v4.2.0.71"})
    self.run_cli(["status"])
    output = self.output()
    self.assertIn("[tsumugium]", output)
    self.assertIn("[tsumugium-beta]", output)
    self.assertIn("未確認の更新あり", output)
    self.assertIn("更新が途中で止まっています", output)
    self.assertIn("自動更新    なし（次のバージョンで追加予定）", output)
    self.run_cli(["status", "--instance", "tsumugium-beta"])
    self.assertNotIn("[tsumugium]", self.output())

  def test_no_instance(self):
    self.assertEqual(self.run_cli(["status"]), cli.EXIT_OK)
    self.assertIn("ありません", self.output())


class StartStopTest(ManageTestCase):
  def test_start_waits_for_ready_and_clears_unverified(self):
    self.add_instance(unverified=VERSION)
    self.set_state(active=False)
    self.assertEqual(self.run_cli(["start"]), cli.EXIT_OK, self.output())
    self.assertEqual(self.systemctl()[-2:], [["enable", "tsumugium.service"], ["start", "tsumugium.service"]])
    self.assertEqual(self.waits, [VERSION])
    self.assertIsNone(load_record(self.system, "tsumugium").unverified)

  def test_start_failure_keeps_unverified(self):
    self.add_instance(unverified=VERSION)
    self.set_state(active=False)
    self.wait_error = ReadyError("Botが起動の途中で止まりました")
    self.assertEqual(self.run_cli(["start"]), cli.EXIT_ERROR)
    self.assertEqual(load_record(self.system, "tsumugium").unverified, VERSION)

  def test_start_when_running(self):
    self.add_instance()
    self.set_state(active=True)
    self.assertEqual(self.run_cli(["start"]), cli.EXIT_OK)
    self.assertIn("すでに動いています", self.output())
    self.assertNotIn(["start", "tsumugium.service"], self.systemctl())

  def test_stop(self):
    self.add_instance()
    self.assertEqual(self.run_cli(["stop"]), cli.EXIT_OK)
    self.assertEqual(self.systemctl(), [["stop", "tsumugium.service"]])
    self.assertIn("--disable", self.output())

  def test_stop_and_disable(self):
    self.add_instance()
    self.assertEqual(self.run_cli(["stop", "--disable"]), cli.EXIT_OK)
    self.assertEqual(self.systemctl(), [["stop", "tsumugium.service"], ["disable", "tsumugium.service"]])

  def test_multiple_instances_need_choice(self):
    self.add_instance("tsumugium")
    self.add_instance("tsumugium-beta")
    self.assertEqual(self.run_cli(["stop", "--yes"]), cli.EXIT_ERROR)
    self.assertIn("--instance", self.output())
    self.assertEqual(self.run_cli(["stop"], "tsumugium-beta\n"), cli.EXIT_OK)
    self.assertEqual(self.systemctl(), [["stop", "tsumugium-beta.service"]])


class UninstallTest(ManageTestCase):
  def setUp(self):
    super().setUp()
    install_entry(self.system)

  def test_uninstall_keeps_data_and_env(self):
    self.add_instance()
    self.assertEqual(self.run_cli(["uninstall"], "y\n"), cli.EXIT_OK, self.output())
    self.assertFalse(self.exists("/opt/tsumugium/app"))
    self.assertFalse(unit_path(self.system, "tsumugium").exists())
    self.assertEqual(list_instances(self.system), [])
    self.assertTrue(self.exists("/opt/tsumugium/data/db/config.db"))
    self.assertTrue(self.exists("/opt/tsumugium/.env"))
    self.assertFalse(self.exists(ENTRY_PATH))
    self.assertIn(["userdel", "tsumugium"], self.runner.calls)
    calls = self.systemctl()
    self.assertLess(calls.index(["stop", "tsumugium.service"]), calls.index(["daemon-reload"]))

  def test_declining_changes_nothing(self):
    self.add_instance()
    self.assertEqual(self.run_cli(["uninstall"], "n\n"), cli.EXIT_ERROR)
    self.assertTrue(self.exists("/opt/tsumugium/app/main.py"))
    self.assertEqual(list_instances(self.system), ["tsumugium"])
    self.assertEqual(self.runner.calls, [])

  def test_purge_needs_two_confirmations(self):
    self.add_instance()
    self.assertEqual(self.run_cli(["uninstall", "--purge"], "y\nwrong\n"), cli.EXIT_ERROR)
    self.assertTrue(self.exists("/opt/tsumugium/data/db/config.db"))
    self.assertEqual(self.run_cli(["uninstall", "--purge"], "y\ntsumugium\n"), cli.EXIT_OK, self.output())
    self.assertFalse(self.exists("/opt/tsumugium"))

  def test_purge_with_yes_is_refused(self):
    self.add_instance()
    self.assertEqual(self.run_cli(["uninstall", "--purge", "--yes"]), cli.EXIT_ERROR)
    self.assertIn("--yes と一緒には使えません", self.output())
    self.assertTrue(self.exists("/opt/tsumugium/data/db/config.db"))

  def test_other_instance_keeps_entry_and_shared_user(self):
    self.add_instance("tsumugium")
    self.add_instance("tsumugium-beta", user="tsumugium")
    self.assertEqual(self.run_cli(["uninstall", "--yes", "--instance", "tsumugium-beta"]), cli.EXIT_OK)
    self.assertTrue(self.exists(ENTRY_PATH))
    self.assertNotIn(["userdel", "tsumugium"], self.runner.calls)
    self.assertEqual(list_instances(self.system), ["tsumugium"])


def load_entry_module():
  module = types.ModuleType("tsumugium_setup_entry")
  exec(compile(ENTRY_SCRIPT, "tsumugium-setup", "exec"), module.__dict__)
  return module


class EntryTest(ManageTestCase):
  def setUp(self):
    super().setUp()
    self.entry = load_entry_module()
    self.record_dir = str(self.system.path("/etc/tsumugium"))
    self.executed = []

  def execute(self, path, argv, env):
    self.assertEqual(argv[1:3], ["-I", "-c"])
    self.executed.append((argv[5:], argv[4]))

  def run_entry(self, argv, stdin=""):
    with unittest.mock.patch("sys.stdin", io.StringIO(stdin)), \
        unittest.mock.patch("sys.stdout", io.StringIO()), \
        unittest.mock.patch("sys.stderr", io.StringIO()) as stderr:
      code = self.entry.main(argv, record_dir=self.record_dir, execute=self.execute)
    return code, stderr.getvalue()

  def app_of(self, name, sub="app"):
    path = self.system.path(f"/opt/{name}/{sub}/installer")
    path.mkdir(parents=True, exist_ok=True)
    return str(path.parent)

  def prefix_records(self, *names):
    for name in names:
      self.add_instance(name)
      data = json.loads(self.system.path(f"/etc/tsumugium/{name}.json").read_text(encoding="utf-8"))
      data["prefix"] = str(self.system.path(f"/opt/{name}"))
      self.system.path(f"/etc/tsumugium/{name}.json").write_text(json.dumps(data), encoding="utf-8")

  def test_single_instance(self):
    self.prefix_records("tsumugium")
    app = self.app_of("tsumugium")
    self.assertEqual(self.run_entry(["upgrade", "--yes"])[0], 0)
    self.assertEqual(self.executed, [(["upgrade", "--yes", "--instance", "tsumugium"], app)])

  def test_status_does_not_add_instance(self):
    self.prefix_records("tsumugium", "tsumugium-beta")
    self.app_of("tsumugium")
    self.run_entry(["status"])
    self.assertEqual(self.executed[0][0], ["status"])

  def test_multiple_instances(self):
    self.prefix_records("tsumugium", "tsumugium-beta")
    app = self.app_of("tsumugium-beta")
    code, stderr = self.run_entry(["stop", "--yes"])
    self.assertEqual(code, 1)
    self.assertIn("--instance", stderr)
    self.run_entry(["stop"], "tsumugium-beta\n")
    self.assertEqual(self.executed, [(["stop", "--instance", "tsumugium-beta"], app)])

  def test_rollback_uses_previous_installer(self):
    self.prefix_records("tsumugium")
    self.app_of("tsumugium")
    code, stderr = self.run_entry(["rollback"])
    self.assertEqual(code, 1)
    self.assertIn("1つ前の版がありません", stderr)
    prev = self.app_of("tsumugium", "app.prev")
    self.run_entry(["rollback", "--instance", "tsumugium"])
    self.assertEqual(self.executed[-1][1], prev)

  def test_no_instance(self):
    code, stderr = self.run_entry(["status"])
    self.assertEqual(code, 1)
    self.assertIn("install.sh", stderr)

  def test_install_and_remove_entry(self):
    install_entry(self.system)
    path = self.system.path(ENTRY_PATH)
    self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o755")
    self.assertTrue(is_managed_entry(path))
    self.assertTrue(remove_entry(self.system))
    path.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    with self.assertRaises(ServiceError):
      install_entry(self.system)
    self.assertFalse(remove_entry(self.system))
    self.assertTrue(path.exists())


if __name__ == "__main__":
  unittest.main()
