"""`.env.template` と `config.py` がずれていないことのテスト。"""

import re
import unittest
from pathlib import Path

from installer.envfile import EnvFile, managed_keys_in, parse_template, required_problems
from installer.instance import ENV_FILE_KEY, VOICEVOX_URL_KEY, Layout


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_TEXT = (ROOT / ".env.template").read_text(encoding="utf-8")
CONFIG_TEXT = (ROOT / "config.py").read_text(encoding="utf-8")

# config.py の os.getenv("名前") と os.getenv("名前", "既定値")
_GETENV_RE = re.compile(r'os\.getenv\("([A-Z_]+)"(?:,\s*"([^"]*)")?\)')
# テンプレートの「# 名前=値」（手で動かすとき用の例）
_COMMENTED_RE = re.compile(r"^#\s*([A-Z][A-Z0-9_]*)=(.*)$", re.MULTILINE)

# インストーラーが unit で渡し、利用者が書くことのない項目
INSTALLER_ONLY = {ENV_FILE_KEY, "UPDATE_STATE_DIR"}
# 値が既定値ではなく、書き方の見本になっている項目
SAMPLE_VALUES = {"DISCORD_BOT_TOKEN", "STATUS_MESSAGE"}
# テンプレートを改良する前の .env にあった項目（今までの .env で更新しても、何も足されないことを確かめる）
OLD_TEMPLATE_KEYS = [
  "STATUS_MESSAGE", "DISCORD_BOT_TOKEN", "OPERATOR_USER_ID",
  "SERVER_CONFIG_DB", "DICT_DB", "SOUND_BOARDS_DB", "USER_CONFIG_DB",
  "EMOJI_JA_JSON", "SPEAKERS_JSON", "DEFAULT_SPEAKER", "TMP_DIR", "VOICEVOX_URL",
  "BACKUP_DIR", "BACKUP_TIMES", "BACKUP_INTERVAL_DAYS", "BACKUP_KEEP",
]


def _config_default(name):
  match = re.search(rf'os\.getenv\("{name}"(?:,\s*"([^"]*)")?\)', CONFIG_TEXT)
  return None if match is None else match.group(1)


class EnvTemplateTest(unittest.TestCase):
  def setUp(self):
    self.entries = parse_template(TEMPLATE_TEXT)
    self.active = EnvFile.parse(TEMPLATE_TEXT).values()
    self.commented = dict(_COMMENTED_RE.findall(TEMPLATE_TEXT))
    self.config = {name: _config_default(name) for name, _ in _GETENV_RE.findall(CONFIG_TEXT)}

  def test_token_is_marked_required(self):
    required = [entry.key for entry in self.entries if entry.required]
    self.assertEqual(required, ["DISCORD_BOT_TOKEN"])
    block = next(entry.block for entry in self.entries if entry.key == "DISCORD_BOT_TOKEN")
    self.assertIn("# 必須", block)

  def test_installer_managed_keys_are_not_active(self):
    managed = set(Layout.of("/opt/tsumugium").bot_environment()) | {VOICEVOX_URL_KEY, ENV_FILE_KEY}
    self.assertEqual(managed & set(self.active), set())
    self.assertEqual(managed_keys_in(EnvFile.parse(TEMPLATE_TEXT), Layout.of("/opt/tsumugium")), [])

  def test_every_template_key_is_read_by_config(self):
    for key in list(self.active) + list(self.commented):
      with self.subTest(key=key):
        self.assertIn(key, self.config)

  def test_every_config_key_is_in_template(self):
    for key in self.config:
      if key in INSTALLER_ONLY:
        continue
      with self.subTest(key=key):
        self.assertTrue(key in self.active or key in self.commented, f"{key} が .env.template にありません")

  def test_values_match_config_defaults(self):
    for key, value in {**self.active, **self.commented}.items():
      if key in SAMPLE_VALUES or self.config.get(key) is None:
        continue
      with self.subTest(key=key):
        self.assertEqual(value.strip(), self.config[key])

  def test_backup_runs_daily_at_noon_by_default(self):
    self.assertEqual(self.config["BACKUP_TIMES"], "12:00")
    self.assertEqual(self.active["BACKUP_TIMES"], "12:00")

  def test_old_env_gets_nothing_added_on_upgrade(self):
    present = set(OLD_TEMPLATE_KEYS)
    missing = [entry.key for entry in self.entries if entry.key not in present]
    self.assertEqual(missing, [])

  def test_copied_template_passes_after_writing_token(self):
    env = EnvFile.parse(TEMPLATE_TEXT.replace(
      "DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN=dummy-token.for-tests.only"
    ))
    self.assertEqual(required_problems(env, self.entries), [])
    untouched = required_problems(EnvFile.parse(TEMPLATE_TEXT), self.entries)
    self.assertEqual(len(untouched), 1)
    self.assertIn("テンプレートのまま", untouched[0])


if __name__ == "__main__":
  unittest.main()
