"""repair・rollback モードと、途中で止まった更新の扱い（installer/upgrade.py、rollback.py）のテスト。"""

import functools
import io
import json
import unittest.mock
from dataclasses import replace

from installer import cli, rollback, upgrade
from installer.instance import load_record, save_record
from installer.service import unit_path
from installer.system import CommandResult
from installer.ui import UI
from tests.test_installer_upgrade import NEW, OLD, UpgradeTestCase, release_zip


class RepairTest(UpgradeTestCase):
  def setUp(self):
    super().setUp()
    self.write("/opt/tsumugium/app.prev/main.py", "previous\n")
    self.write("/opt/tsumugium/app.prev/build-info.json", json.dumps({"version": "v4.2.0.69"}))

  def repair(self, argv=(), answers="", assume_yes=True):
    self.ui = UI(assume_yes=assume_yes, stdin=io.StringIO(answers), stdout=io.StringIO(), stderr=io.StringIO())
    handler = functools.partial(upgrade.run_repair, client=self.github.client(), launch=self.launch)
    with unittest.mock.patch.dict(cli.HANDLERS, {"repair": handler}):
      return cli.main(["repair", *argv], system=self.system, ui=self.ui)

  def test_repair_reinstalls_same_version_and_keeps_prev(self):
    self.write("/opt/tsumugium/app/main.py", "broken\n")
    self.assertEqual(self.repair(["--yes"]), cli.EXIT_OK, self.output())
    self.assertEqual(self.handoff_data["mode"], "repair")
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "print('bot')\n")
    self.assertEqual(self.read("/opt/tsumugium/app.prev/main.py"), "previous\n")
    self.assertFalse(self.exists("/opt/tsumugium/app.repair-old"))
    self.assertEqual(load_record(self.system, "tsumugium").version, OLD)
    self.assertEqual(self.waits, [OLD])
    self.assertIn(f"{OLD} を入れ直しました", self.output())

  def test_repair_failure_restores_previous_state(self):
    self.write("/opt/tsumugium/app/main.py", "broken\n")
    self.fail_versions = {OLD}
    self.assertEqual(self.repair(["--yes"]), cli.EXIT_ERROR)
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "broken\n")
    self.assertEqual(self.read("/opt/tsumugium/app.prev/main.py"), "previous\n")
    self.assertFalse(self.exists("/opt/tsumugium/app.repair-old"))
    self.assertEqual(self.read("/opt/tsumugium/data/db/config.db"), "old-db")
    self.assertIsNone(load_record(self.system, "tsumugium").updating)

  def test_repair_starts_stopped_bot_with_yes(self):
    self.runner.responses[("systemctl", "is-active")] = CommandResult(3)
    self.assertEqual(self.repair(["--yes"]), cli.EXIT_OK, self.output())
    self.assertIn(["start", "tsumugium.service"], self.systemctl())
    self.assertEqual(self.waits, [OLD])

  def test_repair_from_file_must_be_same_version(self):
    from tests.test_installer_release import sha_line
    from pathlib import Path
    data = release_zip(NEW)
    zip_path = Path(self.tmp_path(f"tsumugium-{NEW}.zip"))
    zip_path.write_bytes(data)
    Path(str(zip_path) + ".sha256").write_text(sha_line(data, zip_path.name), encoding="utf-8")
    self.assertEqual(self.repair(["--yes", "--from-file", str(zip_path)]), cli.EXIT_ERROR)
    self.assertIn("同じ版のzip", self.output())
    self.assert_unchanged_app()

  def assert_unchanged_app(self):
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "old\n")
    self.assertFalse(self.exists("/opt/tsumugium/app.new"))

  def test_repair_finishes_interrupted_update_without_app(self):
    # app/ を app.prev/ に移したところで止まった状態
    self.system.path("/opt/tsumugium/app.prev/main.py").unlink()
    self.system.path("/opt/tsumugium/app.prev/build-info.json").unlink()
    self.system.path("/opt/tsumugium/app.prev").rmdir()
    self.system.path("/opt/tsumugium/app").rename(self.system.path("/opt/tsumugium/app.prev"))
    record = load_record(self.system, "tsumugium")
    save_record(self.system, replace(record, updating={"from": OLD, "to": NEW}))
    self.assertEqual(self.repair(["--yes"]), cli.EXIT_OK, self.output())
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.updating), (OLD, None))
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "print('bot')\n")


class RollbackTest(UpgradeTestCase):
  def setUp(self):
    super().setUp()
    # v4.2.0.70 → v4.2.0.71 に更新したあとの状態を作る（実際の app/ には build-info.json がある）
    self.write("/opt/tsumugium/app/build-info.json", json.dumps({"version": OLD}))
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_OK)
    self.write("/opt/tsumugium/data/db/config.db", "new-db")
    self.runner.calls.clear()
    self.waits.clear()

  def rollback(self, argv=(), answers="", assume_yes=True):
    self.ui = UI(assume_yes=assume_yes, stdin=io.StringIO(answers), stdout=io.StringIO(), stderr=io.StringIO())
    handler = functools.partial(rollback.run_rollback, wait=self.fake_wait)
    with unittest.mock.patch.dict(cli.HANDLERS, {"rollback": handler}):
      return cli.main(["rollback", *argv], system=self.system, ui=self.ui)

  def test_rollback_restores_code_and_db(self):
    self.assertEqual(self.rollback(["--yes"]), cli.EXIT_OK, self.output())
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "old\n")
    self.assertEqual(self.read("/opt/tsumugium/app.prev/main.py"), "print('bot')\n")
    self.assertEqual(self.read("/opt/tsumugium/data/db/config.db"), "old-db")
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.updating, record.unverified), (OLD, None, None))
    self.assertIn('TSUMUGIUM_ENV_FILE', unit_path(self.system, "tsumugium").read_text(encoding="utf-8"))
    self.assertEqual(self.waits, [OLD])
    self.assertTrue(self.exists("/usr/local/sbin/tsumugium-setup"))

  def test_rollback_keeping_db(self):
    self.assertEqual(self.rollback([], "y\nn\n", assume_yes=False), cli.EXIT_OK, self.output())
    self.assertEqual(self.read("/opt/tsumugium/data/db/config.db"), "new-db")
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "old\n")

  def test_rollback_without_backup(self):
    import shutil
    shutil.rmtree(self.system.path("/opt/tsumugium/data/backup"))
    self.assertEqual(self.rollback(["--yes"]), cli.EXIT_OK)
    self.assertIn("バックアップが見つからない", self.output())
    self.assertEqual(self.read("/opt/tsumugium/data/db/config.db"), "new-db")

  def test_rollback_of_stopped_bot(self):
    self.runner.responses[("systemctl", "is-active")] = CommandResult(3)
    self.assertEqual(self.rollback(["--yes"]), cli.EXIT_OK)
    self.assertNotIn(["start", "tsumugium.service"], self.systemctl())
    self.assertEqual(load_record(self.system, "tsumugium").unverified, OLD)

  def test_rollback_start_failure(self):
    self.fail_versions = {OLD}
    self.assertEqual(self.rollback(["--yes"]), cli.EXIT_ERROR)
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.unverified), (OLD, OLD))
    self.assertIn("repair", self.output())

  def test_no_previous_version(self):
    import shutil
    shutil.rmtree(self.system.path("/opt/tsumugium/app.prev"))
    self.assertEqual(self.rollback(["--yes"]), cli.EXIT_ERROR)
    self.assertIn("1つ前の版がありません", self.output())
    self.assertEqual(self.runner.calls, [])

  def test_declining_changes_nothing(self):
    self.assertEqual(self.rollback([], "n\n", assume_yes=False), cli.EXIT_ERROR)
    self.assertEqual(self.read("/opt/tsumugium/app/main.py"), "print('bot')\n")
    self.assertEqual(self.runner.calls, [])


class UnfinishedUpdateTest(UpgradeTestCase):
  def setUp(self):
    super().setUp()
    record = load_record(self.system, "tsumugium")
    save_record(self.system, replace(record, updating={"from": OLD, "to": NEW}))
    self.rollbacks = []

  def upgrade_with_choice(self, answers):
    self.ui = UI(assume_yes=False, stdin=io.StringIO(answers), stdout=io.StringIO(), stderr=io.StringIO())
    handler = functools.partial(
      upgrade.run_upgrade,
      client=self.github.client(),
      launch=self.launch,
      rollback=lambda system, ui, record: self.rollbacks.append(record.instance) or 0,
    )
    with unittest.mock.patch.dict(cli.HANDLERS, {"upgrade": handler}):
      return cli.main(["upgrade"], system=self.system, ui=self.ui)

  def test_choose_repair(self):
    self.assertEqual(self.upgrade_with_choice("repair\ny\n"), cli.EXIT_OK, self.output())
    self.assertEqual(self.handoff_data["mode"], "repair")
    record = load_record(self.system, "tsumugium")
    self.assertEqual((record.version, record.updating), (OLD, None))

  def test_choose_rollback(self):
    self.assertEqual(self.upgrade_with_choice("rollback\n"), cli.EXIT_OK)
    self.assertEqual(self.rollbacks, ["tsumugium"])
    self.assertEqual(self.github.requests, [])

  def test_yes_stops(self):
    self.assertEqual(self.upgrade(["--yes"]), cli.EXIT_ERROR)
    self.assertIn("途中で止まっています", self.output())
    self.assertEqual(self.github.requests, [])


class EntryFallbackTest(UpgradeTestCase):
  def test_entry_uses_app_prev_when_app_is_missing(self):
    import types
    from installer.entry import ENTRY_SCRIPT
    module = types.ModuleType("entry")
    exec(compile(ENTRY_SCRIPT, "tsumugium-setup", "exec"), module.__dict__)
    record_dir = str(self.system.path("/etc/tsumugium"))
    path = self.system.path("/etc/tsumugium/tsumugium.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["prefix"] = str(self.system.path("/opt/tsumugium"))
    path.write_text(json.dumps(data), encoding="utf-8")
    self.system.path("/opt/tsumugium/app.prev/installer").mkdir(parents=True)
    executed = []
    module.main(["repair", "--yes"], record_dir=record_dir,
                execute=lambda path, argv, env: executed.append(env["PYTHONPATH"]))
    self.assertEqual(executed, [str(self.system.path("/opt/tsumugium/app.prev"))])


if __name__ == "__main__":
  unittest.main()
