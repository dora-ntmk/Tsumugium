"""GitHub Releaseの本文を作る（CIで使う。配布物には含めない）。

使い方（プロジェクトルートで実行）:
  python scripts/release_notes.py --tag v4.3.0.71 --out notes.md
  python scripts/release_notes.py --tag v4.3.0.71-beta.2 --out notes.md
  python scripts/release_notes.py --changed-code-since v4.3.0.71-beta.1

正式版（下書き）の本文には、docs/RELEASE_CHECKLIST.md「リリースノートの書き方」の見出しの
ひな形と、前の正式版のタグからのコミット名の一覧（参考）を入れる。一覧はHTMLのコメントに
入れるので、消し忘れて公開してもReleaseのページには表示されない。
ベータ版（自動で公開するプレリリース）の本文には、前のタグからのコミット名の一覧をそのまま入れる。

--changed-code-since は、そのタグから配布物のコードが変わったファイルを表示する。
VERSIONの (beta X) を上げ忘れたことを見つけるために使う。
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

# vX.X.X.XX と、ベータの vX.X.X.XX-beta.X。ベータ番号は3桁のゼロ埋め（-beta.00X）へ移行する途中なので、どちらも受け付ける
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)(?:-beta\.0*([1-9]\d*))?$")

_spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
_build_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build_release)

# 配布物に入っていても、Botの動きを変えないファイル
NON_CODE_SUFFIXES = (".md",)
NON_CODE_FILES = {"LICENSE"}

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


def render_beta(tag: str, previous: str | None, subjects: list[str], commit: str) -> str:
  since = f"{previous} からの" if previous else "最初からの"
  lines = [
    f"`main`のコミット {commit[:7]} から自動で作ったベータ版です。",
    "",
    f"{since}変更（マージを除く、古い順、{len(subjects)}件）:",
    "",
  ]
  lines += [f"- {subject}" for subject in subjects]
  return "\n".join(lines) + "\n"


def build_notes(root: Path, tag: str, *, until: str = "HEAD") -> str:
  tags = git(root, "tag", "--list", "v*").split()
  stable = is_stable(tag)
  previous = previous_tag(tags, tag, stable_only=stable)
  subjects = commit_subjects(root, previous, until)
  if stable:
    return render(tag, previous, subjects)
  commit = git(root, "rev-parse", until).strip()
  return render_beta(tag, previous, subjects, commit)


def is_code(path: str) -> bool:
  """配布物に入り、Botの動きに関わるファイルかどうか。"""
  if path in _build_release.EXCLUDED_FILES or path in NON_CODE_FILES:
    return False
  if path.startswith(_build_release.EXCLUDED_PREFIXES):
    return False
  return not path.endswith(NON_CODE_SUFFIXES)


def changed_code_files(root: Path, since: str, until: str = "HEAD") -> list[str]:
  output = git(root, "diff", "--name-only", f"{since}..{until}")
  return [path for path in output.splitlines() if path and is_code(path)]


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="GitHub Releaseの本文を作る")
  parser.add_argument("--tag", help="作るReleaseのタグ名（例 v4.3.0.71、v4.3.0.71-beta.2）")
  parser.add_argument("--out", help="本文を書き出すファイル")
  parser.add_argument("--changed-code-since", metavar="TAG", help="そのタグから変わったコードのファイルを表示する")
  args = parser.parse_args(argv)
  if args.changed_code_since:
    for path in changed_code_files(ROOT, args.changed_code_since):
      print(path)
    return 0
  if not args.tag or not args.out:
    parser.error("--tag と --out を指定してください")
  notes = build_notes(ROOT, args.tag)
  Path(args.out).write_text(notes, encoding="utf-8")
  print(notes)
  return 0


if __name__ == "__main__":
  sys.exit(main())
