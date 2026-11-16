"""config.py が TSUMUGIUM_ENV_FILE で指定した .env を読むことのテスト。"""

import importlib
import os
import unittest.mock

import config
from tests.support import TempDirTestCase


class EnvFileTest(TempDirTestCase):
  def tearDown(self):
    importlib.reload(config)
    super().tearDown()

  def test_specified_file_is_read_and_unit_values_win(self):
    env_file = self.tmp_path("instance.env")
    with open(env_file, "w", encoding="utf-8") as file:
      file.write("STATUS_MESSAGE=from-env-file\nSERVER_CONFIG_DB=db/config.db\n")
    with unittest.mock.patch.dict(os.environ, {
        "TSUMUGIUM_ENV_FILE": env_file,
        "SERVER_CONFIG_DB": "/opt/tsumugium/data/db/config.db",
    }, clear=True):
      loaded = importlib.reload(config)
      self.assertEqual(loaded.STATUS_MESSAGE, "from-env-file")
      self.assertEqual(loaded.SERVER_CONFIG_DB, "/opt/tsumugium/data/db/config.db")


class OnOffTest(unittest.TestCase):
  def test_on_values(self):
    for value in ("on", "ON", " true ", "1", "yes"):
      with self.subTest(value=value):
        self.assertTrue(config._on_off("VOICEVOX_STREAMING", value))

  def test_off_values_and_missing(self):
    for value in ("off", "false", "0", "no", "", None):
      with self.subTest(value=value):
        self.assertFalse(config._on_off("VOICEVOX_STREAMING", value))

  def test_unknown_value_is_off_with_message(self):
    with unittest.mock.patch("builtins.print") as printed:
      self.assertFalse(config._on_off("VOICEVOX_STREAMING", "enable"))
    printed.assert_called_once()

  def test_streaming_is_off_by_default(self):
    env_file = os.path.join(os.path.dirname(__file__), "no-such.env")
    with unittest.mock.patch.dict(os.environ, {"TSUMUGIUM_ENV_FILE": env_file}, clear=True):
      self.assertFalse(importlib.reload(config).VOICEVOX_STREAMING)
    importlib.reload(config)
