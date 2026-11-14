"""Releaseの下書きの本文を作るスクリプト（scripts/release_notes.py）のテスト。"""

import importlib.util
import subprocess
from pathlib import Path

from tests.support import TempDirTestCase


ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
  "release_notes", ROOT / "scripts" / "release_notes.py"
)
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)


class PreviousTagTest(TempDirTestCase):
  TAGS = [
    "v4.1.0.69",
    "v4.2.0.70-beta.1",
    "v4.2.0.70",
    "v4.3.0.71-beta.1",
    "v4.3.0.71-beta.2",
    "not-a-version",
  ]

  def test_stable_skips_betas(self):
    self.assertEqual(
      release_notes.previous_tag(self.TAGS, "v4.3.0.71", stable_only=True),
      "v4.2.0.70",
    )

  def test_beta_uses_any_older_tag(self):
    self.assertEqual(
      release_notes.previous_tag(self.TAGS, "v4.3.0.71-beta.3", stable_only=False),
      "v4.3.0.71-beta.2",
    )

  def test_release_is_newer_than_its_betas(self):
    self.assertEqual(
      release_notes.previous_tag(self.TAGS, "v4.2.0.70", stable_only=False),
      "v4.2.0.70-beta.1",
    )

  def test_no_previous_tag(self):
    self.assertIsNone(release_notes.previous_tag(self.TAGS, "v4.1.0.69", stable_only=True))

  def test_invalid_tag(self):
    with self.assertRaises(ValueError):
      release_notes.previous_tag(self.TAGS, "v4.3.0.71 (beta 1)", stable_only=True)


class RenderTest(TempDirTestCase):
  def test_template_and_commented_commits(self):
    text = release_notes.render("v4.3.0.71", "v4.2.0.70", ["feat: A", "fix: B"])
    head, comment = text.split("<!--", 1)
    self.assertIn("### 機能追加（feat）", head)
    self.assertIn("### 修正（fix）", head)
    self.assertNotIn("feat: A", head)
    self.assertIn("v4.2.0.70 から v4.3.0.71 までのコミット", comment)
    self.assertIn("2件", comment)
    self.assertIn("- feat: A\n- fix: B\n-->", comment)

  def test_double_hyphen_does_not_close_comment(self):
    text = release_notes.render("v4.3.0.71", None, ["fix: --yes と --> の扱い"])
    comment = text.split("<!--", 1)[1]
    self.assertEqual(comment.count("-->"), 1)
    self.assertTrue(comment.rstrip().endswith("-->"))
    self.assertIn("最初から", comment)


class BuildNotesTest(TempDirTestCase):
  def git(self, *args):
    subprocess.run(
      ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
      cwd=self.tmp_dir, check=True, capture_output=True,
    )

  def commit(self, subject):
    self.git("commit", "--allow-empty", "-q", "-m", subject)

  def test_commits_since_previous_stable_tag(self):
    self.git("init", "-q")
    self.commit("feat: 前の版")
    self.git("tag", "v4.2.0.70")
    self.commit("feat: ひとつめ")
    self.git("tag", "v4.3.0.71-beta.1")
    self.commit("fix: ふたつめ")
    self.git("checkout", "-q", "-b", "topic")
    self.commit("docs: みっつめ")
    self.git("checkout", "-q", "-")
    self.git("merge", "-q", "--no-ff", "-m", "Merge topic", "topic")

    text = release_notes.build_notes(Path(self.tmp_dir), "v4.3.0.71")
    comment = text.split("<!--", 1)[1]
    self.assertIn("v4.2.0.70 から v4.3.0.71 までのコミット", comment)
    # 同じ秒に作ったコミットは並び順が決まらないので、含まれることだけを確かめる
    for line in ("- feat: ひとつめ\n", "- fix: ふたつめ\n", "- docs: みっつめ\n"):
      self.assertIn(line, comment)
    self.assertIn("3件", comment)
    self.assertNotIn("前の版", comment)
    self.assertNotIn("Merge topic", comment)
