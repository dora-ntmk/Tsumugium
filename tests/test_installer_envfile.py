"""`.env` の扱い（installer/envfile.py）のテスト。"""

import io
import os
import unittest
import unittest.mock
from pathlib import Path

from installer import cli
from installer.envfile import (
  EnvError,
  EnvFile,
  check_token,
  install_env,
  managed_keys_in,
  parse_template,
  required_problems,
  upgrade_env,
)
from installer.instance import Layout
from installer.system import RecordingRunner, System
from installer.ui import UI
from tests.support import DUMMY_TOKEN, TempDirTestCase


# Discordのトークンと同じく「.」で区切った3つの部分からなる、明らかな偽物
SHAPED_TOKEN = "dummy-token.for-tests.only"

TEMPLATE = """\
# ステータスメッセージ
STATUS_MESSAGE="{guilds}個"

# 必須
# DiscordBotのトークン
DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN

# データベース
SERVER_CONFIG_DB=db/config.db

# VOICEVOXのURL
VOICEVOX_URL=http://127.0.0.1:50021
"""


def make_ui(assume_yes=False):
  return UI(
    assume_yes=assume_yes,
    stdin=io.StringIO(""),
    stdout=io.StringIO(),
    stderr=io.StringIO(),
  )


class EnvFileTest(unittest.TestCase):
  def test_values(self):
    env = EnvFile.parse(
      "# comment\n"
      "A=1\n"
      "export B=two\n"
      'C="quoted # not comment"\n'
      "D='single'\n"
      "E=plain # comment\n"
      "F=\n"
      "  G = spaced  \n"
      "# H=commented\n"
      "A=again\n"
      'I="with \\"escaped\\" quote"\n'
    )
    self.assertEqual(env.values(), {
      "A": "again",
      "B": "two",
      "C": "quoted # not comment",
      "D": "single",
      "E": "plain",
      "F": "",
      "G": "spaced",
      "I": 'with "escaped" quote',
    })

  def test_text_is_kept_as_is(self):
    text = "# comment\n\nexport A=1\nB = 2 # note\n"
    self.assertEqual(EnvFile.parse(text).text(), text)

  def test_append_keeps_existing_lines(self):
    env = EnvFile.parse("A=1\n")
    env.append([["# new", "B=2"]], "# added")
    self.assertEqual(env.text(), "A=1\n\n# added\n# new\nB=2\n")


class TemplateTest(unittest.TestCase):
  def test_entries_and_required_mark(self):
    entries = parse_template(TEMPLATE)
    self.assertEqual(
      [(entry.key, entry.required) for entry in entries],
      [
        ("STATUS_MESSAGE", False),
        ("DISCORD_BOT_TOKEN", True),
        ("SERVER_CONFIG_DB", False),
        ("VOICEVOX_URL", False),
      ],
    )
    self.assertEqual(
      entries[1].block,
      ("# 必須", "# DiscordBotのトークン", "DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN"),
    )

  def test_token_is_required_without_mark(self):
    entries = parse_template("# トークン\nDISCORD_BOT_TOKEN=\n# 必須ではない\nX=1\n")
    self.assertEqual([(e.key, e.required) for e in entries], [("DISCORD_BOT_TOKEN", True), ("X", False)])

  def test_repository_template_can_be_parsed(self):
    root = Path(__file__).resolve().parent.parent
    keys = [entry.key for entry in parse_template((root / ".env.template").read_text(encoding="utf-8"))]
    self.assertIn("DISCORD_BOT_TOKEN", keys)
    self.assertIn("VOICEVOX_URL", keys)


class TokenTest(unittest.TestCase):
  def test_valid(self):
    self.assertIsNone(check_token(SHAPED_TOKEN))

  def test_invalid(self):
    for value, message in (
        (None, "空欄"),
        ("", "空欄"),
        ("YOUR_DISCORD_BOT_TOKEN", "テンプレートのまま"),
        (DUMMY_TOKEN, "形"),
        ("a.b", "形"),
        ("a.b.c d", "形"),
    ):
      with self.subTest(value=value):
        self.assertIn(message, check_token(value))

  def test_required_problems(self):
    template = parse_template("# 必須\nA=\nDISCORD_BOT_TOKEN=\nB=\n")
    self.assertEqual(
      required_problems(EnvFile.parse(f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\nA=x\n"), template),
      [],
    )
    problems = required_problems(EnvFile.parse("A=\n"), template)
    self.assertEqual(len(problems), 2)
    self.assertIn("A が空欄です", problems)

  def test_managed_keys(self):
    env = EnvFile.parse("SERVER_CONFIG_DB=db/config.db\nDICT_DB=\nVOICEVOX_URL=http://x\n")
    self.assertEqual(managed_keys_in(env, Layout.of("/opt/tsumugium")), ["SERVER_CONFIG_DB"])


class EnvTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.system = System(Path(self.tmp_dir) / "root", self.runner, euid=0)
    self.layout = Layout.of("/opt/tsumugium")
    self.target = self.system.path("/opt/tsumugium/.env")
    self.extracted = Path(self.tmp_dir) / "extracted"
    self.extracted.mkdir()
    self.template = self.extracted / ".env.template"
    self.template.write_text(TEMPLATE, encoding="utf-8")

  def chowns(self):
    return [call for call in self.runner.calls if call[0] == "chown"]


class InstallEnvTest(EnvTestCase):
  def install(self, ui=None):
    return install_env(self.system, ui or make_ui(), self.layout, self.extracted, "tsumugium")

  def test_missing_env_shows_steps(self):
    with self.assertRaises(EnvError) as context:
      self.install()
    message = str(context.exception)
    self.assertIn("見つかりません", message)
    self.assertIn(f"sudo cp {self.extracted}/.env.template /opt/tsumugium/.env", message)
    self.assertFalse(self.target.exists())

  def test_env_in_extracted_directory_is_copied(self):
    (self.extracted / ".env").write_text(f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\n", encoding="utf-8")
    env = self.install()
    self.assertEqual(env.get("DISCORD_BOT_TOKEN"), SHAPED_TOKEN)
    self.assertEqual(self.target.read_text(encoding="utf-8"), f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\n")
    self.assertEqual(oct(os.stat(self.target).st_mode & 0o777), "0o600")
    self.assertEqual(self.chowns(), [["chown", "tsumugium:tsumugium", "/opt/tsumugium/.env"]])
    self.assertEqual(os.listdir(self.target.parent), [".env"])

  def test_env_in_prefix_comes_first(self):
    self.target.parent.mkdir(parents=True)
    self.target.write_text(f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\n", encoding="utf-8")
    os.chmod(self.target, 0o644)
    (self.extracted / ".env").write_text("DISCORD_BOT_TOKEN=\n", encoding="utf-8")
    self.install()
    self.assertEqual(oct(os.stat(self.target).st_mode & 0o777), "0o600")
    self.assertEqual(self.chowns(), [["chown", "tsumugium:tsumugium", "/opt/tsumugium/.env"]])

  def test_invalid_token_stops_without_copying(self):
    (self.extracted / ".env").write_text("DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN\n", encoding="utf-8")
    with self.assertRaisesRegex(EnvError, "テンプレートのまま"):
      self.install()
    self.assertFalse(self.target.exists())
    self.assertEqual(self.chowns(), [])

  def test_values_are_not_printed(self):
    (self.extracted / ".env").write_text(
      f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\nSERVER_CONFIG_DB=db/config.db\n", encoding="utf-8"
    )
    ui = make_ui()
    self.install(ui)
    output = ui.stdout.getvalue() + ui.stderr.getvalue()
    self.assertNotIn(SHAPED_TOKEN, output)
    self.assertIn("SERVER_CONFIG_DB", ui.stderr.getvalue())
    self.assertIn("使われません", ui.stderr.getvalue())


class UpgradeEnvTest(EnvTestCase):
  def setUp(self):
    super().setUp()
    self.target.parent.mkdir(parents=True)
    self.backup = self.system.path("/opt/tsumugium/.env.bak")

  def upgrade(self, ui=None):
    return upgrade_env(
      self.system, ui or make_ui(), self.layout, self.template, "tsumugium", "v4.2.0.71"
    )

  def write_env(self, text):
    self.target.write_text(text, encoding="utf-8")
    os.chmod(self.target, 0o600)

  def test_missing_keys_are_appended(self):
    original = f"# 自分のメモ\nDISCORD_BOT_TOKEN={SHAPED_TOKEN}\nSTATUS_MESSAGE=custom\n"
    self.write_env(original)
    ui = make_ui()
    added = self.upgrade(ui)
    self.assertEqual(added, ["SERVER_CONFIG_DB", "VOICEVOX_URL"])
    self.assertEqual(
      self.target.read_text(encoding="utf-8"),
      original
      + "\n# ↓ tsumugium-setup が v4.2.0.71 への更新で追加した項目\n"
      + "# データベース\nSERVER_CONFIG_DB=db/config.db\n"
      + "# VOICEVOXのURL\nVOICEVOX_URL=http://127.0.0.1:50021\n",
    )
    self.assertEqual(self.backup.read_text(encoding="utf-8"), original)
    for path in (self.target, self.backup):
      self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
    self.assertEqual(len(self.chowns()), 2)
    self.assertIn("VOICEVOX_URL", ui.stdout.getvalue())

  def test_nothing_to_add(self):
    text = (
      f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\nSTATUS_MESSAGE=x\n"
      "SERVER_CONFIG_DB=\nVOICEVOX_URL=http://127.0.0.1:50021\n"
    )
    self.write_env(text)
    self.assertEqual(self.upgrade(), [])
    self.assertEqual(self.target.read_text(encoding="utf-8"), text)
    self.assertFalse(self.backup.exists())
    self.assertEqual(self.chowns(), [])

  def test_added_required_key_stops_update(self):
    self.template.write_text(TEMPLATE + "\n# 必須\nNEW_REQUIRED=\n", encoding="utf-8")
    self.write_env(
      f"DISCORD_BOT_TOKEN={SHAPED_TOKEN}\nSTATUS_MESSAGE=x\n"
      "SERVER_CONFIG_DB=\nVOICEVOX_URL=http://127.0.0.1:50021\n"
    )
    with self.assertRaisesRegex(EnvError, "NEW_REQUIRED が空欄です") as context:
      self.upgrade()
    self.assertIn("更新を止めます", str(context.exception))
    # 足した項目は残し、利用者が値を書けば次の実行で通る
    self.assertIn("NEW_REQUIRED=", self.target.read_text(encoding="utf-8"))
    self.assertTrue(self.backup.exists())

  def test_broken_token_stops_update(self):
    self.write_env("DISCORD_BOT_TOKEN=\n")
    with self.assertRaisesRegex(EnvError, "空欄"):
      self.upgrade()

  def test_missing_env(self):
    with self.assertRaisesRegex(EnvError, "見つかりません"):
      self.upgrade()


class CliEnvErrorTest(TempDirTestCase):
  def test_env_errors_become_exit_codes(self):
    def handler(args, system, ui):
      raise EnvError(".env が見つかりません")

    ui = make_ui()
    with unittest.mock.patch.dict(cli.HANDLERS, {"install": handler}):
      code = cli.main(["install"], system=System(self.tmp_dir, euid=0), ui=ui)
    self.assertEqual(code, cli.EXIT_ERROR)
    self.assertIn(".env", ui.stderr.getvalue())


if __name__ == "__main__":
  unittest.main()
