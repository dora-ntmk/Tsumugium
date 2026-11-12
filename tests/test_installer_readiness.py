"""Botの準備完了を待つ処理（installer/readiness.py）のテスト。"""

import json

from installer.instance import Layout
from installer.readiness import ReadyError, clear_ready, read_ready, wait_ready
from installer.system import CommandResult, RecordingRunner, System
from tests.support import TempDirTestCase


VERSION = "v4.2.0.70 (beta 9)"
SHOW_PID = ("systemctl", "show", "--property=MainPID", "--value", "tsumugium.service")
IS_ACTIVE = ("systemctl", "is-active")


class FakeClock:
  def __init__(self):
    self.now = 0.0
    self.sleeps = []
    self.on_sleep = None

  def __call__(self):
    return self.now

  def sleep(self, seconds):
    self.sleeps.append(seconds)
    self.now += seconds
    if self.on_sleep is not None:
      self.on_sleep(len(self.sleeps))


class ReadinessTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.runner.responses[SHOW_PID] = CommandResult(0, "4321\n")
    self.system = System(self.tmp_dir, self.runner, euid=0)
    self.layout = Layout.of("/opt/tsumugium")
    self.ready = self.system.path(str(self.layout.ready_file))
    self.ready.parent.mkdir(parents=True)
    self.clock = FakeClock()

  def write_ready(self, version=VERSION, pid=4321):
    self.ready.write_text(
      json.dumps({"version": version, "pid": pid, "ready_at": "2026-01-02T03:04:05+00:00"}),
      encoding="utf-8",
    )

  def wait(self, timeout=60):
    return wait_ready(
      self.system,
      self.layout,
      "tsumugium",
      VERSION,
      timeout=timeout,
      interval=1,
      clock=self.clock,
      sleep=self.clock.sleep,
    )

  def test_clear_ready(self):
    clear_ready(self.system, self.layout)
    self.write_ready()
    clear_ready(self.system, self.layout)
    self.assertFalse(self.ready.exists())

  def test_read_ready_ignores_broken_files(self):
    self.assertIsNone(read_ready(self.system, self.layout))
    for text in ("{broken", "[]", '{"version": "x"}', '{"version": "x", "pid": "abc"}'):
      with self.subTest(text=text):
        self.ready.write_text(text, encoding="utf-8")
        self.assertIsNone(read_ready(self.system, self.layout))

  def test_ready_immediately(self):
    self.write_ready()
    info = self.wait()
    self.assertEqual((info.version, info.pid), (VERSION, 4321))
    self.assertEqual(self.clock.sleeps, [])

  def test_ready_after_a_while(self):
    self.clock.on_sleep = lambda count: count == 5 and self.write_ready()
    self.assertEqual(self.wait().pid, 4321)
    self.assertEqual(len(self.clock.sleeps), 5)

  def test_file_from_old_process_is_not_accepted(self):
    self.write_ready(pid=1111)
    with self.assertRaisesRegex(ReadyError, "60秒"):
      self.wait()
    self.assertEqual(len(self.clock.sleeps), 60)

  def test_timeout(self):
    with self.assertRaisesRegex(ReadyError, "journalctl -u tsumugium.service"):
      self.wait(timeout=10)
    self.assertEqual(len(self.clock.sleeps), 10)

  def test_other_version_fails(self):
    self.write_ready(version="v4.2.0.69")
    with self.assertRaisesRegex(ReadyError, "v4.2.0.69"):
      self.wait()

  def test_stopped_service_fails_fast(self):
    def stop(count):
      if count == 3:
        self.runner.responses[IS_ACTIVE] = CommandResult(3)

    self.clock.on_sleep = stop
    with self.assertRaisesRegex(ReadyError, "止まりました"):
      self.wait()
    self.assertEqual(len(self.clock.sleeps), 3)
