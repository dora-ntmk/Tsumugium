"""配布用のzipを作る（開発用。配布物には含めない）。

使い方（プロジェクトルートで実行）:
  python scripts/build_release.py --lock requirements.lock --out dist

作るもの:
  dist/tsumugium-<タグ名>.zip         zipの中は tsumugium-<タグ名>/ 以下にまとめる
  dist/tsumugium-<タグ名>.zip.sha256  sha256sum と同じ形式
zipには build-info.json（バージョン、チャンネル、タグ名、コミット）と requirements.lock を入れる。
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

  gitのタグ名には空白を使えないので、ベータは (beta X) を -beta.X に置き換える。
  """
  match = VERSION_RE.fullmatch(version)
  if match is None:
    raise ValueError(f"VERSION の表記が規則に合いません: {version!r}")
  base, beta = match.groups()
  if beta is None:
    return {"version": version, "channel": "stable", "tag": base}
  return {"version": version, "channel": "beta", "tag": f"{base}-beta.{beta}"}


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


def _write_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
  info = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
  info.compress_type = zipfile.ZIP_DEFLATED
  info.external_attr = 0o644 << 16
  archive.writestr(info, data)


def build(
    root: Path,
    out_dir: Path,
    lock_path: Path,
    *,
    files: list[str] | None = None,
    commit: str | None = None,
) -> tuple[Path, Path, dict[str, str]]:
  if not lock_path.is_file():
    raise FileNotFoundError(f"requirements.lock が見つかりません: {lock_path}")

  info = parse_version(read_version(root / "config.py"))
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
      _write_entry(archive, f"{prefix}/{path}", (root / path).read_bytes())
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
  args = parser.parse_args(argv)

  zip_path, sha_path, info = build(ROOT, Path(args.out), Path(args.lock))
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
