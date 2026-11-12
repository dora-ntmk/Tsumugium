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
