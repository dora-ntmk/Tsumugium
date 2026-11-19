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


class OperatorUserIdsTest(unittest.TestCase):
  def test_one_or_more_ids(self):
    self.assertEqual(config._user_ids("222222222222222222"), (222222222222222222,))
    self.assertEqual(
      config._user_ids(" 222222222222222222 , 222222222222222223,"),
      (222222222222222222, 222222222222222223),
    )

  def test_empty_and_duplicates(self):
    for value in (None, "", " , "):
      with self.subTest(value=value):
        self.assertEqual(config._user_ids(value), ())
    self.assertEqual(
      config._user_ids("222222222222222222,222222222222222222"),
      (222222222222222222,),
    )

  def test_invalid_items_are_skipped_with_message(self):
    with unittest.mock.patch("builtins.print") as printed:
      ids = config._user_ids("abc,222222222222222222,-1")
    self.assertEqual(ids, (222222222222222222,))
    self.assertEqual(printed.call_count, 2)


class StreamingModeTest(unittest.TestCase):
  def test_on_values(self):
    for value in ("on", "ON", " true ", "1", "yes"):
      with self.subTest(value=value):
        self.assertEqual(config._streaming_mode(value), "on")

  def test_off_values(self):
    for value in ("off", "false", "0", "no"):
      with self.subTest(value=value):
        self.assertEqual(config._streaming_mode(value), "off")

  def test_auto_and_missing_follow_version_default(self):
    for value in ("auto", " AUTO ", "", None):
      for default in ("off", "hybrid"):
        with self.subTest(value=value, default=default):
          with unittest.mock.patch.object(config, "DEFAULT_VOICEVOX_STREAMING", default):
            self.assertEqual(config._streaming_mode(value), default)

  def test_hybrid(self):
    for value in ("hybrid", " Hybrid "):
      with self.subTest(value=value):
        self.assertEqual(config._streaming_mode(value), "hybrid")

  def test_unknown_value_is_off_with_message(self):
    with unittest.mock.patch("builtins.print") as printed:
      self.assertEqual(config._streaming_mode("enable"), "off")
    printed.assert_called_once()

  def test_streaming_is_off_and_segment_is_automatic_by_default(self):
    env_file = os.path.join(os.path.dirname(__file__), "no-such.env")
    with unittest.mock.patch.dict(os.environ, {"TSUMUGIUM_ENV_FILE": env_file}, clear=True):
      loaded = importlib.reload(config)
      self.assertEqual(loaded.VOICEVOX_STREAMING, "off")
      self.assertEqual(loaded.VOICEVOX_STREAM_SEGMENT, 0.0)
      self.assertEqual(loaded.VOICEVOX_STREAM_THRESHOLD, 0)
    importlib.reload(config)


class StreamSegmentTest(unittest.TestCase):
  def test_seconds(self):
    for value, expected in (
        ("0", 0.0), ("", 0.0), (None, 0.0), ("Auto", 0.0), ("0.5", 0.5), (" 2 ", 2.0),
    ):
      with self.subTest(value=value):
        self.assertEqual(config._stream_segment_seconds(value), expected)

  def test_invalid_or_negative_is_automatic_with_message(self):
    for value in ("fast", "-1", "nan", "inf"):
      with self.subTest(value=value):
        with unittest.mock.patch("builtins.print") as printed:
          self.assertEqual(config._stream_segment_seconds(value), 0.0)
        printed.assert_called_once()


class StreamThresholdTest(unittest.TestCase):
  def test_chars(self):
    for value, expected in (
        ("0", 0), ("", 0), (None, 0), ("auto", 0), ("50", 50), (" 120 ", 120),
    ):
      with self.subTest(value=value):
        self.assertEqual(config._stream_threshold_chars(value), expected)

  def test_invalid_or_negative_is_automatic_with_message(self):
    for value in ("many", "-1", "1.5"):
      with self.subTest(value=value):
        with unittest.mock.patch("builtins.print") as printed:
          self.assertEqual(config._stream_threshold_chars(value), 0)
        printed.assert_called_once()
