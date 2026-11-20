"""配布用のzipを作る（開発用。配布物には含めない）。

使い方（プロジェクトルートで実行）:
  python scripts/build_release.py --lock requirements.lock --out dist

作るもの:
  dist/tsumugium-<タグ名>.zip         zipの中は tsumugium-<タグ名>/ 以下にまとめる
  dist/tsumugium-<タグ名>.zip.sha256  sha256sum と同じ形式
zipには build-info.json（バージョン、チャンネル、タグ名、コミット）と requirements.lock を入れる。

config.py の VERSION には人が決めたことだけを書き、ベータ番号と LAST_UPDATED はここで決めて
zipの中の config.py に書き込む（AGENTS.md「バージョン表記」）。
  vX.X.X.XX         その正式版がまだ出ていなければ正式版。出ていれば（タグか下書きがある）、
                    次のパッチ（3つ目と末尾を1つずつ上げる）のベータにする
  vX.X.X.XX (beta)  その版のベータにする
ベータ番号は、その版のベータのタグの最大＋1。LAST_UPDATED はコミット日（日本時間）。
--released-file には、出したReleaseのタグ名（下書きを含む）を1行に1つ書いたファイルを渡す。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

# AGENTS.md「バージョン表記」: vX.X.X.XX、ベータは vX.X.X.XX (beta X)
VERSION_RE = re.compile(r"^(v\d+\.\d+\.\d+\.\d+)(?: \(beta ([1-9]\d*)\))?$")
CONFIG_VERSION_RE = re.compile(r'^VERSION\s*=\s*"([^"]+)"', re.MULTILINE)
CONFIG_LAST_UPDATED_RE = re.compile(r'^LAST_UPDATED\s*=\s*"([^"]*)"', re.MULTILINE)
# config.py に人が書く形: vX.X.X.XX、vX.X.X.XX (beta)。番号つきの (beta X) もそのまま使う
SOURCE_VERSION_RE = re.compile(r"^(v\d+\.\d+\.\d+\.\d+)(?: \(beta(?: ([1-9]\d*))?\))?$")
# タグ名。v4.3.0.71-beta.12 まではゼロ埋めしていないので、どちらも読む
TAG_RE = re.compile(r"^(v\d+\.\d+\.\d+\.\d+)(?:-beta\.0*([1-9]\d*))?$")

# 配布物に入れないもの（開発用のファイル）
EXCLUDED_PREFIXES = (
  ".claude/",
  ".github/",
  "docs/",
  "scripts/",
  "tests/",
)
EXCLUDED_FILES = {
  ".gitignore",
  "AGENTS.md",
}

# zip内のファイルの日時をそろえ、同じ中身なら同じzipになるようにする
FIXED_DATE_TIME = (2020, 1, 1, 0, 0, 0)


def read_version(config_path: Path) -> str:
  match = CONFIG_VERSION_RE.search(config_path.read_text(encoding="utf-8"))
  if match is None:
    raise ValueError(f"{config_path} に VERSION が見つかりません")
  return match.group(1)


def parse_version(version: str) -> dict[str, str]:
  """VERSIONからチャンネルとタグ名を決める。

  gitのタグ名には空白を使えないので、ベータは (beta X) を -beta.XXX に置き換える。
  GitHubのReleasesはタグ名の文字列順で並ぶので、ベータ番号は3桁にゼロ埋めする（beta.010 を beta.9 より上にするため）。
  """
  match = VERSION_RE.fullmatch(version)
  if match is None:
    raise ValueError(f"VERSION の表記が規則に合いません: {version!r}")
  base, beta = match.groups()
  if beta is None:
    return {"version": version, "channel": "stable", "tag": base}
  return {"version": version, "channel": "beta", "tag": f"{base}-beta.{int(beta):03d}"}


def next_patch(base: str) -> str:
  """vA.B.C.D の次のパッチ vA.B.(C+1).(D+1)。末尾は累積リリース回数なので、いっしょに上げる。"""
  major, minor, patch, count = (int(part) for part in base[1:].split("."))
  return f"v{major}.{minor}.{patch + 1}.{count + 1}"


def resolve_version(source: str, tags: list[str], released: list[str] = ()) -> str:
  """config.py の VERSION と、あるタグ・出したReleaseから、配布物の版を決める。"""
  match = SOURCE_VERSION_RE.fullmatch(source)
  if match is None:
    raise ValueError(f"VERSION の表記が規則に合いません: {source!r}")
  base, number = match.groups()
  if number is not None:
    return source
  betas: dict[str, int] = {}
  stables = set()
  for name in [*tags, *released]:
    tag = TAG_RE.fullmatch(name.strip())
    if tag is None:
      continue
    tag_base, beta = tag.groups()
    if beta is None:
      stables.add(tag_base)
    else:
      betas[tag_base] = max(betas.get(tag_base, 0), int(beta))
  if not source.endswith("(beta)"):
    if base not in stables:
      return base
    while base in stables:
      base = next_patch(base)
  return f"{base} (beta {betas.get(base, 0) + 1})"


def stamp_config(text: str, version: str, last_updated: str) -> str:
  """config.py の VERSION と LAST_UPDATED の値を書き換える。"""
  for pattern, value in ((CONFIG_VERSION_RE, version), (CONFIG_LAST_UPDATED_RE, last_updated)):
    match = pattern.search(text)
    if match is None:
      raise ValueError(f"config.py に {pattern.pattern} が見つかりません")
    text = text[:match.start(1)] + value + text[match.end(1):]
  return text


def git_tags(root: Path) -> list[str]:
  output = subprocess.run(
    ["git", "tag", "--list", "v*"], cwd=root, check=True, capture_output=True, text=True,
  ).stdout
  return output.split()


def commit_date(root: Path, commit: str) -> str:
  """コミット日（日本時間、YYYY/MM/DD）。同じコミットなら同じ値になる。"""
  return subprocess.run(
    ["git", "log", "-1", "--format=%cd", "--date=format-local:%Y/%m/%d", commit],
    cwd=root,
    check=True,
    capture_output=True,
    text=True,
    env={**os.environ, "TZ": "Asia/Tokyo"},
  ).stdout.strip()


def tracked_files(root: Path) -> list[str]:
  output = subprocess.run(
    ["git", "ls-files", "-z"],
    cwd=root,
    check=True,
    capture_output=True,
  ).stdout.decode("utf-8")
  return sorted(path for path in output.split("\0") if path)


def is_distributed(path: str) -> bool:
  if path in EXCLUDED_FILES:
    return False
  return not path.startswith(EXCLUDED_PREFIXES)


def current_commit(root: Path) -> str:
  commit = os.environ.get("GITHUB_SHA")
  if commit:
    return commit
  return subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=root,
    check=True,
    capture_output=True,
    text=True,
  ).stdout.strip()


def _write_entry(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    *,
    executable: bool = False,
) -> None:
  info = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
  info.compress_type = zipfile.ZIP_DEFLATED
  info.external_attr = (0o755 if executable else 0o644) << 16
  archive.writestr(info, data)


def build(
    root: Path,
    out_dir: Path,
    lock_path: Path,
    *,
    files: list[str] | None = None,
    commit: str | None = None,
    version: str | None = None,
    last_updated: str | None = None,
) -> tuple[Path, Path, dict[str, str]]:
  """配布用のzipを作る。version・last_updated を渡すと、zipの中の config.py に書き込む。"""
  if not lock_path.is_file():
    raise FileNotFoundError(f"requirements.lock が見つかりません: {lock_path}")

  info = parse_version(version or read_version(root / "config.py"))
  info["commit"] = commit or current_commit(root)

  if files is None:
    files = tracked_files(root)
  distributed = [path for path in files if is_distributed(path)]
  for reserved in ("build-info.json", "requirements.lock"):
    if reserved in distributed:
      raise ValueError(f"{reserved} はビルドで作るファイルなので、リポジトリに置けません")

  prefix = f"tsumugium-{info['tag']}"
  out_dir.mkdir(parents=True, exist_ok=True)
  zip_path = out_dir / f"{prefix}.zip"

  with zipfile.ZipFile(zip_path, "w") as archive:
    for path in distributed:
      source = root / path
      data = source.read_bytes()
      if path == "config.py" and (version or last_updated):
        text = data.decode("utf-8")
        current = CONFIG_LAST_UPDATED_RE.search(text)
        data = stamp_config(
          text,
          info["version"],
          last_updated or (current.group(1) if current else ""),
        ).encode("utf-8")
      _write_entry(
        archive,
        f"{prefix}/{path}",
        data,
        executable=os.access(source, os.X_OK),
      )
    _write_entry(archive, f"{prefix}/requirements.lock", lock_path.read_bytes())
    build_info = json.dumps(info, ensure_ascii=False, indent=2) + "\n"
    _write_entry(archive, f"{prefix}/build-info.json", build_info.encode("utf-8"))

  digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
  sha_path = out_dir / f"{zip_path.name}.sha256"
  sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
  return zip_path, sha_path, info


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="配布用のzipを作る")
  parser.add_argument("--lock", default="requirements.lock", help="requirements.lock のパス")
  parser.add_argument("--out", default="dist", help="出力先ディレクトリ")
  parser.add_argument(
    "--released-file",
    help="出したReleaseのタグ名（下書きを含む）を1行に1つ書いたファイル。省くとタグだけで決める",
  )
  args = parser.parse_args(argv)

  released = []
  if args.released_file:
    released = Path(args.released_file).read_text(encoding="utf-8").split()
  commit = current_commit(ROOT)
  version = resolve_version(read_version(ROOT / "config.py"), git_tags(ROOT), released)
  zip_path, sha_path, info = build(
    ROOT,
    Path(args.out),
    Path(args.lock),
    commit=commit,
    version=version,
    last_updated=commit_date(ROOT, commit),
  )
  print(f"バージョン: {info['version']}（{info['channel']}、タグ {info['tag']}）")
  print(f"作成しました: {zip_path}")
  print(f"作成しました: {sha_path}")
  github_output = os.environ.get("GITHUB_OUTPUT")
  if github_output:
    with open(github_output, "a", encoding="utf-8") as file:
      file.write(f"tag={info['tag']}\n")
      file.write(f"channel={info['channel']}\n")
  return 0


if __name__ == "__main__":
  sys.exit(main())
