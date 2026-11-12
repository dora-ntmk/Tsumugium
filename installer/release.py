"""GitHub Releases からの版の選択・取得・照合・展開。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path, PurePosixPath

from installer.instance import Layout
from installer.system import System
from installer.ui import UI


GITHUB_API = "https://api.github.com"
USER_AGENT = "tsumugium-setup"
HTTP_TIMEOUT = 30.0
# 配布用zipの大きさの上限（これより大きければ、取得の途中で止める）
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

# v4.2.0.70、v4.2.0.70 (beta 9)、v4.2.0.70-beta.9 のどれも受け付ける
_VERSION_RE = re.compile(
  r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)(?:(?: \(beta ([1-9]\d*)\))|(?:-beta\.([1-9]\d*)))?$"
)
_SHA256_RE = re.compile(r"^([0-9a-f]{64})\s+\*?(\S+)\s*$")


class ReleaseError(RuntimeError):
  """版の選択・取得・照合・展開ができないことを表す。"""


# バージョン

@total_ordering
@dataclass(frozen=True)
class Version:
  numbers: tuple[int, int, int, int]
  beta: int | None = None

  @classmethod
  def parse(cls, text: str) -> "Version":
    match = _VERSION_RE.fullmatch(text.strip())
    if match is None:
      raise ReleaseError(f"バージョンの書き方が正しくありません: {text!r}")
    numbers = tuple(int(part) for part in match.group(1, 2, 3, 4))
    beta = match.group(5) or match.group(6)
    return cls(numbers, int(beta) if beta else None)

  @property
  def is_beta(self) -> bool:
    return self.beta is not None

  def _key(self) -> tuple:
    # ベータは、同じ番号の正式リリースより前とする
    return (self.numbers, 0 if self.is_beta else 1, self.beta or 0)

  def __lt__(self, other: "Version") -> bool:
    return self._key() < other._key()

  @property
  def tag(self) -> str:
    base = "v" + ".".join(str(part) for part in self.numbers)
    return f"{base}-beta.{self.beta}" if self.is_beta else base

  def __str__(self) -> str:
    base = "v" + ".".join(str(part) for part in self.numbers)
    return f"{base} (beta {self.beta})" if self.is_beta else base


def zip_name(tag: str) -> str:
  return f"tsumugium-{tag}.zip"


# GitHub

@dataclass(frozen=True)
class Release:
  version: Version
  tag: str
  prerelease: bool
  zip_url: str
  sha256_url: str


class GitHubClient:
  """GitHub Releases を認証なしで読む（1時間に60回まで）。"""

  def __init__(self, api_base: str = GITHUB_API, *, allow_http: bool = False, timeout: float = HTTP_TIMEOUT):
    self.api_base = api_base.rstrip("/")
    self.allow_http = allow_http
    self.timeout = timeout

  def _check_url(self, url: str) -> None:
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme != "https" and not (self.allow_http and scheme == "http"):
      raise ReleaseError(f"https 以外のURLからは取得しません: {url}")

  def _open(self, url: str, accept: str):
    self._check_url(url)
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
    try:
      return urllib.request.urlopen(request, timeout=self.timeout)
    except urllib.error.HTTPError as error:
      if error.code in (403, 429):
        raise ReleaseError(
          "GitHubへの問い合わせが上限（1時間に60回）に達したか、拒否されました。"
          "しばらく待ってから、もう一度実行してください"
        ) from None
      if error.code == 404:
        raise ReleaseError(f"見つかりません（404）: {url}") from None
      raise ReleaseError(f"{url} が {error.code} を返しました") from None
    except (urllib.error.URLError, OSError) as error:
      raise ReleaseError(f"{url} に接続できません: {getattr(error, 'reason', error)}") from None

  def _get_json(self, path: str):
    with self._open(self.api_base + path, "application/vnd.github+json") as response:
      try:
        return json.loads(response.read().decode("utf-8"))
      except ValueError:
        raise ReleaseError(f"GitHubの応答を読めません: {path}") from None

  def _to_release(self, data: dict) -> Release | None:
    if not isinstance(data, dict) or data.get("draft"):
      return None
    tag = data.get("tag_name")
    if not isinstance(tag, str):
      return None
    try:
      version = Version.parse(tag)
    except ReleaseError:
      return None
    assets = {
      asset.get("name"): asset.get("browser_download_url")
      for asset in data.get("assets") or []
      if isinstance(asset, dict)
    }
    name = zip_name(version.tag)
    if not assets.get(name) or not assets.get(name + ".sha256"):
      return None
    return Release(version, tag, bool(data.get("prerelease")), assets[name], assets[name + ".sha256"])

  def releases(self, repo: str) -> list[Release]:
    data = self._get_json(f"/repos/{repo}/releases?per_page=100")
    if not isinstance(data, list):
      raise ReleaseError("GitHubの応答の形式が正しくありません")
    return [release for release in map(self._to_release, data) if release is not None]

  def release(self, repo: str, version: Version) -> Release:
    data = self._get_json(f"/repos/{repo}/releases/tags/{urllib.parse.quote(version.tag)}")
    release = self._to_release(data)
    if release is None:
      raise ReleaseError(f"{version.tag} には配布用のzipと .sha256 がありません")
    return release

  def download(self, url: str, destination: Path, *, max_bytes: int | None = None) -> None:
    if max_bytes is None:
      max_bytes = MAX_DOWNLOAD_BYTES
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with self._open(url, "application/octet-stream") as response, open(destination, "wb") as file:
      while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
          break
        size += len(chunk)
        if size > max_bytes:
          raise ReleaseError(f"{url} が大きすぎます（{max_bytes}バイトまで）")
        file.write(chunk)

  def read_text(self, url: str, *, max_bytes: int = 64 * 1024) -> str:
    with self._open(url, "text/plain") as response:
      data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
      raise ReleaseError(f"{url} が大きすぎます")
    return data.decode("utf-8", errors="replace")


def select_release(
    client: GitHubClient,
    repo: str,
    channel: str,
    *,
    requested: str | None = None,
) -> Release:
  """取得する版を決める。指定がなければ、チャンネルの最新を選ぶ。

  安定版はプレリリースを除いた最新、ベータはプレリリースも含めた最新。
  """
  if requested is not None:
    return client.release(repo, Version.parse(requested))
  candidates = [
    release for release in client.releases(repo)
    if channel == "beta" or not release.prerelease
  ]
  if not candidates:
    raise ReleaseError(f"{repo} に、{channel} で入れられる版がありません")
  return max(candidates, key=lambda release: release.version)


def check_direction(current: str | None, target: Version, *, allow_downgrade: bool) -> str:
  """今の版と比べる。'newer'、'same'、'older' を返す。古い版は allow_downgrade のときだけ通す。"""
  if current is None:
    return "newer"
  now = Version.parse(current)
  if target == now:
    return "same"
  if target > now:
    return "newer"
  if not allow_downgrade:
    raise ReleaseError(
      f"{target} は今の {now} より古い版です。戻すときは --allow-downgrade を付けてください"
    )
  return "older"


# 照合と展開

def parse_sha256(text: str, expected_name: str) -> str:
  for line in text.splitlines():
    match = _SHA256_RE.match(line.strip())
    if match and match.group(2) == expected_name:
      return match.group(1)
  raise ReleaseError(f"{expected_name} のSHA256値が見つかりません")


def file_sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with open(path, "rb") as file:
    for chunk in iter(lambda: file.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
  actual = file_sha256(path)
  if actual != expected:
    raise ReleaseError(
      f"{path.name} のSHA256値が一致しません（期待 {expected}、実際 {actual}）。"
      "取得が壊れたか、配布物が書き換えられています"
    )


def _safe_member(name: str, top: str) -> PurePosixPath | None:
  """zipの中のパスを確かめ、一番上のディレクトリを除いた相対パスを返す。"""
  path = PurePosixPath(name)
  if path.is_absolute() or ".." in path.parts or "\\" in name:
    raise ReleaseError(f"zipに安全でないパスがあります: {name}")
  if not path.parts or path.parts[0] != top:
    raise ReleaseError(f"zipの中身が {top}/ にまとまっていません: {name}")
  relative = PurePosixPath(*path.parts[1:])
  return relative if relative.parts else None


def extract_release(zip_path: Path, destination: Path) -> dict:
  """配布用zipを destination に展開し、build-info.json の中身を返す。

  zipは tsumugium-<タグ名>/ 以下にまとまっている前提で、その1段を除いて展開する。
  destination がすでにあれば消してから作り直す。
  """
  if destination.exists():
    shutil.rmtree(destination)
  try:
    archive = zipfile.ZipFile(zip_path)
  except (zipfile.BadZipFile, OSError) as error:
    raise ReleaseError(f"{zip_path.name} を開けません: {error}") from None
  with archive:
    names = archive.namelist()
    if not names:
      raise ReleaseError(f"{zip_path.name} が空です")
    top = PurePosixPath(names[0]).parts[0]
    if not re.fullmatch(r"tsumugium-v[0-9A-Za-z.\-]+", top):
      raise ReleaseError(f"{zip_path.name} は配布用のzipではありません")
    destination.mkdir(parents=True)
    for info in archive.infolist():
      relative = _safe_member(info.filename, top)
      mode = (info.external_attr >> 16) & 0o177777
      if stat.S_ISLNK(mode):
        raise ReleaseError(f"zipにシンボリックリンクがあります: {info.filename}")
      if relative is None:
        continue
      target = destination.joinpath(*relative.parts)
      if info.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        continue
      target.parent.mkdir(parents=True, exist_ok=True)
      with archive.open(info) as source, open(target, "wb") as file:
        shutil.copyfileobj(source, file)
      target.chmod(0o755 if mode & 0o111 else 0o644)
  info_path = destination / "build-info.json"
  try:
    build = json.loads(info_path.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    raise ReleaseError(f"{zip_path.name} に build-info.json がないか、読めません") from None
  if not isinstance(build, dict) or not isinstance(build.get("version"), str):
    raise ReleaseError(f"{zip_path.name} の build-info.json の形式が正しくありません")
  return build


@dataclass(frozen=True)
class Fetched:
  """展開した新しい版。"""

  path: Path
  version: Version
  build: dict


def _check_build(build: dict, expected: Version | None) -> Version:
  version = Version.parse(build["version"])
  if expected is not None and version != expected:
    raise ReleaseError(f"zipの中身のバージョン（{version}）が、選んだ版（{expected}）と違います")
  return version


def fetch_release(
    system: System,
    ui: UI,
    layout: Layout,
    client: GitHubClient,
    release: Release,
) -> Fetched:
  """zipと .sha256 を取得して照合し、app.new/ に展開する。

  取得したファイルは入れる場所の中に置き（app.new/ と同じディスクにするため）、展開後に消す。
  """
  name = zip_name(release.version.tag)
  work = system.path(str(layout.prefix / ".download"))
  if work.exists():
    shutil.rmtree(work)
  work.mkdir(parents=True)
  try:
    ui.info(f"{release.version} を取得しています ...")
    expected = parse_sha256(client.read_text(release.sha256_url), name)
    zip_path = work / name
    client.download(release.zip_url, zip_path)
    verify_sha256(zip_path, expected)
    ui.info("SHA256値が一致しました")
    destination = system.path(str(layout.app_new))
    build = extract_release(zip_path, destination)
    version = _check_build(build, release.version)
  finally:
    shutil.rmtree(work, ignore_errors=True)
  ui.info(f"{layout.app_new} に展開しました")
  return Fetched(destination, version, build)


def extract_from_file(system: System, ui: UI, layout: Layout, zip_path: Path) -> Fetched:
  """--from-file: 手元のzipを、隣にある .sha256 で照合してから app.new/ に展開する。"""
  zip_path = Path(zip_path)
  if not zip_path.is_file():
    raise ReleaseError(f"{zip_path} が見つかりません")
  sha_path = zip_path.with_name(zip_path.name + ".sha256")
  if not sha_path.is_file():
    raise ReleaseError(
      f"{sha_path} が見つかりません。zipと一緒に配っている .sha256 を同じ場所に置いてください"
    )
  verify_sha256(zip_path, parse_sha256(sha_path.read_text(encoding="utf-8"), zip_path.name))
  ui.info("SHA256値が一致しました")
  destination = system.path(str(layout.app_new))
  build = extract_release(zip_path, destination)
  version = _check_build(build, None)
  ui.info(f"{layout.app_new} に展開しました（{version}）")
  return Fetched(destination, version, build)
