"""インストーラーの骨組み（installer/cli.py、ui.py、system.py）のテスト。"""

import io
import unittest.mock
import os
import subprocess
import unittest
from pathlib import Path

from installer import cli
from installer.system import CommandError, CommandResult, RecordingRunner, System
from installer.ui import UI, UserAbort
from tests.support import TempDirTestCase


ROOT = Path(__file__).resolve().parent.parent


def make_ui(answers="", assume_yes=False):
  return UI(
    assume_yes=assume_yes,
    stdin=io.StringIO(answers),
    stdout=io.StringIO(),
    stderr=io.StringIO(),
  )


class CliTest(TempDirTestCase):
  def make_system(self, euid=0):
    return System(self.tmp_dir, RecordingRunner(), euid=euid)

  def test_all_modes_are_registered(self):
    self.assertEqual(
      set(cli.MODES),
      {"install", "upgrade", "repair", "rollback", "uninstall", "status", "start", "stop"},
    )
    self.assertEqual(set(cli.HANDLERS), set(cli.MODES))

  def test_unimplemented_mode_reports_error(self):
    ui = make_ui()
    code = cli.main(["repair"], system=self.make_system(), ui=ui)
    self.assertEqual(code, cli.EXIT_ERROR)
    self.assertIn("まだ実装されていません", ui.stderr.getvalue())

  def test_root_is_required(self):
    ui = make_ui()
    code = cli.main(["status"], system=self.make_system(euid=1000), ui=ui)
    self.assertEqual(code, cli.EXIT_ERROR)
    self.assertIn("root権限が必要です", ui.stderr.getvalue())

  def test_options_are_parsed(self):
    args = cli.build_parser().parse_args(["upgrade", "--instance", "beta", "--yes"])
    self.assertEqual((args.mode, args.instance, args.yes), ("upgrade", "beta", True))

  def test_mode_is_required(self):
    ui = make_ui()
    with unittest.mock.patch("sys.stderr", io.StringIO()):
      self.assertEqual(cli.main([], system=self.make_system(), ui=ui), cli.EXIT_USAGE)

  def test_handler_errors_become_exit_codes(self):
    def abort(args, system, ui):
      raise UserAbort("中止しました")

    def command_fails(args, system, ui):
      raise CommandError(["systemctl", "start", "x"], 1, "failed")

    for handler, message in ((abort, "中止しました"), (command_fails, "systemctl")):
      with self.subTest(handler=handler.__name__):
        ui = make_ui()
        with unittest.mock.patch.dict(cli.HANDLERS, {"status": handler}):
          code = cli.main(["status"], system=self.make_system(), ui=ui)
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn(message, ui.stderr.getvalue())


class UiTest(unittest.TestCase):
  def test_ask_uses_default_for_empty_answer(self):
    ui = make_ui("\nbeta\n")
    self.assertEqual(ui.ask("インスタンス名", "tsumugium"), "tsumugium")
    self.assertEqual(ui.ask("インスタンス名", "tsumugium"), "beta")
    self.assertIn("インスタンス名 [tsumugium]: ", ui.stdout.getvalue())

  def test_ask_repeats_until_valid(self):
    ui = make_ui("Bad Name\ngood\n")
    answer = ui.ask(
      "名前",
      "x",
      validate=lambda value: None if value.islower() and " " not in value else "小文字で",
    )
    self.assertEqual(answer, "good")
    self.assertIn("小文字で", ui.stderr.getvalue())

  def test_confirm(self):
    ui = make_ui("y\n\nいいえ\nmaybe\nn\n")
    self.assertTrue(ui.confirm("続けますか？"))
    self.assertTrue(ui.confirm("続けますか？", default=True))
    self.assertFalse(ui.confirm("続けますか？", default=True))
    self.assertFalse(ui.confirm("続けますか？", default=True))
    self.assertIn("y か n", ui.stderr.getvalue())

  def test_choose(self):
    ui = make_ui("alpha\nbeta\n")
    self.assertEqual(ui.choose("チャンネル", ["stable", "beta"], "stable"), "beta")

  def test_assume_yes_uses_defaults_without_reading(self):
    ui = make_ui(assume_yes=True)
    self.assertEqual(ui.ask("名前", "tsumugium"), "tsumugium")
    self.assertTrue(ui.confirm("続けますか？"))
    self.assertEqual(ui.stdout.getvalue(), "")

  def test_assume_yes_with_invalid_default_aborts(self):
    ui = make_ui(assume_yes=True)
    with self.assertRaises(UserAbort):
      ui.ask("名前", "", validate=lambda value: "空欄にできません" if not value else None)

  def test_end_of_input_aborts(self):
    with self.assertRaises(UserAbort):
      make_ui("").ask("名前", "x")


class SystemTest(TempDirTestCase):
  def test_path_is_inside_root(self):
    system = System(self.tmp_dir)
    self.assertEqual(
      system.path("/etc/tsumugium/a.json"),
      Path(self.tmp_dir, "etc", "tsumugium", "a.json"),
    )

  def test_relative_and_parent_paths_are_rejected(self):
    system = System(self.tmp_dir)
    for path in ("etc/x", "/opt/../etc/passwd"):
      with self.subTest(path=path):
        with self.assertRaises(ValueError):
          system.path(path)

  def test_recording_runner(self):
    runner = RecordingRunner(responses={
      ("systemctl", "is-active"): CommandResult(3, "inactive\n"),
    })
    system = System(self.tmp_dir, runner)
    self.assertEqual(system.run(["systemctl", "start", "x"]).returncode, 0)
    self.assertEqual(
      system.run(["systemctl", "is-active", "x"], check=False).stdout, "inactive\n"
    )
    with self.assertRaises(CommandError):
      system.run(["systemctl", "is-active", "x"])
    self.assertEqual(runner.calls[0], ["systemctl", "start", "x"])

  def test_real_runner_raises_on_failure(self):
    system = System(self.tmp_dir)
    self.assertEqual(system.run(["true"]).returncode, 0)
    with self.assertRaises(CommandError):
      system.run(["false"])


class InstallShTest(TempDirTestCase):
  def test_install_sh_runs_installer(self):
    script = ROOT / "install.sh"
    self.assertTrue(os.access(script, os.X_OK), "install.sh に実行権限がありません")
    result = subprocess.run(
      [str(script), "--help"],
      capture_output=True,
      text=True,
      cwd=self.tmp_dir,
    )
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("tsumugium-setup", result.stdout)


if __name__ == "__main__":
  unittest.main()
