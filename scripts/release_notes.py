"""GitHub Releaseの下書きの本文を作る（CIで使う。配布物には含めない）。

使い方（プロジェクトルートで実行）:
  python scripts/release_notes.py --tag v4.3.0.71 --out notes.md

本文は docs/RELEASE_CHECKLIST.md「リリースノートの書き方」の見出しのひな形と、
前の正式版のタグからのコミット名の一覧（参考）を入れる。一覧はHTMLのコメントに入れるので、
消し忘れて公開してもReleaseのページには表示されない。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

# vX.X.X.XX と、ベータの vX.X.X.XX-beta.X
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)(?:-beta\.([1-9]\d*))?$")

TEMPLATE = """### 機能追加（feat）
- feat:

### 修正（fix）
- fix:
"""


def tag_key(tag: str) -> tuple | None:
  """タグを比べるための値。正式版は同じ番号のベータより新しい。"""
  match = TAG_RE.fullmatch(tag)
  if match is None:
    return None
  numbers = tuple(int(part) for part in match.groups()[:4])
  beta = match.group(5)
  return (numbers, 0 if beta else 1, int(beta or 0))


def is_stable(tag: str) -> bool:
  match = TAG_RE.fullmatch(tag)
  return match is not None and match.group(5) is None


def previous_tag(tags: list[str], tag: str, *, stable_only: bool) -> str | None:
  """tag より古いタグのうち、いちばん新しいものを返す。"""
  current = tag_key(tag)
  if current is None:
    raise ValueError(f"タグの表記が規則に合いません: {tag!r}")
  candidates = [
    other for other in tags
    if tag_key(other) is not None
    and tag_key(other) < current
    and (is_stable(other) or not stable_only)
  ]
  return max(candidates, key=tag_key, default=None)


def git(root: Path, *args: str) -> str:
  return subprocess.run(
    ["git", *args], cwd=root, check=True, capture_output=True, text=True,
  ).stdout


def commit_subjects(root: Path, since: str | None, until: str = "HEAD") -> list[str]:
  """since から until までの、マージを除いたコミット名を古い順に返す。"""
  revision = f"{since}..{until}" if since else until
  output = git(root, "log", "--reverse", "--no-merges", "--format=%s", revision)
  return [line for line in output.splitlines() if line.strip()]


def _for_comment(text: str) -> str:
  # HTMLのコメントの中では "--" を書けないので、似た文字に置き換える
  return text.replace("--", "－－")


def render(tag: str, previous: str | None, subjects: list[str]) -> str:
  since = f"{previous} から" if previous else "最初から"
  lines = [
    TEMPLATE,
    "<!--",
    f"参考: {since} {tag} までのコミット（マージを除く、古い順、{len(subjects)}件）。",
    "docs/RELEASE_CHECKLIST.md「リリースノートの書き方」に沿って上の見出しへまとめ直し、",
    "空の行を消してから公開してください。このコメントは公開しても表示されません。",
    "",
  ]
  lines += [f"- {_for_comment(subject)}" for subject in subjects]
  lines.append("-->")
  return "\n".join(lines) + "\n"


def build_notes(root: Path, tag: str, *, until: str = "HEAD") -> str:
  tags = git(root, "tag", "--list", "v*").split()
  previous = previous_tag(tags, tag, stable_only=is_stable(tag))
  return render(tag, previous, commit_subjects(root, previous, until))


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="GitHub Releaseの下書きの本文を作る")
  parser.add_argument("--tag", required=True, help="作るReleaseのタグ名（例 v4.3.0.71）")
  parser.add_argument("--out", required=True, help="本文を書き出すファイル")
  args = parser.parse_args(argv)
  notes = build_notes(ROOT, args.tag)
  Path(args.out).write_text(notes, encoding="utf-8")
  print(notes)
  return 0


if __name__ == "__main__":
  sys.exit(main())
