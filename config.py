"""
ファイル名：config.py
作者：どら
説明：設定値モジュール。
      .env ファイルから環境変数を読み込み、プロジェクト全体で使用する定数として公開する。
依存関係：python-dotenv
"""
import math
import os

from dotenv import load_dotenv

# インストーラーで入れたときは、unit が TSUMUGIUM_ENV_FILE で読む .env を指定する。
# 指定がなければ、今までどおり .env を探して読む。
# どちらの場合も、すでにある環境変数（unit の Environment= など）は上書きしない。
load_dotenv(os.getenv("TSUMUGIUM_ENV_FILE") or None)


def _user_ids(value: str | None) -> tuple[int, ...]:
  """カンマ区切りのDiscordユーザーIDを読む。読めない項目は表示して飛ばす。"""
  user_ids: list[int] = []
  for item in (value or "").split(","):
    item = item.strip()
    if not item:
      continue
    try:
      user_id = int(item)
    except ValueError:
      user_id = 0
    if user_id <= 0:
      print(f"OPERATOR_USER_ID: {item!r} is not a Discord user ID; it is ignored")
      continue
    if user_id not in user_ids:
      user_ids.append(user_id)
  return tuple(user_ids)

# ストリーミングのモードを auto（/synthesis set で決めていないときも同じ）にしたときに使う、このバージョンの既定。
# あとのリリースでここを変えると、auto の環境にはそのまま効く（off と決めた環境は変わらない）
DEFAULT_VOICEVOX_STREAMING = "hybrid"


def parse_streaming_mode(value: str | None, name: str = "VOICEVOX_STREAMING") -> str:
  """ストリーミングのモードを読み、"off"・"on"・"hybrid"のどれかを返す。

  運営者が /synthesis set で保存した値（config.db）と、前の版の .env から取り込む値に使う。

  autoと空欄は、このバージョンの既定（DEFAULT_VOICEVOX_STREAMING）に従う。
  onとoffは、true・false、1・0、yes・noでも書ける。読めない値はoffにする。
  """
  text = (value or "").strip().lower()
  if text in ("", "auto"):
    return DEFAULT_VOICEVOX_STREAMING
  if text == "hybrid":
    return "hybrid"
  if text in ("on", "true", "1", "yes"):
    return "on"
  if text not in ("off", "false", "0", "no"):
    print(f"{name} must be auto, on, off or hybrid; it is treated as off")
  return "off"


def parse_stream_threshold_chars(value: str | None, name: str = "VOICEVOX_STREAM_THRESHOLD") -> int:
  """hybridの基準の文字数を読む。0・auto・空欄は自動調整。読めない値・負の値は0にする。"""
  text = (value or "").strip()
  if not text or text.lower() == "auto":
    return 0
  try:
    chars = int(text)
  except ValueError:
    chars = -1
  if chars < 0:
    print(
      f"{name} must be 0 (automatic) or a number of characters; "
      "it is treated as 0 (automatic)"
    )
    return 0
  return chars


def parse_stream_segment_seconds(value: str | None, name: str = "VOICEVOX_STREAM_SEGMENT") -> float:
  """ストリーミングで一度に作る音声の長さ（秒）を読む。0・auto・空欄は自動調整。読めない値・負の値は0にする。"""
  text = (value or "").strip()
  if not text or text.lower() == "auto":
    return 0.0
  try:
    seconds = float(text)
  except ValueError:
    seconds = -1.0
  if not math.isfinite(seconds) or seconds < 0:
    print(
      f"{name} must be 0 (automatic) or seconds; "
      "it is treated as 0 (automatic)"
    )
    return 0.0
  return seconds

STATUS_MESSAGE = os.getenv("STATUS_MESSAGE", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
# 運営者（エラー・更新のDMを受け取り、運営者用コマンドを使える人）。カンマ区切りで複数人を書ける
OPERATOR_USER_IDS = _user_ids(os.getenv("OPERATOR_USER_ID"))
SERVER_CONFIG_DB  = os.getenv("SERVER_CONFIG_DB", "db/config.db")
DICT_DB      = os.getenv("DICT_DB", "db/dict.db")
SOUND_BOARDS_DB   = os.getenv("SOUND_BOARDS_DB", "db/soundboards.db")
USER_CONFIG_DB    = os.getenv("USER_CONFIG_DB", "db/users.db")
EMOJI_JA_JSON     = os.getenv("EMOJI_JA_JSON", "db/emoji_ja.json")
SPEAKERS_JSON     = os.getenv("SPEAKERS_JSON", "db/speakers.json")
DEFAULT_SPEAKER      = int(os.getenv("DEFAULT_SPEAKER", "8"))
TMP_DIR           = os.getenv("TMP_DIR", "tmp")
VOICEVOX_URL      = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
# 実験的な機能: VOICEVOXの/streaming_synthesisの設定は、運営者が /synthesis set で決めて config.db に保存する。
# 前の版で .env に書いていた値は、起動時に一度だけ config.db へ取り込む（以後 .env の値は使わない）。
# 行がない項目は None
LEGACY_VOICEVOX_STREAMING_ENV = {
  key: os.environ.get(key)
  for key in ("VOICEVOX_STREAMING", "VOICEVOX_STREAM_THRESHOLD", "VOICEVOX_STREAM_SEGMENT")
}
BACKUP_DIR           = os.getenv("BACKUP_DIR", "backup")
# インストーラーへ起動の準備完了を知らせる ready.json の置き場所（空欄なら書かない）
UPDATE_STATE_DIR     = os.getenv("UPDATE_STATE_DIR", "")
BACKUP_TIMES         = os.getenv("BACKUP_TIMES", "12:00")
BACKUP_INTERVAL_DAYS = os.getenv("BACKUP_INTERVAL_DAYS", "1")
BACKUP_KEEP          = os.getenv("BACKUP_KEEP", "7")

# バージョン情報（AGENTS.md「バージョン表記」）
# VERSION には人が決めたこと（次の版、正式版にするか）だけを書く。ベータ番号と LAST_UPDATED は、
# CIが配布物を作るときに決めて書き込むので、PRで上げない
VERSION      = "v4.4.1.73"
LAST_UPDATED = "最終更新日（自動挿入）"
