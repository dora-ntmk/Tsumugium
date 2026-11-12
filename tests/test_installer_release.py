"""版の選択・取得・照合・展開（installer/release.py）のテスト。

GitHubの代わりに、テスト内で 127.0.0.1 に立てたサーバーを使う。
"""

import hashlib
import http.server
import io
import json
import os
import threading
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from installer.instance import Layout
from installer.release import (
  GitHubClient,
  ReleaseError,
  Version,
  check_direction,
  extract_from_file,
  extract_release,
  fetch_release,
  parse_sha256,
  select_release,
)
from installer.system import System
from installer.ui import UI
from tests.support import TempDirTestCase


REPO = "example/Tsumugium"


def make_ui():
  return UI(stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())


def build_zip(tag, version=None, *, extra=None, top=None):
  """build_release.py と同じ形の配布用zipを作る。"""
  top = top or f"tsumugium-{tag}"
  buffer = io.BytesIO()
  with zipfile.ZipFile(buffer, "w") as archive:
    def add(name, data, mode=0o644):
      info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
      info.external_attr = mode << 16
      archive.writestr(info, data)
    add(f"{top}/main.py", "print('bot')\n")
    add(f"{top}/install.sh", "#!/bin/sh\n", 0o755)
    add(f"{top}/db/emoji_ja.json", "{}\n")
    add(f"{top}/build-info.json", json.dumps({"version": version or str(Version.parse(tag)), "tag": tag}))
    for name, data, mode in extra or []:
      add(name, data, mode)
  return buffer.getvalue()


def sha_line(data, name):
  return f"{hashlib.sha256(data).hexdigest()}  {name}\n"


class FakeGitHub:
  """/repos/<repo>/releases と、ダウンロード用のURLを返すサーバー。"""

  def __init__(self):
    self.routes = {}
    self.requests = []
    owner = self

    class Handler(http.server.BaseHTTPRequestHandler):
      def do_GET(self):
        owner.requests.append((self.path, self.headers.get("User-Agent")))
        if self.path not in owner.routes:
          self.send_response(404)
          self.end_headers()
          return
        status, body = owner.routes[self.path]
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

      def log_message(self, *args):
        pass

    self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
    self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

  def __enter__(self):
    self.thread.start()
    return self

  def __exit__(self, *exc_info):
    self.server.shutdown()
    self.server.server_close()
    self.thread.join()

  def add_release(self, tag, *, prerelease=False, draft=False, data=None, sha=None):
    name = f"tsumugium-{tag}.zip"
    data = build_zip(tag) if data is None else data
    self.routes[f"/download/{name}"] = (200, data)
    self.routes[f"/download/{name}.sha256"] = (200, (sha or sha_line(data, name)).encode())
    release = {
      "tag_name": tag,
      "prerelease": prerelease,
      "draft": draft,
      "assets": [
        {"name": name, "browser_download_url": f"{self.url}/download/{name}"},
        {"name": name + ".sha256", "browser_download_url": f"{self.url}/download/{name}.sha256"},
      ],
    }
    self.routes[f"/repos/{REPO}/releases/tags/{tag}"] = (200, json.dumps(release).encode())
    releases = json.loads(self.routes.get(f"/repos/{REPO}/releases?per_page=100", (200, b"[]"))[1])
    releases.append(release)
    self.routes[f"/repos/{REPO}/releases?per_page=100"] = (200, json.dumps(releases).encode())
    return data

  def client(self):
    return GitHubClient(self.url, allow_http=True, timeout=5)


class VersionTest(unittest.TestCase):
  def test_both_spellings_are_the_same(self):
    self.assertEqual(Version.parse("v4.2.0.70 (beta 9)"), Version.parse("v4.2.0.70-beta.9"))
    self.assertEqual(Version.parse("v4.2.0.70-beta.9").tag, "v4.2.0.70-beta.9")
    self.assertEqual(str(Version.parse("v4.2.0.70-beta.9")), "v4.2.0.70 (beta 9)")
    self.assertEqual(Version.parse("v4.2.0.70").tag, "v4.2.0.70")

  def test_ordering(self):
    ordered = [
      "v4.1.0.69",
      "v4.2.0.70 (beta 1)",
      "v4.2.0.70 (beta 2)",
      "v4.2.0.70 (beta 10)",
      "v4.2.0.70",
      "v4.2.1.71 (beta 1)",
      "v4.10.0.80",
    ]
    versions = [Version.parse(text) for text in ordered]
    self.assertEqual(sorted(reversed(versions)), versions)

  def test_invalid(self):
    for text in ("4.2.0.70", "v4.2.0", "v4.2.0.70 (beta 0)", "v4.2.0.70-beta", "latest"):
      with self.subTest(text=text):
        with self.assertRaises(ReleaseError):
          Version.parse(text)

  def test_direction(self):
    target = Version.parse("v4.2.0.71")
    self.assertEqual(check_direction(None, target, allow_downgrade=False), "newer")
    self.assertEqual(check_direction("v4.2.0.70", target, allow_downgrade=False), "newer")
    self.assertEqual(check_direction("v4.2.0.71", target, allow_downgrade=False), "same")
    with self.assertRaisesRegex(ReleaseError, "--allow-downgrade"):
      check_direction("v4.2.0.72", target, allow_downgrade=False)
    self.assertEqual(check_direction("v4.2.0.72", target, allow_downgrade=True), "older")


class SelectReleaseTest(unittest.TestCase):
  def test_channels(self):
    with FakeGitHub() as github:
      github.add_release("v4.2.0.70")
      github.add_release("v4.2.0.71-beta.3", prerelease=True)
      github.add_release("v4.2.0.69")
      github.add_release("v4.2.0.72", draft=True)
      github.add_release("not-a-version", data=build_zip("v0.0.0.1", top="tsumugium-not-a-version"))
      client = github.client()
      self.assertEqual(select_release(client, REPO, "stable").tag, "v4.2.0.70")
      self.assertEqual(select_release(client, REPO, "beta").tag, "v4.2.0.71-beta.3")
      self.assertEqual(github.requests[0][1], "tsumugium-setup")

  def test_beta_follows_stable_after_release(self):
    with FakeGitHub() as github:
      github.add_release("v4.2.0.71-beta.3", prerelease=True)
      github.add_release("v4.2.0.71")
      self.assertEqual(select_release(github.client(), REPO, "beta").tag, "v4.2.0.71")

  def test_requested_version(self):
    with FakeGitHub() as github:
      github.add_release("v4.2.0.70-beta.8", prerelease=True)
      github.add_release("v4.2.0.70")
      release = select_release(github.client(), REPO, "stable", requested="v4.2.0.70 (beta 8)")
      self.assertEqual(release.tag, "v4.2.0.70-beta.8")
      with self.assertRaisesRegex(ReleaseError, "404"):
        select_release(github.client(), REPO, "stable", requested="v4.2.0.99")

  def test_no_release(self):
    with FakeGitHub() as github:
      github.add_release("v4.2.0.71-beta.1", prerelease=True)
      with self.assertRaisesRegex(ReleaseError, "入れられる版がありません"):
        select_release(github.client(), REPO, "stable")

  def test_rate_limit_message(self):
    with FakeGitHub() as github:
      github.routes[f"/repos/{REPO}/releases?per_page=100"] = (403, b"{}")
      with self.assertRaisesRegex(ReleaseError, "60回"):
        select_release(github.client(), REPO, "stable")

  def test_http_is_refused_by_default(self):
    with self.assertRaisesRegex(ReleaseError, "https"):
      GitHubClient("http://127.0.0.1:1").releases(REPO)


class ExtractTest(TempDirTestCase):
  def write_zip(self, data, name="tsumugium-v4.2.0.70.zip"):
    path = Path(self.tmp_path(name))
    path.write_bytes(data)
    return path

  def test_extract_strips_top_directory_and_keeps_exec_bit(self):
    destination = Path(self.tmp_path("app.new"))
    build = extract_release(self.write_zip(build_zip("v4.2.0.70")), destination)
    self.assertEqual(build["version"], "v4.2.0.70")
    self.assertEqual((destination / "main.py").read_text(encoding="utf-8"), "print('bot')\n")
    self.assertTrue((destination / "db" / "emoji_ja.json").is_file())
    self.assertEqual(oct(os.stat(destination / "install.sh").st_mode & 0o777), "0o755")
    self.assertEqual(oct(os.stat(destination / "main.py").st_mode & 0o777), "0o644")

  def test_old_destination_is_replaced(self):
    destination = Path(self.tmp_path("app.new"))
    destination.mkdir()
    (destination / "leftover.py").write_text("x", encoding="utf-8")
    extract_release(self.write_zip(build_zip("v4.2.0.70")), destination)
    self.assertFalse((destination / "leftover.py").exists())

  def test_unsafe_zips_are_rejected(self):
    cases = {
      "parent": [("tsumugium-v4.2.0.70/../evil.py", "x", 0o644)],
      "absolute": [("/etc/evil", "x", 0o644)],
      "outside": [("other/evil.py", "x", 0o644)],
      "symlink": [("tsumugium-v4.2.0.70/link", "/etc/passwd", 0o120777)],
    }
    for label, extra in cases.items():
      with self.subTest(label=label):
        destination = Path(self.tmp_path(f"app-{label}"))
        with self.assertRaises(ReleaseError):
          extract_release(self.write_zip(build_zip("v4.2.0.70", extra=extra), f"{label}.zip"), destination)
        self.assertFalse(Path(self.tmp_dir, "evil.py").exists())

  def test_not_a_release_zip(self):
    with self.assertRaises(ReleaseError):
      extract_release(self.write_zip(b"not a zip"), Path(self.tmp_path("a")))
    with self.assertRaisesRegex(ReleaseError, "配布用のzipではありません"):
      extract_release(self.write_zip(build_zip("v4.2.0.70", top="something")), Path(self.tmp_path("b")))

  def test_parse_sha256(self):
    digest = "a" * 64
    self.assertEqual(parse_sha256(f"{digest}  x.zip\n", "x.zip"), digest)
    self.assertEqual(parse_sha256(f"{digest} *x.zip\n", "x.zip"), digest)
    with self.assertRaises(ReleaseError):
      parse_sha256(f"{digest}  other.zip\n", "x.zip")


class BuildReleaseCompatibilityTest(TempDirTestCase):
  def test_zip_from_build_script_can_be_extracted(self):
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("build_release", root / "scripts" / "build_release.py")
    build_release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_release)

    lock = Path(self.tmp_path("requirements.lock"))
    lock.write_text("discord.py==0.0.0 --hash=sha256:dummy\n", encoding="utf-8")
    files = ["config.py", "main.py", "install.sh", "installer/__init__.py", ".env.template"]
    zip_path, sha_path, info = build_release.build(
      root, Path(self.tmp_path("dist")), lock, files=files, commit="0" * 40
    )
    system = System(Path(self.tmp_dir) / "root")
    fetched = extract_from_file(system, make_ui(), Layout.of("/opt/tsumugium"), zip_path)
    self.assertEqual(fetched.version, Version.parse(info["version"]))
    self.assertEqual(fetched.version.tag, info["tag"])
    self.assertTrue((fetched.path / "requirements.lock").is_file())
    self.assertEqual(oct(os.stat(fetched.path / "install.sh").st_mode & 0o777), "0o755")


class FetchTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.system = System(Path(self.tmp_dir) / "root")
    self.layout = Layout.of("/opt/tsumugium")
    self.app_new = self.system.path("/opt/tsumugium/app.new")

  def fetch(self, github, tag="v4.2.0.71"):
    client = github.client()
    release = select_release(client, REPO, "stable", requested=tag)
    return fetch_release(self.system, make_ui(), self.layout, client, release)

  def test_fetch_and_extract(self):
    with FakeGitHub() as github:
      github.add_release("v4.2.0.71")
      fetched = self.fetch(github)
    self.assertEqual(fetched.version, Version.parse("v4.2.0.71"))
    self.assertEqual(fetched.path, self.app_new)
    self.assertTrue((self.app_new / "main.py").is_file())
    self.assertEqual(sorted(os.listdir(self.system.path("/opt/tsumugium"))), ["app.new"])

  def test_checksum_mismatch_stops_before_extracting(self):
    with FakeGitHub() as github:
      github.add_release("v4.2.0.71", sha=f"{'0' * 64}  tsumugium-v4.2.0.71.zip\n")
      with self.assertRaisesRegex(ReleaseError, "一致しません"):
        self.fetch(github)
    self.assertFalse(self.app_new.exists())
    self.assertEqual(os.listdir(self.system.path("/opt/tsumugium")), [])

  def test_version_inside_zip_must_match(self):
    with FakeGitHub() as github:
      github.add_release("v4.2.0.71", data=build_zip("v4.2.0.71", version="v4.2.0.70"))
      with self.assertRaisesRegex(ReleaseError, "違います"):
        self.fetch(github)

  def test_too_large_download_stops(self):
    with FakeGitHub() as github:
      github.add_release("v4.2.0.71")
      client = github.client()
      release = select_release(client, REPO, "stable")
      with unittest.mock.patch("installer.release.MAX_DOWNLOAD_BYTES", 10):
        with self.assertRaisesRegex(ReleaseError, "大きすぎます"):
          fetch_release(self.system, make_ui(), self.layout, client, release)

  def test_from_file(self):
    data = build_zip("v4.2.0.71-beta.2")
    zip_path = Path(self.tmp_path("tsumugium-v4.2.0.71-beta.2.zip"))
    zip_path.write_bytes(data)
    with self.assertRaisesRegex(ReleaseError, ".sha256"):
      extract_from_file(self.system, make_ui(), self.layout, zip_path)
    Path(str(zip_path) + ".sha256").write_text(sha_line(data, zip_path.name), encoding="utf-8")
    fetched = extract_from_file(self.system, make_ui(), self.layout, zip_path)
    self.assertEqual(fetched.version, Version.parse("v4.2.0.71 (beta 2)"))
    self.assertTrue((self.app_new / "main.py").is_file())

  def test_from_file_checksum_mismatch(self):
    zip_path = Path(self.tmp_path("tsumugium-v4.2.0.71.zip"))
    zip_path.write_bytes(build_zip("v4.2.0.71"))
    Path(str(zip_path) + ".sha256").write_text(f"{'0' * 64}  {zip_path.name}\n", encoding="utf-8")
    with self.assertRaisesRegex(ReleaseError, "一致しません"):
      extract_from_file(self.system, make_ui(), self.layout, zip_path)
    self.assertFalse(self.app_new.exists())



if __name__ == "__main__":
  unittest.main()
