"""install モード（installer/install.py）のテスト。

一時ディレクトリをシステムのルートにし、コマンド（useradd、pip、systemctl など）は記録するだけにする。
"""

import functools
import io
import json
import os
import unittest
import unittest.mock
from pathlib import Path

from installer import cli, install
from installer.checks import CheckError
from installer.envfile import EnvError
from installer.instance import InstanceError, load_record, record_path, save_record, InstanceRecord
from installer.readiness import ReadyError
from installer.service import unit_path
from installer.system import CommandResult, RecordingRunner, System
from installer.ui import UI, UserAbort
from tests.support import TempDirTestCase


PYTHON = "/usr/bin/python3"
VERSION = "v4.2.0.70 (beta 9)"
SHAPED_TOKEN = "dummy-token.for-tests.only"
ADMIN = "admin1"


class InstallTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.runner.responses[("dpkg-query", "-W", "-f=${Status}", "ffmpeg")] = CommandResult(
      0, "install ok installed"
    )
    self.root = Path(self.tmp_dir) / "root"
    self.system = System(self.root, self.runner, euid=0)
    self.write_root("/etc/os-release", 'ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n')
    self.system.path("/run/systemd/system").mkdir(parents=True)

    self.source = Path(self.tmp_dir) / "tsumugium-v4.2.0.70-beta.9"
    self.write_source("build-info.json", json.dumps({"version": VERSION, "channel": "beta"}))
    self.write_source("main.py", "print('bot')\n")
    self.write_source("installer/__init__.py", "")
    self.write_source("requirements.lock", "discord.py==0.0.0 --hash=sha256:dummy\n")
    self.write_source(".env.template", "# 必須\nDISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN\n")
    self.write_source(".env", f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\n")

    self.waits = []
    self.wait_error = None
    self.probe = unittest.mock.patch("installer.checks.probe_voicevox", return_value="0.14.10")
    self.probe_mock = self.probe.start()
    self.addCleanup(self.probe.stop)

  def write_root(self, absolute, text):
    path = self.system.path(absolute)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

  def write_source(self, relative, text):
    path = self.source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

  def fake_wait(self, system, layout, service, version):
    self.waits.append((str(layout.prefix), service, version))
    if self.wait_error is not None:
      raise self.wait_error

  def run_install(self, argv, answers="", assume_yes=None):
    args = cli.build_parser().parse_args(["install", *argv])
    self.ui = UI(
      assume_yes=args.yes if assume_yes is None else assume_yes,
      stdin=io.StringIO(answers),
      stdout=io.StringIO(),
      stderr=io.StringIO(),
    )
    return install.handle(
      args,
      self.system,
      self.ui,
      source=self.source,
      python=PYTHON,
      version_info=(3, 10, 12),
      wait=self.fake_wait,
    )

  def output(self):
    return self.ui.stdout.getvalue() + self.ui.stderr.getvalue()

  def calls(self, command):
    return [call for call in self.runner.calls if call[0] == command]

  def assert_nothing_installed(self):
    self.assertFalse(self.system.path("/opt").exists())
    self.assertFalse(self.system.path("/etc/tsumugium").exists())
    self.assertFalse(self.system.path("/etc/systemd").exists())
    for command in ("useradd", "usermod", "chown", "systemctl", "apt-get"):
      self.assertEqual(self.calls(command), [], command)


class InstallFlowTest(InstallTestCase):
  def test_yes_installs_with_defaults(self):
    self.assertEqual(self.run_install(["--yes"]), install.EXIT_OK)

    prefix = self.system.path("/opt/tsumugium")
    self.assertEqual((prefix / "app" / "main.py").read_text(encoding="utf-8"), "print('bot')\n")
    self.assertTrue((prefix / "app" / "build-info.json").is_file())
    self.assertFalse((prefix / "app" / ".env").exists())
    self.assertFalse((prefix / "app.new").exists())
    env = prefix / ".env"
    self.assertEqual(env.read_text(encoding="utf-8"), f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\n")
    self.assertEqual(oct(os.stat(env).st_mode & 0o777), "0o640")

    record = load_record(self.system, "tsumugium")
    self.assertEqual(
      (record.user, record.service, record.prefix, record.repo, record.channel, record.version),
      ("tsumugium", "tsumugium", "/opt/tsumugium", "dora-ntmk/Tsumugium", "stable", VERSION),
    )
    self.assertIsNone(record.admin_user)
    unit = unit_path(self.system, "tsumugium").read_text(encoding="utf-8")
    self.assertIn('Environment="TSUMUGIUM_ENV_FILE=/opt/tsumugium/.env"', unit)

    self.assertIn([PYTHON, "-m", "venv", "--clear", "/opt/tsumugium/app.new/.venv"], self.runner.calls)
    pip = [call for call in self.runner.calls if "pip" in call]
    self.assertEqual(len(pip), 1)
    self.assertEqual(pip[0][0], "/opt/tsumugium/app.new/.venv/bin/python")
    self.assertIn("--require-hashes", pip[0])
    self.assertEqual(pip[0][-1], "/opt/tsumugium/app.new/requirements.lock")
    self.assertIn(["chown", "-R", "root:root", "/opt/tsumugium/app.new"], self.runner.calls)
    # 画面に出力を流すのは、時間のかかる venv の作成と pip だけ
    self.assertEqual(
      [call[:3] for call in self.runner.streamed],
      [[PYTHON, "-m", "venv"], ["/opt/tsumugium/app.new/.venv/bin/python", "-m", "pip"]],
    )
    self.assertIn(["chown", "root:tsumugium", "/opt/tsumugium/.env"], self.runner.calls)

    systemctl = [call[1:] for call in self.calls("systemctl")]
    self.assertEqual(systemctl, [
      ["daemon-reload"],
      # 自動更新の timer と service を書き、stable の既定（オフ）に合わせて無効にする
      ["daemon-reload"],
      ["disable", "--now", "tsumugium-update.timer"],
      ["enable", "tsumugium.service"],
      ["start", "tsumugium.service"],
    ])
    self.assertFalse(load_record(self.system, "tsumugium").auto_update)
    self.assertTrue(self.system.path("/etc/systemd/system/tsumugium-update.timer").is_file())
    self.assertIn("自動更新:     オフ", self.output())
    self.assertEqual(self.waits, [("/opt/tsumugium", "tsumugium", VERSION)])
    self.assertIn("インストールが完了しました", self.output())
    self.assertNotIn(SHAPED_TOKEN, self.output())
    entry = self.system.path("/usr/local/sbin/tsumugium-setup")
    self.assertEqual(oct(os.stat(entry).st_mode & 0o777), "0o755")

  def test_venv_is_built_before_switching(self):
    order = []
    original = self.runner.run

    def run(args, **kwargs):
      if "pip" in args:
        order.append(("pip", self.system.path("/opt/tsumugium/app").exists()))
      return original(args, **kwargs)

    with unittest.mock.patch.object(self.runner, "run", side_effect=run):
      self.run_install(["--yes"])
    self.assertEqual(order, [("pip", False)])

  def test_interactive_answers(self):
    answers = "\n".join([
      "tsumugium-beta",   # インスタンス名
      "",                 # 専用ユーザー名
      "",                 # サービス名
      "",                 # 入れる場所
      "",                 # 取得元
      "beta",             # チャンネル
      "",                 # 自動更新（beta の既定はオン）
      ADMIN,              # 管理者
      "",                 # VOICEVOXのURL
      "y",                # 確認
    ]) + "\n"
    self.assertEqual(self.run_install([], answers), install.EXIT_OK)
    record = load_record(self.system, "tsumugium-beta")
    self.assertEqual(
      (record.user, record.service, record.prefix, record.channel, record.admin_user),
      ("tsumugium-beta", "tsumugium-beta", "/opt/tsumugium-beta", "beta", ADMIN),
    )
    self.assertTrue(record.auto_update)
    self.assertIn(
      ["systemctl", "enable", "--now", "tsumugium-beta-update.timer"], self.runner.calls
    )
    self.assertEqual(self.calls("usermod"), [["usermod", "-aG", "tsumugium-beta", ADMIN]])
    output = self.output()
    for expected in ("[1/10 .env]", "[10/10 確認]", "beta（ベータ）", f"{ADMIN}（tsumugium-beta グループに追加）"):
      self.assertIn(expected, output)

  def test_options_are_not_asked(self):
    argv = [
      "--instance", "bot2", "--user", "botuser", "--service", "bot2svc",
      "--prefix", "/srv/bot2", "--repo", "example/Fork", "--channel", "beta",
      "--admin", ADMIN, "--voicevox-url", "http://192.0.2.10:50021", "--no-auto-update",
    ]
    self.assertEqual(self.run_install(argv, "y\n"), install.EXIT_OK)
    record = load_record(self.system, "bot2")
    self.assertEqual(
      (record.user, record.service, record.prefix, record.repo, record.voicevox_url),
      ("botuser", "bot2svc", "/srv/bot2", "example/Fork", "http://192.0.2.10:50021"),
    )
    self.assertFalse(record.auto_update)
    self.probe_mock.assert_called_once()
    output = self.output()
    self.assertNotIn("インスタンス名 [", output)
    self.assertNotIn("VOICEVOXのURL [", output)
    self.assertIn("既定（dora-ntmk/Tsumugium）ではありません", output)

  def test_new_user_is_created(self):
    self.runner.responses[("id", "-u", "tsumugium")] = CommandResult(1, "", "no such user")
    self.run_install(["--yes"])
    self.assertEqual(len(self.calls("useradd")), 1)
    self.assertIn("tsumugium（新しく作成）", self.output())

  def test_data_is_imported(self):
    self.write_source("data/db/config.db", "dummy-db")
    self.write_source("old/main.py", "old\n")
    self.run_install(["--yes"])
    prefix = self.system.path("/opt/tsumugium")
    self.assertEqual((prefix / "data/db/config.db").read_text(encoding="utf-8"), "dummy-db")
    self.assertFalse((prefix / "app" / "data").exists())
    self.assertFalse((prefix / "app" / "old").exists())
    self.assertEqual(
      self.runner.calls.count(["chown", "-R", "tsumugium:tsumugium", "/opt/tsumugium/data"]), 2
    )

  def test_voicevox_skip_installs_without_starting(self):
    self.probe_mock.side_effect = CheckError("接続できません")
    answers = "\n" * 9 + "skip\ny\n"
    self.assertEqual(self.run_install([], answers), install.EXIT_OK)
    systemctl = [call[1:] for call in self.calls("systemctl")]
    self.assertIn(["enable", "tsumugium.service"], systemctl)
    self.assertNotIn(["start", "tsumugium.service"], systemctl)
    self.assertEqual(self.waits, [])
    self.assertIn("sudo systemctl start tsumugium.service", self.output())

  def test_ready_failure_keeps_installation(self):
    self.wait_error = ReadyError("60秒待ちましたが、Botの準備が終わりませんでした")
    self.assertEqual(self.run_install(["--yes"]), install.EXIT_ERROR)
    self.assertTrue(record_path(self.system, "tsumugium").exists())
    self.assertTrue(self.system.path("/opt/tsumugium/app/main.py").exists())
    self.assertIn("sudo systemctl restart tsumugium.service", self.output())
    self.assertNotIn("インストールが完了しました", self.output())


class InstallStopsTest(InstallTestCase):
  def test_declining_changes_nothing(self):
    with self.assertRaises(UserAbort):
      self.run_install([], "\n" * 8 + "n\n")
    self.assert_nothing_installed()

  def test_missing_env_stops_first(self):
    os.remove(self.source / ".env")
    with self.assertRaisesRegex(EnvError, "見つかりません"):
      self.run_install(["--yes"])
    self.assertEqual(self.runner.calls, [])

  def test_existing_instance_stops(self):
    save_record(self.system, InstanceRecord("tsumugium", "tsumugium", "tsumugium", "/opt/tsumugium"))
    with self.assertRaisesRegex(UserAbort, "upgrade"):
      self.run_install(["--yes"])
    self.assertFalse(self.system.path("/opt").exists())
    self.assertEqual(self.calls("systemctl"), [])

  def test_foreign_unit_stops(self):
    self.write_root("/etc/systemd/system/tsumugium.service", "[Unit]\nDescription=old\n")
    with self.assertRaisesRegex(UserAbort, "別の名前"):
      self.run_install(["--yes"])
    self.assertFalse(self.system.path("/opt").exists())

  def test_existing_app_stops(self):
    self.system.path("/opt/tsumugium/app").mkdir(parents=True)
    with self.assertRaisesRegex(UserAbort, "repair"):
      self.run_install(["--yes"])
    self.assertEqual(self.calls("useradd"), [])

  def test_unknown_admin_is_asked_again(self):
    self.runner.responses[("id", "-u", "nobody2")] = CommandResult(1)
    answers = "\n" * 7 + "nobody2\n\n\ny\n"
    self.assertEqual(self.run_install([], answers), install.EXIT_OK)
    self.assertIn("nobody2 が見つかりません", self.output())
    self.assertEqual(self.calls("usermod"), [])

  def test_missing_build_info(self):
    os.remove(self.source / "build-info.json")
    with self.assertRaisesRegex(InstanceError, "build-info.json"):
      self.run_install(["--yes"])
    self.assertEqual(self.runner.calls, [])


class CliInstallTest(InstallTestCase):
  def test_cli_routes_install_options(self):
    handler = functools.partial(
      install.handle,
      source=self.source,
      python=PYTHON,
      version_info=(3, 10, 12),
      wait=self.fake_wait,
    )
    ui = UI(stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO(), assume_yes=True)
    with unittest.mock.patch.dict(cli.HANDLERS, {"install": handler}):
      code = cli.main(
        ["install", "--yes", "--instance", "bot3", "--channel", "beta"],
        system=self.system,
        ui=ui,
      )
    self.assertEqual(code, cli.EXIT_OK, ui.stderr.getvalue())
    self.assertEqual(load_record(self.system, "bot3").channel, "beta")

  def test_install_is_registered(self):
    self.assertIs(cli.HANDLERS["install"], install.handle)


if __name__ == "__main__":
  unittest.main()
