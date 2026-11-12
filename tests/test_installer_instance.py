"""インスタンスの名前・ディレクトリ構成・記録・ロック（installer/instance.py）のテスト。"""

import io
import json
import os
import unittest
import unittest.mock
from dataclasses import replace

from installer import cli
from installer.instance import (
  InstanceError,
  InstanceLock,
  InstanceRecord,
  Layout,
  LockBusy,
  check_channel,
  check_name,
  check_prefix,
  check_repo,
  delete_record,
  list_instances,
  load_record,
  record_path,
  save_record,
  select_instance,
)
from installer.system import System
from installer.ui import UI
from tests.support import TempDirTestCase


def make_ui(answers="", assume_yes=False):
  return UI(
    assume_yes=assume_yes,
    stdin=io.StringIO(answers),
    stdout=io.StringIO(),
    stderr=io.StringIO(),
  )


def make_record(instance="tsumugium", **changes):
  record = InstanceRecord(
    instance=instance,
    user=instance,
    service=instance,
    prefix=f"/opt/{instance}",
  )
  return replace(record, **changes)


class CheckTest(unittest.TestCase):
  def test_valid_names(self):
    for name in ("tsumugium", "tsumugium-beta", "_bot", "a1", "x" * 32):
      with self.subTest(name=name):
        self.assertIsNone(check_name(name))

  def test_invalid_names(self):
    for name in ("", "Tsumugium", "1bot", "-bot", "bot.service", "bot name", "x" * 33, "root"):
      with self.subTest(name=name):
        self.assertIsNotNone(check_name(name))

  def test_valid_prefixes(self):
    for prefix in ("/opt/tsumugium", "/srv/bots/tsumugium", "/var/lib/tsumugium"):
      with self.subTest(prefix=prefix):
        self.assertIsNone(check_prefix(prefix))

  def test_invalid_prefixes(self):
    for prefix in (
        "opt/tsumugium",
        "/",
        "/opt/../home/x",
        "/home/kirima/tsumugium",
        "/home",
        "/root/tsumugium",
        "/tmp/tsumugium",
    ):
      with self.subTest(prefix=prefix):
        self.assertIsNotNone(check_prefix(prefix))

  def test_repo_and_channel(self):
    self.assertIsNone(check_repo("dora-ntmk/Tsumugium"))
    self.assertIsNotNone(check_repo("https://github.com/dora-ntmk/Tsumugium"))
    self.assertIsNotNone(check_repo("Tsumugium"))
    self.assertIsNone(check_channel("stable"))
    self.assertIsNone(check_channel("beta"))
    self.assertIsNotNone(check_channel("nightly"))


class LayoutTest(unittest.TestCase):
  def test_paths(self):
    layout = Layout.of("/opt/tsumugium")
    self.assertEqual(str(layout.env_file), "/opt/tsumugium/.env")
    self.assertEqual(str(layout.env_backup), "/opt/tsumugium/.env.bak")
    self.assertEqual(str(layout.app), "/opt/tsumugium/app")
    self.assertEqual(str(layout.app_prev), "/opt/tsumugium/app.prev")
    self.assertEqual(str(layout.app_new), "/opt/tsumugium/app.new")
    self.assertEqual(str(layout.venv_python), "/opt/tsumugium/app/.venv/bin/python")
    self.assertEqual(str(layout.db), "/opt/tsumugium/data/db")
    self.assertEqual(str(layout.ready_file), "/opt/tsumugium/data/state/ready.json")

  def test_bot_environment_uses_data_directory(self):
    environment = Layout.of("/opt/tsumugium").bot_environment()
    self.assertEqual(environment["SERVER_CONFIG_DB"], "/opt/tsumugium/data/db/config.db")
    self.assertEqual(environment["USER_CONFIG_DB"], "/opt/tsumugium/data/db/users.db")
    self.assertEqual(environment["TMP_DIR"], "/opt/tsumugium/data/tmp")
    self.assertEqual(environment["BACKUP_DIR"], "/opt/tsumugium/data/backup")
    self.assertEqual(environment["UPDATE_STATE_DIR"], "/opt/tsumugium/data/state")
    for value in environment.values():
      self.assertTrue(value.startswith("/opt/tsumugium/data/"))


class RecordTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.system = System(self.tmp_dir)

  def test_save_and_load(self):
    record = make_record(version="v4.2.0.70", admin_user="dora")
    path = save_record(self.system, record)
    self.assertEqual(path, record_path(self.system, "tsumugium"))
    self.assertTrue(str(path).startswith(self.tmp_dir))
    self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o644")
    self.assertEqual(load_record(self.system, "tsumugium"), record)

  def test_saving_leaves_no_temporary_files(self):
    save_record(self.system, make_record())
    save_record(self.system, make_record(version="v4.2.0.71"))
    self.assertEqual(os.listdir(record_path(self.system, "tsumugium").parent), ["tsumugium.json"])

  def test_invalid_record_is_not_saved(self):
    with self.assertRaises(InstanceError):
      save_record(self.system, make_record(prefix="/home/kirima/tsumugium"))
    self.assertFalse(record_path(self.system, "tsumugium").exists())

  def test_missing_record(self):
    with self.assertRaisesRegex(InstanceError, "見つかりません"):
      load_record(self.system, "tsumugium")

  def test_unknown_format_is_rejected(self):
    path = save_record(self.system, make_record())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with self.assertRaisesRegex(InstanceError, "対応していません"):
      load_record(self.system, "tsumugium")

  def test_broken_record_is_rejected(self):
    path = record_path(self.system, "tsumugium")
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with self.assertRaisesRegex(InstanceError, "読み込めません"):
      load_record(self.system, "tsumugium")

  def test_unknown_keys_are_ignored(self):
    path = save_record(self.system, make_record())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["added_in_future"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    self.assertEqual(load_record(self.system, "tsumugium").instance, "tsumugium")

  def test_updating_mark_is_kept(self):
    save_record(self.system, make_record(updating={"from": "v4.2.0.70", "to": "v4.2.0.71"}))
    self.assertEqual(
      load_record(self.system, "tsumugium").updating,
      {"from": "v4.2.0.70", "to": "v4.2.0.71"},
    )

  def test_list_and_delete(self):
    self.assertEqual(list_instances(self.system), [])
    save_record(self.system, make_record("tsumugium"))
    save_record(self.system, make_record("tsumugium-beta"))
    self.assertEqual(list_instances(self.system), ["tsumugium", "tsumugium-beta"])
    delete_record(self.system, "tsumugium")
    delete_record(self.system, "tsumugium")
    self.assertEqual(list_instances(self.system), ["tsumugium-beta"])


class SelectInstanceTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.system = System(self.tmp_dir)

  def test_no_instance(self):
    with self.assertRaisesRegex(InstanceError, "ありません"):
      select_instance(self.system, make_ui(), None)

  def test_single_instance_is_selected(self):
    save_record(self.system, make_record())
    self.assertEqual(select_instance(self.system, make_ui(), None), "tsumugium")

  def test_requested_instance_must_exist(self):
    save_record(self.system, make_record())
    self.assertEqual(select_instance(self.system, make_ui(), "tsumugium"), "tsumugium")
    with self.assertRaises(InstanceError):
      select_instance(self.system, make_ui(), "other")

  def test_multiple_instances_are_chosen(self):
    save_record(self.system, make_record("tsumugium"))
    save_record(self.system, make_record("tsumugium-beta"))
    self.assertEqual(
      select_instance(self.system, make_ui("tsumugium-beta\n"), None),
      "tsumugium-beta",
    )
    with self.assertRaisesRegex(InstanceError, "--instance"):
      select_instance(self.system, make_ui(assume_yes=True), None)


class LockTest(TempDirTestCase):
  def test_second_lock_is_busy(self):
    system = System(self.tmp_dir)
    with InstanceLock(system, "tsumugium"):
      with self.assertRaises(LockBusy):
        with InstanceLock(system, "tsumugium"):
          pass
      with InstanceLock(system, "tsumugium-beta"):
        pass
    with InstanceLock(system, "tsumugium"):
      pass

  def test_lock_file_is_inside_root(self):
    lock = InstanceLock(System(self.tmp_dir), "tsumugium")
    self.assertTrue(str(lock.path).startswith(self.tmp_dir))


class CliInstanceErrorTest(TempDirTestCase):
  def test_instance_errors_become_exit_codes(self):
    def handler(args, system, ui):
      raise LockBusy("ほかの処理が実行中です")

    ui = make_ui()
    with unittest.mock.patch.dict(cli.HANDLERS, {"status": handler}):
      code = cli.main(["status"], system=System(self.tmp_dir, euid=0), ui=ui)
    self.assertEqual(code, cli.EXIT_ERROR)
    self.assertIn("実行中です", ui.stderr.getvalue())


if __name__ == "__main__":
  unittest.main()
