"""起動の準備完了の知らせ（services/ready_state_service.py と on_ready）のテスト。"""

import datetime
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from cogs.lifecycle_cog import LifecycleCog
from installer.instance import Layout
from services.ready_state_service import READY_FILE_NAME, ReadyStateService
from tests.support import DUMMY_GUILD_ID, RecordingNotifier, TempDirTestCase


FIXED_NOW = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)


class ReadyStateServiceTest(TempDirTestCase):
  def make(self, state_dir=None):
    return ReadyStateService(
      self.tmp_dir if state_dir is None else state_dir,
      "v4.2.0.70 (beta 9)",
      pid=12345,
      now=lambda: FIXED_NOW,
    )

  def test_mark_ready_writes_file(self):
    service = self.make()
    self.assertTrue(service.mark_ready())
    with open(self.tmp_path(READY_FILE_NAME), encoding="utf-8") as file:
      data = json.load(file)
    self.assertEqual(data, {
      "version": "v4.2.0.70 (beta 9)",
      "pid": 12345,
      "ready_at": "2026-01-02T03:04:05+00:00",
    })
    self.assertEqual(os.listdir(self.tmp_dir), [READY_FILE_NAME])

  def test_close_removes_file(self):
    service = self.make()
    service.close()
    service.mark_ready()
    service.close()
    self.assertEqual(os.listdir(self.tmp_dir), [])

  def test_disabled_when_directory_is_empty(self):
    service = self.make(state_dir="")
    self.assertFalse(service.enabled)
    self.assertFalse(service.mark_ready())
    service.close()
    self.assertEqual(os.listdir(self.tmp_dir), [])

  def test_missing_directory_is_created(self):
    service = self.make(self.tmp_path("state"))
    self.assertTrue(service.mark_ready())
    self.assertTrue(os.path.isfile(self.tmp_path("state", READY_FILE_NAME)))

  def test_write_failure_does_not_raise(self):
    blocker = self.tmp_path("not-a-directory")
    with open(blocker, "w", encoding="utf-8") as file:
      file.write("x")
    with mock.patch("builtins.print") as printed:
      self.assertFalse(self.make(blocker).mark_ready())
    self.assertIn("ready.json", printed.call_args.args[0])

  def test_file_name_matches_installer(self):
    self.assertEqual(Layout.of("/opt/tsumugium").ready_file.name, READY_FILE_NAME)


class _Client:
  def __init__(self):
    self.events = {}
    self.guilds = [SimpleNamespace(id=DUMMY_GUILD_ID)]

  def event(self, handler):
    self.events[handler.__name__] = handler
    return handler


class OnReadyTest(TempDirTestCase, unittest.IsolatedAsyncioTestCase):
  async def test_ready_is_marked_after_startup_work(self):
    order = []
    client = _Client()
    tree = SimpleNamespace(sync=mock.AsyncMock(side_effect=lambda: order.append("sync")))
    server_config = mock.MagicMock()
    server_config.get_all_guild_ids.return_value = {str(DUMMY_GUILD_ID)}
    sound_boards = SimpleNamespace(refresh=mock.AsyncMock())
    status_service = SimpleNamespace(
      update=mock.AsyncMock(side_effect=lambda: order.append("status")),
    )
    ready_state = ReadyStateService(self.tmp_dir, "v4.2.0.70 (beta 9)")
    original = ready_state.mark_ready

    def mark_ready():
      order.append("ready")
      return original()

    ready_state.mark_ready = mark_ready
    LifecycleCog(
      client,
      tree,
      server_config,
      dict_manager=mock.MagicMock(),
      sound_boards=sound_boards,
      backup_databases=[],
      status_service=status_service,
      error_notifier=RecordingNotifier(),
      ready_state=ready_state,
    )
    with mock.patch("cogs.lifecycle_cog.start_backup") as start_backup, \
        mock.patch("builtins.print"):
      await client.events["on_ready"]()
    start_backup.assert_called_once()
    self.assertEqual(order, ["sync", "status", "ready"])
    self.assertTrue(os.path.isfile(self.tmp_path(READY_FILE_NAME)))

  async def test_without_ready_state(self):
    client = _Client()
    LifecycleCog(
      client,
      SimpleNamespace(sync=mock.AsyncMock()),
      mock.MagicMock(**{"get_all_guild_ids.return_value": set()}),
      dict_manager=mock.MagicMock(),
      sound_boards=SimpleNamespace(refresh=mock.AsyncMock()),
      backup_databases=[],
      status_service=SimpleNamespace(update=mock.AsyncMock()),
      error_notifier=RecordingNotifier(),
    )
    with mock.patch("cogs.lifecycle_cog.start_backup"), mock.patch("builtins.print"):
      await client.events["on_ready"]()


if __name__ == "__main__":
  unittest.main()
