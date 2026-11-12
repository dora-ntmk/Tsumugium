"""インストール前の確認（OS・systemd・Python・必要なパッケージ・VOICEVOX）。"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from installer.system import System
from installer.ui import UI, UserAbort


# 対応するOSと、その最低バージョン
SUPPORTED_OS = {
  "ubuntu": (22, 4),
  "debian": (12,),
}
SUPPORTED_OS_TEXT = "Ubuntu 22.04以上、Debian 12以上"
MIN_PYTHON = (3, 10)
VOICEVOX_TIMEOUT = 5.0


class CheckError(RuntimeError):
  """インストールを続けられない環境であることを表す。"""


@dataclass(frozen=True)
class OsInfo:
  id: str
  version: str
  pretty_name: str


@dataclass
class PreflightResult:
  os: OsInfo
  installed_packages: list[str] = field(default_factory=list)


# OS

def read_os_release(system: System) -> dict[str, str]:
  for candidate in ("/etc/os-release", "/usr/lib/os-release"):
    path = system.path(candidate)
    if path.is_file():
      break
  else:
    raise CheckError("/etc/os-release が見つからないため、OSを確かめられません")
  values: dict[str, str] = {}
  for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    key, value = line.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
      value = value[1:-1]
    values[key.strip()] = value
  return values


def _parse_version(text: str) -> tuple[int, ...] | None:
  try:
    return tuple(int(part) for part in text.split("."))
  except ValueError:
    return None


def check_os(system: System) -> OsInfo:
  values = read_os_release(system)
  os_id = values.get("ID", "").lower()
  version = values.get("VERSION_ID", "")
  pretty = values.get("PRETTY_NAME", f"{os_id} {version}".strip())
  supported = SUPPORTED_OS_TEXT
  if os_id not in SUPPORTED_OS:
    raise CheckError(f"{pretty} には対応していません。対応しているのは {supported} です")
  parsed = _parse_version(version)
  if parsed is None or parsed < SUPPORTED_OS[os_id]:
    raise CheckError(f"{pretty} は古いため対応していません。対応しているのは {supported} です")
  return OsInfo(os_id, version, pretty)


# systemd と Python

def check_systemd(system: System) -> None:
  # systemd で起動したシステムにだけ、このディレクトリがある
  if not system.path("/run/systemd/system").is_dir():
    raise CheckError("systemd で動いていないため、サービスとして登録できません")


def check_python(version_info: tuple[int, ...] = tuple(sys.version_info[:3])) -> None:
  if tuple(version_info[:2]) < MIN_PYTHON:
    current = ".".join(str(part) for part in version_info[:3])
    raise CheckError(f"Python 3.10以上が必要です（今は {current}）")


# パッケージ

def venv_package(version_info: tuple[int, ...] = tuple(sys.version_info[:2])) -> str:
  """このPythonで venv を作るのに要るパッケージ名（例: python3.10-venv）。"""
  return f"python{version_info[0]}.{version_info[1]}-venv"


def is_package_installed(system: System, package: str) -> bool:
  result = system.run(
    ["dpkg-query", "-W", "-f=${Status}", package],
    check=False,
  )
  return result.returncode == 0 and result.stdout.strip() == "install ok installed"


def missing_packages(
    system: System,
    *,
    python: str = sys.executable,
    version_info: tuple[int, ...] = tuple(sys.version_info[:2]),
) -> list[str]:
  missing = []
  # venv の作成に要る ensurepip は、Debian系では pythonX.Y-venv に入っている
  if system.run([python, "-c", "import ensurepip"], check=False).returncode != 0:
    missing.append(venv_package(version_info))
  if not is_package_installed(system, "ffmpeg"):
    missing.append("ffmpeg")
  return missing


def install_packages(system: System, ui: UI, packages: list[str]) -> None:
  if not packages:
    return
  ui.info("次のパッケージが入っていないので、apt で入れます: " + " ".join(packages))
  if not ui.confirm("入れてよいですか？", default=True):
    raise UserAbort("必要なパッケージを入れないため、中止しました")
  env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
  system.run(["apt-get", "update"], env=env)
  system.run(["apt-get", "install", "-y", "--no-install-recommends", *packages], env=env)
  ui.info("入れました: " + " ".join(packages))


def run_preflight(
    system: System,
    ui: UI,
    *,
    python: str = sys.executable,
    version_info: tuple[int, ...] = tuple(sys.version_info[:3]),
) -> PreflightResult:
  """OS・systemd・Python を確かめ、足りないパッケージを入れる。"""
  ui.step("事前チェック")
  os_info = check_os(system)
  ui.info(f"OS: {os_info.pretty_name}")
  check_systemd(system)
  ui.info("systemd: あり")
  check_python(version_info)
  ui.info("Python: " + ".".join(str(part) for part in version_info[:3]))
  packages = missing_packages(system, python=python, version_info=version_info)
  install_packages(system, ui, packages)
  if not packages:
    ui.info("必要なパッケージ: すべて入っています")
  return PreflightResult(os_info, packages)


# VOICEVOX

def check_voicevox_url(value: str) -> str | None:
  parsed = urllib.parse.urlsplit(value)
  if parsed.scheme not in ("http", "https") or not parsed.netloc:
    return "VOICEVOXのURLは http:// か https:// から始めてください（例: http://127.0.0.1:50021）"
  if parsed.query or parsed.fragment:
    return "VOICEVOXのURLに ? や # は使えません"
  return None


def probe_voicevox(url: str, timeout: float = VOICEVOX_TIMEOUT) -> str:
  """/version に接続し、VOICEVOXのバージョンを返す。つながらなければ CheckError。"""
  # VOICEVOX は同じ機械か内部のネットワークにあるので、プロキシを通さない
  opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
  target = url.rstrip("/") + "/version"
  try:
    with opener.open(target, timeout=timeout) as response:
      body = response.read().decode("utf-8", errors="replace")
  except urllib.error.HTTPError as error:
    raise CheckError(f"{target} が {error.code} を返しました") from None
  except (urllib.error.URLError, OSError) as error:
    reason = getattr(error, "reason", error)
    raise CheckError(f"{target} に接続できません: {reason}") from None
  try:
    version = json.loads(body)
  except ValueError:
    raise CheckError(f"{target} の応答がVOICEVOXのものではありません") from None
  if not isinstance(version, str):
    raise CheckError(f"{target} の応答がVOICEVOXのものではありません")
  return version


@dataclass(frozen=True)
class VoicevoxResult:
  url: str
  # 接続できたときのバージョン。起動せずに入れることを選んだときは None
  version: str | None


VOICEVOX_CHOICES = ("retry", "skip", "abort")


def confirm_voicevox(
    ui: UI,
    default_url: str,
    *,
    timeout: float = VOICEVOX_TIMEOUT,
) -> VoicevoxResult:
  """VOICEVOXのURLを聞き、実際に接続して確かめる。"""
  ui.step("VOICEVOX")
  url = ui.ask("VOICEVOXのURL", default_url, validate=check_voicevox_url).rstrip("/")
  while True:
    try:
      version = probe_voicevox(url, timeout)
    except CheckError as error:
      if ui.assume_yes:
        raise CheckError(f"{error}。VOICEVOXを起動してから、もう一度実行してください") from None
      ui.warn(str(error))
      ui.info("VOICEVOXにつながらないままでも、インストールだけは済ませられます。")
      ui.info("その場合、Botは起動しません（VOICEVOXを起動してから start で動かします）。")
      ui.info("  retry: もう一度接続する")
      ui.info("  skip : Botを起動せずにインストールだけ済ませる")
      ui.info("  abort: 中止する")
      choice = ui.choose("どうしますか", list(VOICEVOX_CHOICES), "retry")
      if choice == "retry":
        continue
      if choice == "skip":
        return VoicevoxResult(url, None)
      raise UserAbort("VOICEVOXにつながらないため、中止しました")
    ui.info(f"VOICEVOX: 接続できました（バージョン {version}）")
    return VoicevoxResult(url, version)
