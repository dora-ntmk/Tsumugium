"""専用ユーザー・ディレクトリ・systemd unit（installer/service.py）のテスト。"""

import io
import os
import unittest
import unittest.mock
from dataclasses import replace

from installer import cli
from installer.envfile import managed_keys_in, EnvFile
from installer.instance import InstanceRecord, Layout
from installer.service import (
  UNIT_MARKER,
  ServiceError,
  Systemctl,
  ensure_user,
  prepare_directories,
  remove_unit,
  render_unit,
  secure_app,
  unit_environment,
  unit_path,
  write_unit,
)
from installer.system import CommandResult, RecordingRunner, System
from installer.ui import UI
from tests.support import TempDirTestCase


def make_record(**changes):
  record = InstanceRecord(
    instance="tsumugium-beta",
    user="tsumugium-beta",
    service="tsumugium-beta",
    prefix="/opt/tsumugium-beta",
  )
  return replace(record, **changes)


class ServiceTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.system = System(self.tmp_dir, self.runner, euid=0)

  def calls(self, command):
    return [call for call in self.runner.calls if call[0] == command]


class UserTest(ServiceTestCase):
  def test_existing_user_is_used(self):
    self.assertFalse(ensure_user(self.system, "tsumugium", "/opt/tsumugium"))
    self.assertEqual(self.calls("useradd"), [])

  def test_missing_user_is_created(self):
    self.runner.responses[("id", "-u", "tsumugium")] = CommandResult(1, "", "no such user")
    self.assertTrue(ensure_user(self.system, "tsumugium", "/opt/tsumugium"))
    self.assertEqual(self.calls("useradd"), [[
      "useradd", "--system", "--user-group",
      "--home-dir", "/opt/tsumugium", "--no-create-home",
      "--shell", "/usr/sbin/nologin",
      "tsumugium",
    ]])


class DirectoryTest(ServiceTestCase):
  def test_prepare_directories(self):
    layout = Layout.of("/opt/tsumugium")
    prepare_directories(self.system, layout, "tsumugium")
    prefix = self.system.path("/opt/tsumugium")
    self.assertEqual(oct(os.stat(prefix).st_mode & 0o777), "0o755")
    for name in ("data", "data/db", "data/backup", "data/tmp", "data/state"):
      with self.subTest(name=name):
        path = prefix / name
        self.assertTrue(path.is_dir())
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o750")
    self.assertEqual(self.calls("chown"), [
      ["chown", "root:root", "/opt/tsumugium"],
      ["chown", "-R", "tsumugium:tsumugium", "/opt/tsumugium/data"],
    ])

  def test_existing_data_is_kept(self):
    db = self.system.path("/opt/tsumugium/data/db/config.db")
    db.parent.mkdir(parents=True)
    db.write_bytes(b"dummy")
    prepare_directories(self.system, Layout.of("/opt/tsumugium"), "tsumugium")
    self.assertEqual(db.read_bytes(), b"dummy")

  def test_secure_app(self):
    secure_app(self.system, "/opt/tsumugium/app.new")
    self.assertEqual(self.runner.calls, [
      ["chown", "-R", "root:root", "/opt/tsumugium/app.new"],
      ["chmod", "-R", "u=rwX,go=rX", "/opt/tsumugium/app.new"],
    ])


class RenderUnitTest(unittest.TestCase):
  def test_unit_contents(self):
    text = render_unit(make_record(voicevox_url="http://192.0.2.10:50021"))
    lines = text.splitlines()
    self.assertEqual(lines[0], UNIT_MARKER)
    for expected in (
        "Description=Tsumugium (tsumugium-beta)",
        "After=network-online.target",
        "User=tsumugium-beta",
        "Group=tsumugium-beta",
        "WorkingDirectory=/opt/tsumugium-beta/app",
        "ExecStart=/opt/tsumugium-beta/app/.venv/bin/python main.py",
        'Environment="PYTHONUNBUFFERED=1"',
        'Environment="TSUMUGIUM_ENV_FILE=/opt/tsumugium-beta/.env"',
        'Environment="SERVER_CONFIG_DB=/opt/tsumugium-beta/data/db/config.db"',
        'Environment="UPDATE_STATE_DIR=/opt/tsumugium-beta/data/state"',
        'Environment="VOICEVOX_URL=http://192.0.2.10:50021"',
        "Restart=on-failure",
        "RestartSec=10",
        "ProtectSystem=strict",
        "ReadWritePaths=/opt/tsumugium-beta/data",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "NoNewPrivileges=yes",
        "WantedBy=multi-user.target",
    ):
      with self.subTest(expected=expected):
        self.assertIn(expected, lines)

  def test_special_characters_are_escaped(self):
    text = render_unit(make_record(voicevox_url='http://host:50021/a%20b"c'))
    self.assertIn('Environment="VOICEVOX_URL=http://host:50021/a%%20b\\"c"', text)

  def test_unit_environment_matches_managed_keys(self):
    record = make_record()
    environment = unit_environment(record)
    env = EnvFile.parse("".join(f"{key}=x\n" for key in environment))
    self.assertEqual(
      set(managed_keys_in(env, record.layout)),
      set(environment) - {"PYTHONUNBUFFERED", "TSUMUGIUM_ENV_FILE"},
    )


class WriteUnitTest(ServiceTestCase):
  def test_write_and_reload(self):
    record = make_record()
    self.assertTrue(write_unit(self.system, record))
    path = unit_path(self.system, "tsumugium-beta")
    self.assertEqual(path.read_text(encoding="utf-8"), render_unit(record))
    self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o644")
    self.assertEqual(os.listdir(path.parent), ["tsumugium-beta.service"])
    self.assertEqual(self.runner.calls, [["systemctl", "daemon-reload"]])

  def test_same_contents_are_not_rewritten(self):
    write_unit(self.system, make_record())
    self.runner.calls.clear()
    self.assertFalse(write_unit(self.system, make_record()))
    self.assertEqual(self.runner.calls, [])
    self.assertTrue(write_unit(self.system, make_record(voicevox_url="http://127.0.0.1:50022")))
    self.assertEqual(self.runner.calls, [["systemctl", "daemon-reload"]])

  def test_foreign_unit_is_not_overwritten(self):
    path = unit_path(self.system, "tsumugium-beta")
    path.parent.mkdir(parents=True)
    original = "[Unit]\nDescription=old bot\n"
    path.write_text(original, encoding="utf-8")
    with self.assertRaisesRegex(ServiceError, "上書きしません"):
      write_unit(self.system, make_record())
    with self.assertRaises(ServiceError):
      remove_unit(self.system, "tsumugium-beta")
    self.assertEqual(path.read_text(encoding="utf-8"), original)
    self.assertEqual(self.runner.calls, [])

  def test_remove_unit(self):
    self.assertFalse(remove_unit(self.system, "tsumugium-beta"))
    write_unit(self.system, make_record())
    self.runner.calls.clear()
    self.assertTrue(remove_unit(self.system, "tsumugium-beta"))
    self.assertFalse(unit_path(self.system, "tsumugium-beta").exists())
    self.assertEqual(self.runner.calls, [["systemctl", "daemon-reload"]])


class SystemctlTest(ServiceTestCase):
  def test_commands(self):
    systemctl = Systemctl(self.system)
    systemctl.enable("tsumugium")
    systemctl.start("tsumugium")
    systemctl.restart("tsumugium")
    systemctl.stop("tsumugium")
    systemctl.disable("tsumugium")
    self.assertEqual(self.runner.calls, [
      ["systemctl", "enable", "tsumugium.service"],
      ["systemctl", "start", "tsumugium.service"],
      ["systemctl", "restart", "tsumugium.service"],
      ["systemctl", "stop", "tsumugium.service"],
      ["systemctl", "disable", "tsumugium.service"],
    ])

  def test_state_queries(self):
    systemctl = Systemctl(self.system)
    self.assertTrue(systemctl.is_active("tsumugium"))
    self.runner.responses[("systemctl", "is-active")] = CommandResult(3)
    self.runner.responses[("systemctl", "is-enabled")] = CommandResult(1)
    self.assertFalse(systemctl.is_active("tsumugium"))
    self.assertFalse(systemctl.is_enabled("tsumugium"))


class CliServiceErrorTest(TempDirTestCase):
  def test_service_errors_become_exit_codes(self):
    def handler(args, system, ui):
      raise ServiceError("上書きしません")

    ui = UI(stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    with unittest.mock.patch.dict(cli.HANDLERS, {"install": handler}):
      code = cli.main(["install"], system=System(self.tmp_dir, euid=0), ui=ui)
    self.assertEqual(code, cli.EXIT_ERROR)
    self.assertIn("上書きしません", ui.stderr.getvalue())


if __name__ == "__main__":
  unittest.main()
