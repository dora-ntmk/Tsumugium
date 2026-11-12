"""専用ユーザー・ディレクトリの権限・systemd unit。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from installer.instance import ENV_FILE_KEY, VOICEVOX_URL_KEY, InstanceRecord, Layout
from installer.system import System


UNIT_DIR = "/etc/systemd/system"
UNIT_MARKER = "# Managed by tsumugium-setup"
NOLOGIN = "/usr/sbin/nologin"


class ServiceError(RuntimeError):
  """ユーザー・unit の操作を続けられないことを表す。"""


def unit_name(service: str) -> str:
  return f"{service}.service"


def unit_path(system: System, service: str) -> Path:
  return system.path(f"{UNIT_DIR}/{unit_name(service)}")


# 専用ユーザー

def user_exists(system: System, user: str) -> bool:
  return system.run(["id", "-u", user], check=False).returncode == 0


def ensure_user(system: System, user: str, home: str) -> bool:
  """専用ユーザーがいなければ作る。作ったときは True を返す。"""
  if user_exists(system, user):
    return False
  system.run([
    "useradd",
    "--system",
    "--user-group",
    "--home-dir", home,
    "--no-create-home",
    "--shell", NOLOGIN,
    user,
  ])
  return True


# ディレクトリ

def prepare_directories(system: System, layout: Layout, user: str) -> None:
  """入れる場所と data/ 以下を作り、持ち主と権限をそろえる。

  入れる場所は root の持ち物（Botは読むだけ）、data/ 以下は専用ユーザーの持ち物にする。
  """
  prefix = system.path(str(layout.prefix))
  prefix.mkdir(parents=True, exist_ok=True)
  os.chmod(prefix, 0o755)
  system.run(["chown", "root:root", str(layout.prefix)])
  for directory in (layout.data, layout.db, layout.backup, layout.tmp, layout.state):
    path = system.path(str(directory))
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o750)
  system.run(["chown", "-R", f"{user}:{user}", str(layout.data)])


def secure_app(system: System, app_dir: str) -> None:
  """コードの置き場所を root の持ち物にし、Botからは読むだけにする。"""
  system.run(["chown", "-R", "root:root", app_dir])
  system.run(["chmod", "-R", "u=rwX,go=rX", app_dir])


# unit ファイル

def _quote(value: str) -> str:
  """Environment= の値を、空白や記号を含んでも崩れない形にする。"""
  escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
  return f'"{escaped}"'


def unit_environment(record: InstanceRecord) -> dict[str, str]:
  """unit で Bot に渡す環境変数。これらは .env より優先される。"""
  return {
    "PYTHONUNBUFFERED": "1",
    ENV_FILE_KEY: str(record.layout.env_file),
    **record.layout.bot_environment(),
    VOICEVOX_URL_KEY: record.voicevox_url,
  }


def render_unit(record: InstanceRecord) -> str:
  layout = record.layout
  environment = "\n".join(
    f"Environment={_quote(f'{key}={value}')}"
    for key, value in unit_environment(record).items()
  )
  return f"""\
{UNIT_MARKER}
# このファイルは tsumugium-setup が作りました。直接書き換えても、次の更新で元に戻ります。
[Unit]
Description=Tsumugium ({record.instance})
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={record.user}
Group={record.user}
WorkingDirectory={layout.app}
ExecStart={layout.venv_python} main.py
{environment}
Restart=on-failure
RestartSec=10
ProtectSystem=strict
ReadWritePaths={layout.data}
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
"""


def is_managed_unit(path: Path) -> bool:
  try:
    with open(path, encoding="utf-8") as file:
      return file.readline().rstrip("\n") == UNIT_MARKER
  except (OSError, UnicodeDecodeError):
    return False


def _check_can_write(path: Path, service: str) -> None:
  if path.exists() and not is_managed_unit(path):
    raise ServiceError(
      f"{UNIT_DIR}/{unit_name(service)} は tsumugium-setup が作ったものではないため、上書きしません。"
      "別のサービス名を選ぶか、元のunitの名前を変えてから、もう一度実行してください"
    )


def write_unit(system: System, record: InstanceRecord) -> bool:
  """unit を書き、systemd に読み直させる。中身が変わらなければ何もせず False を返す。"""
  path = unit_path(system, record.service)
  _check_can_write(path, record.service)
  text = render_unit(record)
  if path.is_file() and path.read_text(encoding="utf-8") == text:
    return False
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".service")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as file:
      file.write(text)
      file.flush()
      os.fsync(file.fileno())
    os.chmod(temp, 0o644)
    os.replace(temp, path)
  except BaseException:
    try:
      os.remove(temp)
    except FileNotFoundError:
      pass
    raise
  Systemctl(system).daemon_reload()
  return True


def remove_unit(system: System, service: str) -> bool:
  """tsumugium-setup が作った unit だけを消す。消したときは True を返す。"""
  path = unit_path(system, service)
  if not path.exists():
    return False
  _check_can_write(path, service)
  path.unlink()
  Systemctl(system).daemon_reload()
  return True


class Systemctl:
  """systemctl の操作。"""

  def __init__(self, system: System):
    self.system = system

  def _run(self, *args: str, check: bool = True):
    return self.system.run(["systemctl", *args], check=check)

  def daemon_reload(self) -> None:
    self._run("daemon-reload")

  def enable(self, service: str) -> None:
    self._run("enable", unit_name(service))

  def disable(self, service: str) -> None:
    self._run("disable", unit_name(service))

  def start(self, service: str) -> None:
    self._run("start", unit_name(service))

  def stop(self, service: str, *, check: bool = True) -> None:
    self._run("stop", unit_name(service), check=check)

  def restart(self, service: str) -> None:
    self._run("restart", unit_name(service))

  def is_active(self, service: str) -> bool:
    return self._run("is-active", "--quiet", unit_name(service), check=False).returncode == 0

  def is_enabled(self, service: str) -> bool:
    return self._run("is-enabled", "--quiet", unit_name(service), check=False).returncode == 0
