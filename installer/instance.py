"""インスタンスの名前・ディレクトリ構成・記録・ロック。"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from installer.system import System
from installer.ui import UI


RECORD_DIR = "/etc/tsumugium"
LOCK_DIR = "/run/lock"
RECORD_FORMAT = 1

DEFAULT_INSTANCE = "tsumugium"
DEFAULT_REPO = "dora-ntmk/Tsumugium"
DEFAULT_VOICEVOX_URL = "http://127.0.0.1:50021"
CHANNELS = ("stable", "beta")

# Linuxのユーザー名とsystemdのunit名の両方に使える名前（小文字・数字・_・-、32文字まで）
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FORBIDDEN_PREFIX_ROOTS = ("/home", "/root", "/tmp", "/run", "/proc", "/sys", "/dev")
_RESERVED_NAMES = {"root", "daemon", "bin", "sys", "nobody"}
# unitファイルにそのまま書けるよう、空白や % などを含む場所は受け付けない
_PREFIX_CHARS_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class InstanceError(RuntimeError):
  """インスタンスの記録や名前に問題があることを表す。"""


class LockBusy(InstanceError):
  """同じインスタンスで、ほかの処理が実行中であることを表す。"""


# unit の Environment= で渡し、.env に書いても使われない項目
VOICEVOX_URL_KEY = "VOICEVOX_URL"
# Bot が読む .env の場所を unit で渡すための変数
ENV_FILE_KEY = "TSUMUGIUM_ENV_FILE"


# 名前の検証。問題がなければ None、あればエラーの文を返す（UI.ask の validate に渡せる形）。

def check_name(value: str, what: str = "名前") -> str | None:
  if not _NAME_RE.fullmatch(value):
    return (
      f"{what}は、小文字の英字か _ で始め、小文字の英字・数字・_・- だけを使い、"
      "32文字以内にしてください"
    )
  if value in _RESERVED_NAMES:
    return f"{value} は{what}に使えません"
  return None


def check_prefix(value: str) -> str | None:
  path = PurePosixPath(value)
  if not path.is_absolute():
    return "入れる場所は / から始まる絶対パスにしてください"
  if ".." in path.parts:
    return "入れる場所に .. は使えません"
  if not _PREFIX_CHARS_RE.fullmatch(value):
    return "入れる場所には、英数字と . _ - / だけを使ってください"
  if path == PurePosixPath("/"):
    return "/ には入れられません"
  for forbidden in _FORBIDDEN_PREFIX_ROOTS:
    if path == PurePosixPath(forbidden) or PurePosixPath(forbidden) in path.parents:
      return f"{forbidden} の下には入れられません（専用ユーザーが読めないか、消える場所です）"
  return None


def check_repo(value: str) -> str | None:
  if not _REPO_RE.fullmatch(value):
    return "取得元は 所有者/リポジトリ名 の形で入力してください"
  return None


def check_channel(value: str) -> str | None:
  if value not in CHANNELS:
    return f"チャンネルは {' / '.join(CHANNELS)} のどれかにしてください"
  return None


@dataclass(frozen=True)
class Layout:
  """入れる場所の配下の各パス（システム上の絶対パス）。"""

  prefix: PurePosixPath

  @classmethod
  def of(cls, prefix: str) -> "Layout":
    return cls(PurePosixPath(prefix))

  @property
  def env_file(self) -> PurePosixPath:
    return self.prefix / ".env"

  @property
  def env_backup(self) -> PurePosixPath:
    return self.prefix / ".env.bak"

  @property
  def app(self) -> PurePosixPath:
    return self.prefix / "app"

  @property
  def app_prev(self) -> PurePosixPath:
    return self.prefix / "app.prev"

  @property
  def app_new(self) -> PurePosixPath:
    return self.prefix / "app.new"

  @property
  def venv_python(self) -> PurePosixPath:
    return self.app / ".venv" / "bin" / "python"

  @property
  def data(self) -> PurePosixPath:
    return self.prefix / "data"

  @property
  def db(self) -> PurePosixPath:
    return self.data / "db"

  @property
  def backup(self) -> PurePosixPath:
    return self.data / "backup"

  @property
  def tmp(self) -> PurePosixPath:
    return self.data / "tmp"

  @property
  def state(self) -> PurePosixPath:
    return self.data / "state"

  @property
  def ready_file(self) -> PurePosixPath:
    return self.state / "ready.json"

  def bot_environment(self) -> dict[str, str]:
    """systemd unit の Environment= で渡す、インストーラーが管理するパス。"""
    return {
      "SERVER_CONFIG_DB": str(self.db / "config.db"),
      "DICT_DB": str(self.db / "dict.db"),
      "SOUND_BOARDS_DB": str(self.db / "soundboards.db"),
      "USER_CONFIG_DB": str(self.db / "users.db"),
      "TMP_DIR": str(self.tmp),
      "BACKUP_DIR": str(self.backup),
      "UPDATE_STATE_DIR": str(self.state),
    }


@dataclass
class InstanceRecord:
  """/etc/tsumugium/<インスタンス名>.json に残す、インストール時に選んだ値。"""

  instance: str
  user: str
  service: str
  prefix: str
  repo: str = DEFAULT_REPO
  channel: str = "stable"
  version: str | None = None
  admin_user: str | None = None
  voicevox_url: str = DEFAULT_VOICEVOX_URL
  # 切り替えの前に書き、終わったら消す。途中で止まったことを次の実行で見つけるために使う。
  updating: dict | None = None
  # Botを止めたまま入れ替えたとき、まだ起動を確かめていない版
  unverified: str | None = None
  format: int = field(default=RECORD_FORMAT)

  @property
  def layout(self) -> Layout:
    return Layout.of(self.prefix)

  def validate(self) -> None:
    problems = [
      check_name(self.instance, "インスタンス名"),
      check_name(self.user, "ユーザー名"),
      check_name(self.service, "サービス名"),
      check_prefix(self.prefix),
      check_repo(self.repo),
      check_channel(self.channel),
    ]
    if self.admin_user is not None:
      problems.append(check_name(self.admin_user, "管理者のユーザー名"))
    problems = [problem for problem in problems if problem]
    if problems:
      raise InstanceError("記録の内容が正しくありません: " + " / ".join(problems))


def record_path(system: System, instance: str) -> Path:
  return system.path(f"{RECORD_DIR}/{instance}.json")


def save_record(system: System, record: InstanceRecord) -> Path:
  """記録を書く。一時ファイルに書いてから置き換えるので、途中で止まっても壊れない。"""
  record.validate()
  path = record_path(system, record.instance)
  path.parent.mkdir(parents=True, exist_ok=True)
  data = json.dumps(asdict(record), ensure_ascii=False, indent=2) + "\n"
  fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as file:
      file.write(data)
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
  return path


def load_record(system: System, instance: str) -> InstanceRecord:
  path = record_path(system, instance)
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    raise InstanceError(f"インスタンス {instance} は見つかりません") from None
  except (OSError, ValueError) as error:
    raise InstanceError(f"{path} を読み込めません: {error}") from None
  if not isinstance(data, dict):
    raise InstanceError(f"{path} の形式が正しくありません")
  if data.get("format") != RECORD_FORMAT:
    raise InstanceError(
      f"{path} の形式（format={data.get('format')!r}）に対応していません。"
      "新しいバージョンのインストーラーで操作してください"
    )
  known = set(InstanceRecord.__dataclass_fields__)
  try:
    record = InstanceRecord(**{key: value for key, value in data.items() if key in known})
  except TypeError as error:
    raise InstanceError(f"{path} に必要な項目がありません: {error}") from None
  if record.instance != instance:
    raise InstanceError(f"{path} のインスタンス名が一致しません")
  record.validate()
  return record


def delete_record(system: System, instance: str) -> None:
  try:
    record_path(system, instance).unlink()
  except FileNotFoundError:
    pass


def list_instances(system: System) -> list[str]:
  directory = system.path(RECORD_DIR)
  if not directory.is_dir():
    return []
  return sorted(
    path.stem
    for path in directory.glob("*.json")
    if not path.name.startswith(".") and check_name(path.stem) is None
  )


def select_instance(system: System, ui: UI, requested: str | None) -> str:
  """--instance の指定、またはインストール済みのものから対象を決める。"""
  instances = list_instances(system)
  if requested is not None:
    if requested not in instances:
      raise InstanceError(f"インスタンス {requested} は見つかりません")
    return requested
  if not instances:
    raise InstanceError("インストールされたインスタンスがありません")
  if len(instances) == 1:
    return instances[0]
  if ui.assume_yes:
    raise InstanceError(
      "インスタンスが複数あります。--instance で指定してください: " + ", ".join(instances)
    )
  return ui.choose("対象のインスタンスを選んでください", instances, instances[0])


class InstanceLock:
  """インスタンスごとのロック。ほかの処理が持っていれば、待たずに LockBusy を出す。"""

  def __init__(self, system: System, instance: str):
    self.path = system.path(f"{LOCK_DIR}/tsumugium-{instance}.lock")
    self.instance = instance
    self._file = None

  def __enter__(self) -> "InstanceLock":
    self.path.parent.mkdir(parents=True, exist_ok=True)
    self._file = open(self.path, "a+")
    try:
      fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
      self._file.close()
      self._file = None
      if error.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
        raise LockBusy(
          f"インスタンス {self.instance} では、ほかの処理が実行中です。"
          "終わってからもう一度実行してください"
        ) from None
      raise
    return self

  def __exit__(self, *exc_info) -> None:
    if self._file is not None:
      fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
      self._file.close()
      self._file = None
