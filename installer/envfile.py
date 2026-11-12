"""`.env` の扱い（探す・確かめる・更新時に足りない項目を足す）。

インストーラーは `.env` を作らない。利用者が `.env.template` をもとに用意したものを使う。
値（トークンなど）は画面に出さない。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from installer.instance import VOICEVOX_URL_KEY, Layout
from installer.system import System
from installer.ui import UI


TOKEN_KEY = "DISCORD_BOT_TOKEN"
TOKEN_PLACEHOLDER = "YOUR_DISCORD_BOT_TOKEN"
# テンプレートで、項目の直前のコメントにこの行があれば必須とみなす
REQUIRED_MARK = "# 必須"
# テンプレートに印がなくても必須として扱う項目
ALWAYS_REQUIRED = (TOKEN_KEY,)

_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


class EnvError(RuntimeError):
  """`.env` がないか、内容に問題があることを表す。"""


def _parse_value(raw: str) -> str:
  raw = raw.strip()
  if raw[:1] in ("'", '"'):
    quote = raw[0]
    end = raw.find(quote, 1)
    while quote == '"' and end > 0 and raw[end - 1] == "\\":
      end = raw.find(quote, end + 1)
    if end > 0:
      value = raw[1:end]
      return value.replace('\\"', '"') if quote == '"' else value
    return raw
  # 引用符なしの値では、空白の後の # からをコメントとみなす
  match = re.search(r"\s#", raw)
  return (raw[:match.start()] if match else raw).strip()


@dataclass
class EnvFile:
  """行をそのまま保ったまま `.env` を扱う。"""

  lines: list[str] = field(default_factory=list)

  @classmethod
  def parse(cls, text: str) -> "EnvFile":
    return cls(text.splitlines())

  @classmethod
  def read(cls, path: Path) -> "EnvFile":
    return cls.parse(path.read_text(encoding="utf-8"))

  def values(self) -> dict[str, str]:
    """項目名 → 値。同じ項目が何度も書かれていれば、後のものを使う（python-dotenv と同じ）。"""
    result: dict[str, str] = {}
    for line in self.lines:
      if line.lstrip().startswith("#"):
        continue
      match = _ASSIGN_RE.match(line)
      if match:
        result[match.group(1)] = _parse_value(match.group(2))
    return result

  def keys(self) -> list[str]:
    return list(self.values())

  def get(self, key: str) -> str | None:
    return self.values().get(key)

  def append(self, blocks: list[list[str]], heading: str) -> None:
    if self.lines and self.lines[-1].strip():
      self.lines.append("")
    self.lines.append(heading)
    for block in blocks:
      self.lines.extend(block)

  def text(self) -> str:
    return "\n".join(self.lines) + "\n" if self.lines else ""


@dataclass(frozen=True)
class TemplateEntry:
  key: str
  # 直前のコメントと、項目の行（そのまま足せる形）
  block: tuple[str, ...]
  required: bool


def parse_template(text: str) -> list[TemplateEntry]:
  entries: list[TemplateEntry] = []
  comments: list[str] = []
  seen: set[str] = set()
  for line in text.splitlines():
    stripped = line.strip()
    if not stripped:
      comments = []
      continue
    if stripped.startswith("#"):
      comments.append(line)
      continue
    match = _ASSIGN_RE.match(line)
    if match and match.group(1) not in seen:
      key = match.group(1)
      seen.add(key)
      required = key in ALWAYS_REQUIRED or any(
        comment.strip() == REQUIRED_MARK for comment in comments
      )
      entries.append(TemplateEntry(key, tuple(comments) + (line,), required))
    comments = []
  return entries


def check_token(value: str | None) -> str | None:
  """トークンの形を確かめる。ネットワークには接続しない。"""
  if not value:
    return f"{TOKEN_KEY} が空欄です"
  if value == TOKEN_PLACEHOLDER:
    return f"{TOKEN_KEY} がテンプレートのまま（{TOKEN_PLACEHOLDER}）です"
  if not _TOKEN_RE.fullmatch(value):
    return f"{TOKEN_KEY} がDiscordのトークンの形になっていません"
  return None


def required_problems(env: EnvFile, template: list[TemplateEntry]) -> list[str]:
  values = env.values()
  required = [entry.key for entry in template if entry.required]
  for key in ALWAYS_REQUIRED:
    if key not in required:
      required.append(key)
  problems = []
  for key in required:
    if key == TOKEN_KEY:
      problem = check_token(values.get(key))
      if problem:
        problems.append(problem)
    elif not values.get(key):
      problems.append(f"{key} が空欄です")
  return problems


def managed_keys_in(env: EnvFile, layout: Layout) -> list[str]:
  """unit の Environment= で渡すため、`.env` に書いても使われない項目。"""
  managed = [*layout.bot_environment(), VOICEVOX_URL_KEY]
  values = env.values()
  return [key for key in managed if values.get(key)]


def _write_private(system: System, path: Path, text: str, user: str) -> None:
  """権限600で書き、持ち主を専用ユーザーにする。一時ファイルから置き換えるので途中で壊れない。"""
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-env-")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as file:
      file.write(text)
      file.flush()
      os.fsync(file.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)
  except BaseException:
    try:
      os.remove(temp)
    except FileNotFoundError:
      pass
    raise
  _chown(system, path, user)


def _chown(system: System, path: Path, user: str) -> None:
  absolute = "/" + str(path.relative_to(system.root))
  system.run(["chown", f"{user}:{user}", absolute])


def _edit_steps(layout: Layout, template_hint: str) -> str:
  return "\n".join([
    "次の手順で .env を用意してから、もう一度実行してください。",
    f"  sudo mkdir -p {layout.prefix}",
    f"  sudo cp {template_hint} {layout.env_file}",
    f"  sudo nano {layout.env_file}    # {TOKEN_KEY} にトークンを書く",
  ])


def _report_problems(problems: list[str], layout: Layout, action: str) -> EnvError:
  lines = [f".env に問題があるため、{action}を止めます。"]
  lines += [f"  - {problem}" for problem in problems]
  lines.append(f"{layout.env_file} を直してから、もう一度実行してください。")
  return EnvError("\n".join(lines))


def _warn_managed(ui: UI, env: EnvFile, layout: Layout) -> None:
  keys = managed_keys_in(env, layout)
  if keys:
    ui.warn(
      ".env の次の項目は、インストーラーが決めた値をunitで渡すため使われません: " + ", ".join(keys)
    )


def install_env(
    system: System,
    ui: UI,
    layout: Layout,
    extracted: Path,
    user: str,
) -> EnvFile:
  """インストール時: `.env` を探して確かめ、入れる場所に置く。

  探す順番は、入れる場所の `.env`、展開したディレクトリの `.env`。
  """
  ui.step(".env")
  target = system.path(str(layout.env_file))
  candidates = [target, extracted / ".env"]
  source = next((path for path in candidates if path.is_file()), None)
  if source is None:
    raise EnvError(
      ".env が見つかりません。\n"
      + _edit_steps(layout, str(extracted / ".env.template"))
    )
  env = EnvFile.read(source)
  template_path = extracted / ".env.template"
  template = parse_template(template_path.read_text(encoding="utf-8")) if template_path.is_file() else []
  problems = required_problems(env, template)
  if problems:
    if source != target:
      ui.info(f"見つけた .env: {source}")
    raise _report_problems(problems, layout, "インストール")
  if source != target:
    _write_private(system, target, env.text(), user)
    ui.info(f"{source} を {layout.env_file} に置きました")
  else:
    os.chmod(target, 0o600)
    _chown(system, target, user)
    ui.info(f"{layout.env_file} を使います")
  _warn_managed(ui, env, layout)
  return env


def upgrade_env(
    system: System,
    ui: UI,
    layout: Layout,
    template_path: Path,
    user: str,
    version: str,
) -> list[str]:
  """更新時: テンプレートにあって `.env` にない項目を足し、必須の項目を確かめる。

  足した項目の名前を返す。足す前に `.env.bak` を作る。
  """
  ui.step(".env")
  target = system.path(str(layout.env_file))
  if not target.is_file():
    raise EnvError(f"{layout.env_file} が見つかりません")
  env = EnvFile.read(target)
  template = parse_template(template_path.read_text(encoding="utf-8"))
  present = set(env.keys())
  missing = [entry for entry in template if entry.key not in present]
  if missing:
    backup = system.path(str(layout.env_backup))
    shutil.copy2(target, backup)
    os.chmod(backup, 0o600)
    _chown(system, backup, user)
    env.append(
      [list(entry.block) for entry in missing],
      f"# ↓ tsumugium-setup が {version} への更新で追加した項目",
    )
    _write_private(system, target, env.text(), user)
    ui.info(f"{layout.env_file} に次の項目を足しました（元のものは {layout.env_backup}）:")
    for entry in missing:
      ui.info(f"  {entry.key}" + ("（必須）" if entry.required else ""))
  else:
    ui.info(".env: 足りない項目はありません")
  problems = required_problems(env, template)
  if problems:
    raise _report_problems(problems, layout, "更新")
  _warn_managed(ui, env, layout)
  return [entry.key for entry in missing]
