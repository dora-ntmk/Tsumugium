"""インストール前の確認（installer/checks.py）のテスト。"""

import http.server
import io
import socket
import threading
import unittest
import unittest.mock

from installer import cli
from installer.checks import (
  CheckError,
  VoicevoxResult,
  check_os,
  check_python,
  check_systemd,
  check_voicevox_url,
  confirm_voicevox,
  missing_packages,
  probe_voicevox,
  run_preflight,
  venv_package,
)
from installer.system import CommandResult, RecordingRunner, System
from installer.ui import UI, UserAbort
from tests.support import TempDirTestCase


PYTHON = "/usr/bin/python3"
FFMPEG_QUERY = ("dpkg-query", "-W", "-f=${Status}", "ffmpeg")
ENSUREPIP = (PYTHON, "-c", "import ensurepip")

UBUNTU_2204 = 'PRETTY_NAME="Ubuntu 22.04.4 LTS"\nNAME="Ubuntu"\nVERSION_ID="22.04"\nID=ubuntu\nID_LIKE=debian\n'
DEBIAN_12 = 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nVERSION_ID="12"\nID=debian\n'


def make_ui(answers="", assume_yes=False):
  return UI(
    assume_yes=assume_yes,
    stdin=io.StringIO(answers),
    stdout=io.StringIO(),
    stderr=io.StringIO(),
  )


class SystemTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.runner = RecordingRunner()
    self.system = System(self.tmp_dir, self.runner, euid=0)

  def write(self, absolute, text=""):
    path = self.system.path(absolute)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

  def make_systemd(self):
    self.system.path("/run/systemd/system").mkdir(parents=True)

  def packages_present(self):
    self.runner.responses[FFMPEG_QUERY] = CommandResult(0, "install ok installed")

  def packages_missing(self):
    self.runner.responses[ENSUREPIP] = CommandResult(1, "", "No module named 'ensurepip'")
    self.runner.responses[FFMPEG_QUERY] = CommandResult(1, "", "no packages found")


class OsTest(SystemTestCase):
  def test_supported_versions(self):
    for text, os_id, version in (
        (UBUNTU_2204, "ubuntu", "22.04"),
        ('ID=ubuntu\nVERSION_ID="24.04"\n', "ubuntu", "24.04"),
        (DEBIAN_12, "debian", "12"),
        ("ID=debian\nVERSION_ID=13\n", "debian", "13"),
    ):
      with self.subTest(text=text):
        self.write("/etc/os-release", text)
        info = check_os(self.system)
        self.assertEqual((info.id, info.version), (os_id, version))

  def test_old_versions_are_rejected(self):
    for text in ('ID=ubuntu\nVERSION_ID="20.04"\n', 'ID=debian\nVERSION_ID="11"\n'):
      with self.subTest(text=text):
        self.write("/etc/os-release", text)
        with self.assertRaisesRegex(CheckError, "古いため"):
          check_os(self.system)

  def test_other_os_is_rejected(self):
    for text in ('ID=fedora\nVERSION_ID="40"\n', 'ID=linuxmint\nID_LIKE="ubuntu debian"\nVERSION_ID="21.3"\n'):
      with self.subTest(text=text):
        self.write("/etc/os-release", text)
        with self.assertRaisesRegex(CheckError, "対応していません"):
          check_os(self.system)

  def test_unknown_version_is_rejected(self):
    self.write("/etc/os-release", "ID=debian\n")
    with self.assertRaises(CheckError):
      check_os(self.system)

  def test_usr_lib_fallback(self):
    self.write("/usr/lib/os-release", DEBIAN_12)
    self.assertEqual(check_os(self.system).id, "debian")

  def test_missing_file(self):
    with self.assertRaisesRegex(CheckError, "os-release"):
      check_os(self.system)


class SystemdAndPythonTest(SystemTestCase):
  def test_systemd(self):
    with self.assertRaisesRegex(CheckError, "systemd"):
      check_systemd(self.system)
    self.make_systemd()
    check_systemd(self.system)

  def test_python(self):
    check_python((3, 10, 0))
    check_python((3, 13, 1))
    with self.assertRaisesRegex(CheckError, "3.9.18"):
      check_python((3, 9, 18))


class PackageTest(SystemTestCase):
  def test_venv_package_name_follows_python(self):
    self.assertEqual(venv_package((3, 10)), "python3.10-venv")
    self.assertEqual(venv_package((3, 12)), "python3.12-venv")

  def test_nothing_missing(self):
    self.packages_present()
    self.assertEqual(missing_packages(self.system, python=PYTHON, version_info=(3, 10)), [])

  def test_missing_packages(self):
    self.packages_missing()
    self.assertEqual(
      missing_packages(self.system, python=PYTHON, version_info=(3, 10)),
      ["python3.10-venv", "ffmpeg"],
    )

  def test_half_installed_ffmpeg_counts_as_missing(self):
    self.runner.responses[FFMPEG_QUERY] = CommandResult(0, "deinstall ok config-files")
    self.assertEqual(missing_packages(self.system, python=PYTHON, version_info=(3, 10)), ["ffmpeg"])


class PreflightTest(SystemTestCase):
  def setUp(self):
    super().setUp()
    self.write("/etc/os-release", UBUNTU_2204)
    self.make_systemd()

  def preflight(self, ui):
    return run_preflight(self.system, ui, python=PYTHON, version_info=(3, 10, 12))

  def apt_calls(self):
    return [call for call in self.runner.calls if call[0] == "apt-get"]

  def test_all_present(self):
    self.packages_present()
    ui = make_ui()
    result = self.preflight(ui)
    self.assertEqual(result.os.id, "ubuntu")
    self.assertEqual(result.installed_packages, [])
    self.assertEqual(self.apt_calls(), [])
    self.assertIn("すべて入っています", ui.stdout.getvalue())

  def test_missing_packages_are_installed(self):
    self.packages_missing()
    ui = make_ui("\n")
    with unittest.mock.patch.object(self.runner, "run", wraps=self.runner.run) as run:
      result = self.preflight(ui)
    self.assertEqual(result.installed_packages, ["python3.10-venv", "ffmpeg"])
    self.assertEqual(self.apt_calls(), [
      ["apt-get", "update"],
      ["apt-get", "install", "-y", "--no-install-recommends", "python3.10-venv", "ffmpeg"],
    ])
    for call in run.call_args_list:
      if call.args[0][0] == "apt-get":
        self.assertEqual(call.kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")
    self.assertIn("入れました: python3.10-venv ffmpeg", ui.stdout.getvalue())
    # 時間のかかる apt は、進み具合が見えるように出力を画面に流す
    self.assertEqual(self.runner.streamed, self.apt_calls())
    self.assertIn("数分かかることがあります", ui.stdout.getvalue())

  def test_yes_installs_without_asking(self):
    self.packages_missing()
    self.preflight(make_ui(assume_yes=True))
    self.assertEqual(len(self.apt_calls()), 2)

  def test_declining_install_aborts(self):
    self.packages_missing()
    with self.assertRaises(UserAbort):
      self.preflight(make_ui("n\n"))
    self.assertEqual(self.apt_calls(), [])

  def test_unsupported_os_stops_before_installing(self):
    self.write("/etc/os-release", 'ID=fedora\nVERSION_ID="40"\n')
    self.packages_missing()
    with self.assertRaises(CheckError):
      self.preflight(make_ui(assume_yes=True))
    self.assertEqual(self.runner.calls, [])


class _VersionHandler(http.server.BaseHTTPRequestHandler):
  body = b'"0.14.10"'
  status = 200

  def do_GET(self):
    if self.path != "/version":
      self.send_response(404)
      self.end_headers()
      return
    self.send_response(self.status)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(self.body)

  def log_message(self, *args):
    pass


class LocalServer:
  def __init__(self, body=b'"0.14.10"', status=200):
    handler = type("Handler", (_VersionHandler,), {"body": body, "status": status})
    self.server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
    self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

  def __enter__(self):
    self.thread.start()
    return self

  def __exit__(self, *exc_info):
    self.server.shutdown()
    self.server.server_close()
    self.thread.join()


def closed_port_url():
  with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
  return f"http://127.0.0.1:{port}"


class VoicevoxTest(unittest.TestCase):
  def test_url_validation(self):
    self.assertIsNone(check_voicevox_url("http://127.0.0.1:50021"))
    self.assertIsNone(check_voicevox_url("https://voicevox.example.internal"))
    for value in ("127.0.0.1:50021", "ftp://127.0.0.1", "http://", "http://host/?a=1"):
      with self.subTest(value=value):
        self.assertIsNotNone(check_voicevox_url(value))

  def test_probe_returns_version(self):
    with LocalServer() as server:
      self.assertEqual(probe_voicevox(server.url + "/", timeout=2), "0.14.10")

  def test_probe_errors(self):
    with LocalServer(status=500) as server:
      with self.assertRaisesRegex(CheckError, "500"):
        probe_voicevox(server.url, timeout=2)
    with LocalServer(body=b"<html>") as server:
      with self.assertRaisesRegex(CheckError, "VOICEVOXのものではありません"):
        probe_voicevox(server.url, timeout=2)
    with self.assertRaisesRegex(CheckError, "接続できません"):
      probe_voicevox(closed_port_url(), timeout=2)

  def test_confirm_with_default_url(self):
    with LocalServer() as server:
      ui = make_ui("\n")
      result = confirm_voicevox(ui, server.url, timeout=2)
    self.assertEqual(result, VoicevoxResult(server.url, "0.14.10"))
    self.assertIn("0.14.10", ui.stdout.getvalue())

  def test_confirm_asks_again_for_invalid_url(self):
    with LocalServer() as server:
      ui = make_ui(f"localhost\n{server.url}/\n")
      result = confirm_voicevox(ui, "http://127.0.0.1:50021", timeout=2)
    self.assertEqual(result.url, server.url)
    self.assertIn("http://", ui.stderr.getvalue())

  def test_retry_after_starting_voicevox(self):
    with LocalServer() as server:
      url = server.url
      calls = []
      real_probe = probe_voicevox

      def probe(target, timeout):
        calls.append(target)
        if len(calls) == 1:
          raise CheckError("接続できません")
        return real_probe(target, timeout)

      with unittest.mock.patch("installer.checks.probe_voicevox", probe):
        result = confirm_voicevox(make_ui("\nretry\n"), url, timeout=2)
    self.assertEqual(result.version, "0.14.10")
    self.assertEqual(len(calls), 2)

  def test_skip_installs_without_starting(self):
    url = closed_port_url()
    ui = make_ui("\nskip\n")
    self.assertEqual(confirm_voicevox(ui, url, timeout=2), VoicevoxResult(url, None))
    self.assertIn("接続できません", ui.stderr.getvalue())

  def test_abort(self):
    with self.assertRaises(UserAbort):
      confirm_voicevox(make_ui("\nabort\n"), closed_port_url(), timeout=2)

  def test_yes_stops_when_unreachable(self):
    with self.assertRaisesRegex(CheckError, "起動してから"):
      confirm_voicevox(make_ui(assume_yes=True), closed_port_url(), timeout=2)


class CliCheckErrorTest(TempDirTestCase):
  def test_check_errors_become_exit_codes(self):
    def handler(args, system, ui):
      raise CheckError("systemd で動いていないため、サービスとして登録できません")

    ui = make_ui()
    with unittest.mock.patch.dict(cli.HANDLERS, {"install": handler}):
      code = cli.main(["install"], system=System(self.tmp_dir, euid=0), ui=ui)
    self.assertEqual(code, cli.EXIT_ERROR)
    self.assertIn("systemd", ui.stderr.getvalue())


if __name__ == "__main__":
  unittest.main()
