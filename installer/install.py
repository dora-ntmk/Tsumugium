"""install モード: 展開したzipの中身を、初めて入れる。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from installer.autoupdate import apply_auto_update
from installer.checks import (
  VoicevoxResult,
  confirm_voicevox,
  run_preflight,
)
from installer.deploy import build_venv
from installer.entry import ENTRY_PATH, install_entry
from installer.envfile import find_env, install_env
from installer.instance import (
  CHANNELS,
  DEFAULT_INSTANCE,
  default_auto_update,
  DEFAULT_REPO,
  DEFAULT_VOICEVOX_URL,
  InstanceError,
  InstanceLock,
  InstanceRecord,
  Layout,
  check_channel,
  check_name,
  check_prefix,
  check_repo,
  list_instances,
  save_record,
)
from installer.readiness import READY_TIMEOUT, ReadyError, clear_ready, wait_ready
from installer.service import (
  Systemctl,
  ensure_user,
  is_managed_unit,
  prepare_directories,
  unit_name,
  unit_path,
  user_exists,
  write_unit,
)
from installer.system import System
from installer.ui import UI, UserAbort


EXIT_OK = 0
EXIT_ERROR = 1
STEPS = 10

# 展開したディレクトリのうち、コードとしてコピーしないもの（一番上の階層だけを見る）
NOT_CODE = {".env", "data", "old", ".venv", "app", "app.new", "app.prev"}


def default_source_dir() -> Path:
  """このインストーラーが入っている、展開したディレクトリ。"""
  return Path(__file__).resolve().parent.parent


def add_arguments(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("--user", help="専用ユーザー名（既定はインスタンス名）")
  parser.add_argument("--service", help="サービス名（既定はインスタンス名）")
  parser.add_argument("--prefix", help="入れる場所（既定は /opt/<インスタンス名>）")
  parser.add_argument("--repo", help=f"取得元のリポジトリ（既定は {DEFAULT_REPO}）")
  parser.add_argument("--channel", choices=CHANNELS, help="チャンネル（既定は stable）")
  parser.add_argument("--admin", help="グループに入れる管理者のユーザー名")
  parser.add_argument("--voicevox-url", help=f"VOICEVOXのURL（既定は {DEFAULT_VOICEVOX_URL}）")
  parser.add_argument(
    "--auto-update",
    action=argparse.BooleanOptionalAction,
    default=None,
    help="自動更新（10分おきに新しい版を確かめて入れる）を使うか（既定は beta ならオン、stable ならオフ）",
  )


def _display_width(text: str) -> int:
  """端末での表示幅（全角は2文字分）。"""
  return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def read_build_info(source: Path) -> dict:
  path = source / "build-info.json"
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    raise InstanceError(
      f"{path} がありません。配布用のzipを展開したディレクトリの install.sh から実行してください"
    ) from None
  except (OSError, ValueError) as error:
    raise InstanceError(f"{path} を読み込めません: {error}") from None
  if not isinstance(data, dict) or not isinstance(data.get("version"), str):
    raise InstanceError(f"{path} の形式が正しくありません")
  return data


@dataclass
class Answers:
  instance: str
  user: str
  user_exists: bool
  service: str
  prefix: str
  repo: str
  channel: str
  auto_update: bool
  admin: str | None
  voicevox: VoicevoxResult


class Installer:
  def __init__(
      self,
      args: argparse.Namespace,
      system: System,
      ui: UI,
      *,
      source: Path,
      python: str,
      version_info: tuple[int, ...],
      wait: Callable[..., object],
  ):
    self.args = args
    self.system = system
    self.ui = ui
    self.source = source
    self.python = python
    self.version_info = version_info
    self.wait = wait

  def step(self, number: int, title: str) -> None:
    self.ui.step(f"{number}/{STEPS} {title}")

  def shown(self, path: Path) -> str:
    """システム上のパスとして表示する（テストではルートが一時ディレクトリになる）。"""
    try:
      return "/" + str(path.relative_to(self.system.root))
    except ValueError:
      return str(path)

  # 質問

  def _option_or_ask(self, value, question, default, validate):
    if value is not None:
      problem = validate(value)
      if problem:
        raise UserAbort(f"{question}: {problem}")
      self.ui.info(f"{question}: {value}（オプションで指定）")
      return value
    return self.ui.ask(question, default, validate=validate)

  def _check_instance(self, value: str) -> str | None:
    problem = check_name(value, "インスタンス名")
    if problem:
      return problem
    if value in list_instances(self.system):
      return (
        f"インスタンス {value} はすでにあります。"
        "更新は sudo tsumugium-setup upgrade、入れ直しは repair を使ってください"
      )
    return None

  def _check_service(self, value: str) -> str | None:
    problem = check_name(value, "サービス名")
    if problem:
      return problem
    path = unit_path(self.system, value)
    if path.exists() and not is_managed_unit(path):
      return f"{unit_name(value)} はすでにあり、tsumugium-setup が作ったものではありません。別の名前にしてください"
    return None

  def _check_prefix(self, value: str) -> str | None:
    problem = check_prefix(value)
    if problem:
      return problem
    layout = Layout.of(value)
    for directory in (layout.app, layout.app_prev):
      if self.system.path(str(directory)).exists():
        return f"{directory} がすでにあります。別の場所にするか、repair を使ってください"
    return None

  def _check_admin(self, value: str) -> str | None:
    if value == "":
      return None
    problem = check_name(value, "管理者のユーザー名")
    if problem:
      return problem
    if not user_exists(self.system, value):
      return f"ユーザー {value} が見つかりません"
    return None

  def ask_questions(self) -> Answers:
    args = self.args
    self.step(2, "インスタンス名")
    instance = self._option_or_ask(
      args.instance, "インスタンス名", DEFAULT_INSTANCE, self._check_instance
    )

    self.step(3, "専用ユーザー")
    user = self._option_or_ask(
      args.user, "専用ユーザー名", instance, lambda value: check_name(value, "ユーザー名")
    )
    exists = user_exists(self.system, user)
    self.ui.info(f"ユーザー {user} はすでにあります。そのまま使います" if exists
                 else f"ユーザー {user} はまだありません。作成します")

    self.step(4, "サービス名")
    service = self._option_or_ask(args.service, "サービス名", instance, self._check_service)

    self.step(5, "入れる場所")
    prefix = self._option_or_ask(args.prefix, "入れる場所", f"/opt/{instance}", self._check_prefix)

    self.step(6, "取得元")
    repo = self._option_or_ask(args.repo, "取得元のリポジトリ", DEFAULT_REPO, check_repo)
    if repo != DEFAULT_REPO:
      self.ui.warn(
        f"取得元が既定（{DEFAULT_REPO}）ではありません。"
        "更新ではここから取得したプログラムを root で動かします。信頼できる取得元か確かめてください"
      )

    self.step(7, "チャンネル")
    if args.channel is not None:
      channel = args.channel
      self.ui.info(f"チャンネル: {channel}（オプションで指定）")
    else:
      channel = self.ui.choose("チャンネルを選んでください", list(CHANNELS), "stable")
    problem = check_channel(channel)
    if problem:
      raise UserAbort(problem)
    if args.auto_update is not None:
      auto_update = args.auto_update
      self.ui.info(f"自動更新: {'オン' if auto_update else 'オフ'}（オプションで指定）")
    else:
      auto_update = self.ui.confirm(
        "自動更新（10分おきに新しい版を確かめて入れる）を使いますか？",
        default=default_auto_update(channel),
      ) if not self.ui.assume_yes else default_auto_update(channel)

    self.step(8, "管理者")
    admin = self._option_or_ask(
      args.admin,
      f"{user} グループに入れる管理者のユーザー名（空欄なら入れません）",
      "",
      self._check_admin,
    ) or None

    voicevox = confirm_voicevox(
      self.ui,
      args.voicevox_url or DEFAULT_VOICEVOX_URL,
      ask_url=args.voicevox_url is None,
      title=f"9/{STEPS} VOICEVOX",
    )
    return Answers(instance, user, exists, service, prefix, repo, channel, auto_update, admin, voicevox)

  def show_summary(self, answers: Answers, version: str) -> None:
    self.step(10, "確認")
    voicevox = answers.voicevox
    rows = [
      ("インスタンス名", answers.instance),
      ("専用ユーザー", answers.user + ("" if answers.user_exists else "（新しく作成）")),
      ("サービス名", unit_name(answers.service)),
      ("入れる場所", answers.prefix),
      ("取得元", answers.repo),
      ("チャンネル", {"stable": "stable（安定版）", "beta": "beta（ベータ）"}[answers.channel]),
      ("自動更新", "オン（10分おき）" if answers.auto_update else "オフ"),
      ("管理者", f"{answers.admin}（{answers.user} グループに追加）" if answers.admin else "なし"),
      ("VOICEVOX", voicevox.url + ("" if voicevox.version else "（つながらないため、起動はしません）")),
      ("バージョン", version),
    ]
    self.ui.info("次の内容でインストールします")
    width = max(_display_width(label) for label, _ in rows) + 2
    for label, value in rows:
      self.ui.info(f"  {label}{' ' * (width - _display_width(label))}{value}")

  # インストール

  def _import_data(self, layout: Layout, user: str) -> bool:
    """展開したディレクトリの data/ を、入れる場所の data/ が空なら取り込む（移行用）。"""
    imported = self.source / "data"
    if not imported.is_dir():
      return False
    target = self.system.path(str(layout.data))
    if any(path.is_file() for path in target.rglob("*")):
      self.ui.warn(f"{layout.data} にすでにファイルがあるため、{imported} は取り込みません")
      return False
    shutil.copytree(imported, target, dirs_exist_ok=True)
    self.system.run(["chown", "-R", f"{user}:{user}", str(layout.data)])
    self.ui.info(f"{imported} を {layout.data} に取り込みました")
    return True

  def _deploy_code(self, layout: Layout) -> None:
    """コードを app.new/ にコピーし、venvを作り、依存を入れてから app/ にする。"""
    app_new = self.system.path(str(layout.app_new))
    if app_new.exists():
      shutil.rmtree(app_new)
    shutil.copytree(
      self.source,
      app_new,
      ignore=lambda directory, names: NOT_CODE.intersection(names)
      if Path(directory) == self.source else set(),
      symlinks=True,
    )
    self.ui.info(f"コードを {layout.app_new} に置きました")

    build_venv(self.system, self.ui, self.python, layout.app_new)
    os.rename(app_new, self.system.path(str(layout.app)))
    self.ui.info(f"コードを {layout.app} に配置しました")

  def install(self, answers: Answers, version: str, env_source: Path) -> InstanceRecord:
    record = InstanceRecord(
      instance=answers.instance,
      user=answers.user,
      service=answers.service,
      prefix=answers.prefix,
      repo=answers.repo,
      channel=answers.channel,
      version=version,
      admin_user=answers.admin,
      voicevox_url=answers.voicevox.url,
      auto_update=answers.auto_update,
    )
    record.validate()
    layout = record.layout
    self.ui.step("インストール")
    if ensure_user(self.system, record.user, record.prefix):
      self.ui.info(f"ユーザー {record.user} を作成しました")
    if record.admin_user:
      self.system.run(["usermod", "-aG", record.user, record.admin_user])
      self.ui.info(f"{record.admin_user} を {record.user} グループに入れました（次のログインから有効）")
    prepare_directories(self.system, layout, record.user)
    self.ui.info(f"{layout.prefix} を用意しました")
    install_env(self.system, self.ui, layout, self.source, record.user, source=env_source)
    self._import_data(layout, record.user)
    self._deploy_code(layout)
    write_unit(self.system, record)
    self.ui.info(f"systemd unit を作成しました: {unit_name(record.service)}")
    apply_auto_update(self.system, record)
    self.ui.info(f"自動更新の timer を作成しました（自動更新は{'オン' if record.auto_update else 'オフ'}）")
    save_record(self.system, record)
    self.ui.info(f"記録を作成しました: /etc/tsumugium/{record.instance}.json")
    install_entry(self.system)
    self.ui.info(f"tsumugium-setup を {ENTRY_PATH} に配置しました")
    return record

  def start(self, record: InstanceRecord, voicevox: VoicevoxResult) -> int:
    systemctl = Systemctl(self.system)
    service = unit_name(record.service)
    self.ui.step("起動")
    systemctl.enable(record.service)
    if voicevox.version is None:
      self.ui.info(f"{service} の自動起動を有効にしました。VOICEVOXにつながらないため、起動はしていません")
      self.ui.info("VOICEVOXを起動してから、次のコマンドでBotを動かしてください:")
      self.ui.info(f"  sudo systemctl start {service}")
      return EXIT_OK
    clear_ready(self.system, record.layout)
    systemctl.start(record.service)
    self.ui.info(f"{service} を起動しました")
    self.ui.info(f"起動の完了を待っています（最大{int(READY_TIMEOUT)}秒）...")
    try:
      self.wait(self.system, record.layout, record.service, record.version)
    except ReadyError as error:
      self.ui.error(str(error))
      self.ui.info("インストールは済んでいます。原因を直してから、次のコマンドで起動してください:")
      self.ui.info(f"  sudo systemctl restart {service}")
      return EXIT_ERROR
    self.ui.info("起動を確認しました")
    return EXIT_OK

  def finish(self, record: InstanceRecord) -> None:
    service = unit_name(record.service)
    self.ui.info("")
    self.ui.info("インストールが完了しました。")
    self.ui.info("  状態を見る:   sudo tsumugium-setup status")
    self.ui.info(f"  ログを見る:   sudo journalctl -u {service} -f")
    self.ui.info(f"  設定を変える: sudo nano {record.layout.env_file} のあと sudo systemctl restart {service}")
    self.ui.info("  更新する:     sudo tsumugium-setup upgrade")
    if record.auto_update_enabled:
      self.ui.info("  自動更新:     オン（10分おきに新しい版を確かめます。止めるときは sudo tsumugium-setup auto-update off）")
    else:
      self.ui.info("  自動更新:     オフ（新しい版はDMで知らせます。自動にするときは sudo tsumugium-setup auto-update on）")

  def run(self) -> int:
    build = read_build_info(self.source)
    version = build["version"]
    self.ui.info(f"Tsumugium インストーラー {version}")

    self.step(1, ".env")
    default_instance = self.args.instance or DEFAULT_INSTANCE
    default_layout = Layout.of(self.args.prefix or f"/opt/{default_instance}")
    env_source = find_env(self.system, self.source, default_layout)
    self.ui.info(f"見つかりました: {self.shown(env_source)}")
    self.ui.info("DISCORD_BOT_TOKEN が設定されています")

    run_preflight(self.system, self.ui, python=self.python, version_info=self.version_info)
    answers = self.ask_questions()
    self.show_summary(answers, version)
    if not self.ui.confirm("この内容でインストールしますか？", default=False):
      raise UserAbort("インストールを中止しました")

    with InstanceLock(self.system, answers.instance):
      # 質問している間に、同じ名前で入れられていないかを確かめ直す
      problem = self._check_instance(answers.instance) or self._check_prefix(answers.prefix)
      if problem:
        raise InstanceError(problem)
      record = self.install(answers, version, env_source)
      code = self.start(record, answers.voicevox)
    if code == EXIT_OK:
      self.finish(record)
    return code


def handle(
    args: argparse.Namespace,
    system: System,
    ui: UI,
    *,
    source: Path | None = None,
    python: str = sys.executable,
    version_info: tuple[int, ...] = tuple(sys.version_info[:3]),
    wait: Callable[..., object] = wait_ready,
) -> int:
  installer = Installer(
    args,
    system,
    ui,
    source=Path(source) if source is not None else default_source_dir(),
    python=python,
    version_info=version_info,
    wait=wait,
  )
  return installer.run()
