"""配布用zipを作るスクリプト（scripts/build_release.py）のテスト。"""

import hashlib
import importlib.util
import json
import os
import zipfile
from pathlib import Path

from tests.support import TempDirTestCase


ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
  "build_release", ROOT / "scripts" / "build_release.py"
)
build_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_release)


class ParseVersionTest(TempDirTestCase):
  def test_stable_version(self):
    self.assertEqual(
      build_release.parse_version("v4.2.0.70"),
      {"version": "v4.2.0.70", "channel": "stable", "tag": "v4.2.0.70"},
    )

  def test_beta_version_becomes_tag_without_spaces(self):
    self.assertEqual(
      build_release.parse_version("v4.2.0.70 (beta 8)"),
      {"version": "v4.2.0.70 (beta 8)", "channel": "beta", "tag": "v4.2.0.70-beta.8"},
    )

  def test_invalid_versions_are_rejected(self):
    for version in ("v4.2.0", "v4.2.0.70 (beta 0)", "v4.2.0.70-beta.8"):
      with self.subTest(version=version):
        with self.assertRaises(ValueError):
          build_release.parse_version(version)

  def test_repository_version_can_be_parsed(self):
    version = build_release.read_version(ROOT / "config.py")
    build_release.parse_version(version)


class FileSelectionTest(TempDirTestCase):
  def test_development_files_are_excluded(self):
    for path in (
        "tests/test_swap.py",
        ".github/workflows/ci.yml",
        "docs/ARCHITECTURE.md",
        "scripts/build_release.py",
        ".claude/CLAUDE.md",
        "AGENTS.md",
        ".gitignore",
    ):
      with self.subTest(path=path):
        self.assertFalse(build_release.is_distributed(path))

  def test_runtime_files_are_included(self):
    for path in (
        "main.py",
        "services/voice_service.py",
        "db/emoji_ja.json",
        ".env.template",
        "requirements.txt",
        "README.md",
        "LICENSE",
    ):
      with self.subTest(path=path):
        self.assertTrue(build_release.is_distributed(path))


class BuildTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.root = Path(self.tmp_dir) / "repo"
    files = {
      "config.py": 'VERSION      = "v4.2.0.70 (beta 3)"\n',
      "main.py": "print('main')\n",
      "db/emoji_ja.json": "{}\n",
      ".env.template": "DISCORD_BOT_TOKEN=\n",
      "tests/test_x.py": "# test\n",
      "AGENTS.md": "# spec\n",
    }
    for path, text in files.items():
      target = self.root / path
      target.parent.mkdir(parents=True, exist_ok=True)
      target.write_text(text, encoding="utf-8")
    self.files = sorted(files)
    self.lock = Path(self.tmp_path("requirements.lock"))
    self.lock.write_text("discord.py==0.0.0 --hash=sha256:dummy\n", encoding="utf-8")
    self.out = Path(self.tmp_dir) / "dist"

  def build(self):
    return build_release.build(
      self.root, self.out, self.lock, files=self.files, commit="0" * 40
    )

  def test_zip_contents_and_build_info(self):
    zip_path, _, info = self.build()
    self.assertEqual(zip_path.name, "tsumugium-v4.2.0.70-beta.3.zip")
    prefix = "tsumugium-v4.2.0.70-beta.3/"
    with zipfile.ZipFile(zip_path) as archive:
      names = sorted(archive.namelist())
      build_info = json.loads(archive.read(prefix + "build-info.json"))
      lock = archive.read(prefix + "requirements.lock").decode("utf-8")
    self.assertEqual(names, sorted(prefix + name for name in (
      ".env.template",
      "build-info.json",
      "config.py",
      "db/emoji_ja.json",
      "main.py",
      "requirements.lock",
    )))
    self.assertEqual(build_info, {
      "version": "v4.2.0.70 (beta 3)",
      "channel": "beta",
      "tag": "v4.2.0.70-beta.3",
      "commit": "0" * 40,
    })
    self.assertEqual(info, build_info)
    self.assertIn("discord.py==0.0.0", lock)

  def test_sha256_file_matches_zip(self):
    zip_path, sha_path, _ = self.build()
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    self.assertEqual(sha_path.read_text(encoding="utf-8"), f"{digest}  {zip_path.name}\n")

  def test_same_input_gives_same_zip(self):
    first, _, _ = self.build()
    first_bytes = first.read_bytes()
    os.remove(first)
    second, _, _ = self.build()
    self.assertEqual(second.read_bytes(), first_bytes)

  def test_executable_bit_is_kept(self):
    script = self.root / "install.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    self.files = sorted(self.files + ["install.sh"])
    zip_path, _, _ = self.build()
    prefix = "tsumugium-v4.2.0.70-beta.3/"
    with zipfile.ZipFile(zip_path) as archive:
      modes = {
        info.filename: (info.external_attr >> 16) & 0o777
        for info in archive.infolist()
      }
    self.assertEqual(modes[prefix + "install.sh"], 0o755)
    self.assertEqual(modes[prefix + "main.py"], 0o644)

  def test_missing_lock_file_is_an_error(self):
    os.remove(self.lock)
    with self.assertRaises(FileNotFoundError):
      self.build()
