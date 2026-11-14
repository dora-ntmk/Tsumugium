"""インストーラーの結合テスト（開発用・CI専用。配布物には含めない）。

GitHub Actions の使い捨てのUbuntuで、本物の sudo・useradd・systemd・pip を使い、
install から uninstall までを通しで動かす。手元のマシンでは実行しないこと
（/opt/tsumugium、/etc/tsumugium、ユーザー tsumugium などを作って消す）。

使い方（プロジェクトルートで、sudo できる一般ユーザーとして実行）:
  python3 scripts/ci_installer_e2e.py --lock requirements-e2e.lock

requirements.lock は配布物と同じ作り方で作ったものを渡す（本物の依存を入れられることも確かめる）。

Bot本体は、ready.json などを書いて待つだけの偽物に差し替える（Discordには接続しない）。
VOICEVOXの代わりに、127.0.0.1:50021 で /version だけを返すサーバーを立てる。
"""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from installer.release import extract_release  # noqa: E402

_spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
build_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_release)


INSTANCE = "tsumugium"
PREFIX = f"/opt/{INSTANCE}"
SERVICE = f"{INSTANCE}.service"
UPDATE_SERVICE = f"{INSTANCE}-update.service"
UPDATE_TIMER = f"{INSTANCE}-update.timer"
VERSION_A = "v0.0.1.1"
VERSION_B = "v0.0.2.2"
VERSION_C = "v0.0.3.3"
# Discordのトークンと同じ形の、明らかな偽物
TOKEN = "dummy-token.for-ci.only"

# 偽のBot。.env と unit の値を読めたかを env-check.json に書き、DBの代わりのファイルに
# 自分の版を書き足してから、ready.json を書いて待つ。
FAKE_MAIN = '''"""CIの結合テスト用の偽のBot（Discordには接続しない）。"""

import json
import os
import signal
import sys
import time

from config import DISCORD_BOT_TOKEN, SERVER_CONFIG_DB, UPDATE_STATE_DIR, VERSION
from services.ready_state_service import ReadyStateService

state = ReadyStateService(UPDATE_STATE_DIR, VERSION)


def stop(*_):
  state.close()
  sys.exit(0)


signal.signal(signal.SIGTERM, stop)
with open(os.path.join(UPDATE_STATE_DIR, "env-check.json"), "w", encoding="utf-8") as file:
  json.dump({"token_set": bool(DISCORD_BOT_TOKEN), "server_config_db": SERVER_CONFIG_DB}, file)
with open(SERVER_CONFIG_DB, "a", encoding="utf-8") as file:
  file.write(VERSION + "\\n")
state.mark_ready()
print(f"fake bot {VERSION} ready", flush=True)
while True:
  time.sleep(60)
'''

CRASH_MAIN = '''"""CIの結合テスト用の、起動するとすぐ落ちる偽のBot。"""

import sys

print("fake bot crashes", flush=True)
sys.exit(1)
'''


class Failure(Exception):
  pass


# コマンド

def run(args, *, check=True):
  print("$ " + " ".join(args), flush=True)
  result = subprocess.run(args, text=True, capture_output=True, stdin=subprocess.DEVNULL)
  for stream in (result.stdout, result.stderr):
    if stream.strip():
      print(stream.rstrip(), flush=True)
  if check and result.returncode != 0:
    raise Failure(f"終了コード {result.returncode}: {' '.join(args)}")
  return result


def sudo(*args, check=True):
  return run(["sudo", *args], check=check)


def setup(*args, expect=0):
  result = sudo("tsumugium-setup", *args, check=False)
  if result.returncode != expect:
    raise Failure(f"tsumugium-setup {' '.join(args)} の終了コードが {result.returncode}（期待 {expect}）")
  return result


def check(condition, message):
  if not condition:
    raise Failure(message)
  print(f"  OK: {message}", flush=True)


# 状態を読む

def read_json(path):
  return json.loads(sudo("cat", path).stdout)


def record():
  return read_json(f"/etc/tsumugium/{INSTANCE}.json")


def is_active():
  return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0


def owner_and_mode(path):
  return sudo("stat", "-c", "%U:%G %a", path).stdout.strip()


def exists(path):
  return subprocess.run(["sudo", "test", "-e", path]).returncode == 0


def app_version(directory):
  return read_json(f"{PREFIX}/{directory}/build-info.json")["version"]


def db_lines():
  return sudo("cat", f"{PREFIX}/data/db/config.db").stdout.split()


def status_text():
  return setup("status").stdout


def timer_enabled():
  return subprocess.run(["systemctl", "is-enabled", "--quiet", UPDATE_TIMER]).returncode == 0


def assert_running(version):
  check(is_active(), f"{SERVICE} が動いている")
  check(record()["version"] == version, f"記録の版が {version}")
  ready = read_json(f"{PREFIX}/data/state/ready.json")
  check(ready["version"] == version, f"ready.json の版が {version}")


# 準備

def start_fake_voicevox():
  class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
      if self.path != "/version":
        self.send_response(404)
        self.end_headers()
        return
      self.send_response(200)
      self.send_header("Content-Type", "application/json")
      self.end_headers()
      self.wfile.write(b'"0.0.0-ci"')

    def log_message(self, *args):
      pass

  server = http.server.HTTPServer(("127.0.0.1", 50021), Handler)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  return server


def make_zip(work: Path, version: str, main_text: str, lock: Path) -> Path:
  """リポジトリを写し、VERSION と main.py を差し替えて配布用zipを作る。"""
  source = work / f"src-{version}"
  files = [path for path in build_release.tracked_files(ROOT) if (ROOT / path).is_file()]
  for path in files:
    target = source / path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / path, target)
  config = source / "config.py"
  text, count = re.subn(
    r'^VERSION(\s*)=\s*"[^"]+"', f'VERSION\\1= "{version}"', config.read_text(encoding="utf-8"),
    count=1, flags=re.MULTILINE,
  )
  if count != 1:
    raise Failure("config.py の VERSION を書き換えられません")
  config.write_text(text, encoding="utf-8")
  (source / "main.py").write_text(main_text, encoding="utf-8")
  zip_path, _, info = build_release.build(source, work / "dist", lock, files=files, commit="0" * 40)
  print(f"zipを作りました: {zip_path.name}（{info['version']}）", flush=True)
  return zip_path


# 流れ

def scenario(work: Path, zips: dict[str, Path]) -> None:
  admin = os.environ.get("SUDO_USER") or os.environ.get("USER") or "runner"

  print("\n== 1. install（版A） ==", flush=True)
  extracted = work / "extract-a"
  extract_release(zips["a"], extracted)
  # .env には、unit で渡す値と食い違うパスも書いておく（unit の値が使われることを確かめる）
  (extracted / ".env").write_text(
    f"DISCORD_BOT_TOKEN={TOKEN}\nSERVER_CONFIG_DB=db/config.db\n", encoding="utf-8"
  )
  result = sudo(str(extracted / "install.sh"), "install", "--yes", "--admin", admin, check=False)
  if result.returncode != 0:
    raise Failure("install が失敗しました")
  check("使われません" in result.stderr, ".env の SERVER_CONFIG_DB が使われないことを知らせた")
  assert_running(VERSION_A)
  env_check = read_json(f"{PREFIX}/data/state/env-check.json")
  check(env_check["token_set"], "Botが TSUMUGIUM_ENV_FILE の .env からトークンを読めた")
  check(
    env_check["server_config_db"] == f"{PREFIX}/data/db/config.db",
    "unit で渡したDBのパスが .env より優先された",
  )
  check(owner_and_mode(f"{PREFIX}/.env") == f"root:{INSTANCE} 640", ".env は root:<グループ> の640")
  check(owner_and_mode(f"{PREFIX}/app").startswith("root:root "), "app/ は root の持ち物")
  check(owner_and_mode(f"{PREFIX}/data") == f"{INSTANCE}:{INSTANCE} 750", "data/ は専用ユーザーの750")
  check(db_lines() == [VERSION_A], "Botが data/ に書き込めた")
  check(admin in sudo("getent", "group", INSTANCE).stdout, f"{admin} がグループに入った")
  check(exists("/usr/local/sbin/tsumugium-setup"), "tsumugium-setup が置かれた")
  check("稼働中" in status_text(), "status が稼働中を表示する")

  print("\n== 1b. 自動更新の timer ==", flush=True)
  for unit in (UPDATE_SERVICE, UPDATE_TIMER):
    check(exists(f"/etc/systemd/system/{unit}"), f"{unit} が置かれた")
    verify = sudo("systemd-analyze", "verify", f"/etc/systemd/system/{unit}", check=False)
    check(verify.returncode == 0, f"{unit} を systemd が正しく読める（{verify.stderr.strip()}）")
  check(not timer_enabled(), "stable の既定なので、自動更新の timer は無効")
  check("自動更新    オフ" in status_text(), "status が自動更新オフを表示する")
  # オフのときは GitHub に問い合わせずに終わる（CIでは本物のReleasesを取りに行かない）
  setup("upgrade", "--auto", "--instance", INSTANCE)
  check(read_json(f"{PREFIX}/data/state/auto-update.json")["result"] == "off", "オフなので何もしなかった")
  setup("auto-update", "on")
  check(timer_enabled(), "auto-update on で timer が有効になった")
  check(record()["auto_update"] is True, "記録に auto_update が残った")
  check("自動更新    オン（10分おき）" in status_text(), "status が自動更新オンを表示する")
  setup("auto-update", "off")
  check(not timer_enabled(), "auto-update off で timer が無効になった")

  print("\n== 2. upgrade（版B） ==", flush=True)
  setup("upgrade", "--yes", "--from-file", str(zips["b"]))
  assert_running(VERSION_B)
  check(app_version("app.prev") == VERSION_A, "app.prev/ が版A")
  check(db_lines() == [VERSION_A, VERSION_B], "DBが引き継がれた")
  backups = sudo("ls", f"{PREFIX}/data/backup").stdout.split()
  check(any(name.endswith(f"-{VERSION_A}") for name in backups), "更新前のDBのバックアップがある")

  print("\n== 3. upgrade（すぐ落ちる版C）→ 自動で版Bに戻る ==", flush=True)
  result = setup("upgrade", "--yes", "--from-file", str(zips["c"]), expect=1)
  check("ロールバック" in result.stdout, "ロールバックした")
  assert_running(VERSION_B)
  check(record().get("updating") is None, "更新中の印が消えた")
  check(app_version("app") == VERSION_B, "app/ が版B")
  check(app_version("app.prev") == VERSION_A, "app.prev/（版A）が残っている")
  check(VERSION_C in record()["skip_versions"], "起動できなかった版Cを、自動更新で飛ばす版にした")
  last = read_json(f"{PREFIX}/data/state/last-update.json")
  check((last["result"], last["to"]) == ("rolled_back", VERSION_C), "last-update.json に戻したことが残った")

  print("\n== 4. rollback（版Aへ、DBも戻す） ==", flush=True)
  setup("rollback", "--yes")
  assert_running(VERSION_A)
  check(app_version("app.prev") == VERSION_B, "app.prev/ に版Bが残っている")
  check(VERSION_B not in db_lines(), "DBが版Bに更新する前の状態に戻った")

  print("\n== 5. stop・start ==", flush=True)
  setup("stop")
  check(not is_active(), "止まった")
  check("停止中（手動）" in status_text(), "status が停止中（手動）を表示する")
  setup("stop", "--disable")
  check("停止中（自動起動も無効）" in status_text(), "status が自動起動も無効を表示する")
  setup("start")
  assert_running(VERSION_A)

  print("\n== 6. repair（版Aを入れ直す） ==", flush=True)
  setup("repair", "--yes", "--from-file", str(zips["a"]))
  assert_running(VERSION_A)
  check(app_version("app.prev") == VERSION_B, "repair しても app.prev/ はそのまま")

  print("\n== 7. uninstall ==", flush=True)
  setup("uninstall", "--yes")
  check(not is_active(), "サービスが止まった")
  check(not exists(f"/etc/systemd/system/{SERVICE}"), "unit が消えた")
  check(not exists(f"/etc/systemd/system/{UPDATE_TIMER}"), "自動更新の timer が消えた")
  check(not exists(f"/etc/systemd/system/{UPDATE_SERVICE}"), "自動更新の service が消えた")
  check(not exists(f"/etc/tsumugium/{INSTANCE}.json"), "記録が消えた")
  check(not exists(f"{PREFIX}/app"), "コードが消えた")
  check(exists(f"{PREFIX}/data/db/config.db"), "DBは残った")
  check(exists(f"{PREFIX}/.env"), ".env は残った")
  check(subprocess.run(["id", INSTANCE], capture_output=True).returncode != 0, "専用ユーザーが消えた")
  check(not exists("/usr/local/sbin/tsumugium-setup"), "tsumugium-setup が消えた")


def show_logs() -> None:
  print("\n== 失敗したときの記録 ==", flush=True)
  sudo("journalctl", "-u", SERVICE, "-n", "100", "--no-pager", check=False)
  sudo("cat", f"/etc/tsumugium/{INSTANCE}.json", check=False)
  sudo("ls", "-la", PREFIX, check=False)


def main(argv=None) -> int:
  parser = argparse.ArgumentParser(description="インストーラーの結合テスト（CI専用）")
  parser.add_argument("--lock", required=True, help="配布物と同じ作り方で作った requirements.lock")
  args = parser.parse_args(argv)
  if os.geteuid() == 0:
    print("sudo できる一般ユーザーとして実行してください", file=sys.stderr)
    return 2

  work = Path(tempfile.mkdtemp(prefix="tsumugium-e2e-", dir=os.environ.get("RUNNER_TEMP")))
  os.chmod(work, 0o755)
  lock = Path(args.lock).resolve()
  server = start_fake_voicevox()
  try:
    zips = {
      "a": make_zip(work, VERSION_A, FAKE_MAIN, lock),
      "b": make_zip(work, VERSION_B, FAKE_MAIN, lock),
      "c": make_zip(work, VERSION_C, CRASH_MAIN, lock),
    }
    scenario(work, zips)
  except Failure as error:
    print(f"\n失敗: {error}", file=sys.stderr, flush=True)
    show_logs()
    return 1
  finally:
    server.shutdown()
  print("\nすべて通りました", flush=True)
  return 0


if __name__ == "__main__":
  sys.exit(main())
