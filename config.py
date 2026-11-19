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

# VOICEVOX_STREAMING を auto（または空欄）にしたときに使う、このバージョンの既定。
# あとのリリースでここを変えると、auto の環境にはそのまま効く（off と書いた環境は変わらない）
DEFAULT_VOICEVOX_STREAMING = "off"


def _streaming_mode(value: str | None) -> str:
  """VOICEVOX_STREAMINGを読み、"off"・"on"・"hybrid"のどれかを返す。

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
    print("VOICEVOX_STREAMING must be auto, on, off or hybrid; it is treated as off")
  return "off"


def _stream_threshold_chars(value: str | None) -> int:
  """VOICEVOX_STREAM_THRESHOLDを読む（文字数）。0・auto・空欄は自動調整。読めない値・負の値は0にする。"""
  text = (value or "").strip()
  if not text or text.lower() == "auto":
    return 0
  try:
    chars = int(text)
  except ValueError:
    chars = -1
  if chars < 0:
    print(
      "VOICEVOX_STREAM_THRESHOLD must be 0 (automatic) or a number of characters; "
      "it is treated as 0 (automatic)"
    )
    return 0
  return chars


def _stream_segment_seconds(value: str | None) -> float:
  """VOICEVOX_STREAM_SEGMENTを読む（秒）。0・auto・空欄は自動調整。読めない値・負の値は0にする。"""
  text = (value or "").strip()
  if not text or text.lower() == "auto":
    return 0.0
  try:
    seconds = float(text)
  except ValueError:
    seconds = -1.0
  if not math.isfinite(seconds) or seconds < 0:
    print(
      "VOICEVOX_STREAM_SEGMENT must be 0 (automatic) or seconds; "
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
# 実験的な機能: VOICEVOXの/streaming_synthesisで、音声を作り終える前に読み上げを始める。
# "off"・"on"・"hybrid"（VOICEVOX_STREAM_THRESHOLDで振り分け、/synthesis-log で比べる）。
# auto は、このバージョンの既定（DEFAULT_VOICEVOX_STREAMING）に従う
VOICEVOX_STREAMING = _streaming_mode(os.getenv("VOICEVOX_STREAMING", "auto"))
# hybridで、この文字数以上ならストリーミングする。0は自動調整（今は五分五分で振り分ける）
VOICEVOX_STREAM_THRESHOLD = _stream_threshold_chars(
  os.getenv("VOICEVOX_STREAM_THRESHOLD", "0")
)
# /streaming_synthesisで一度に作る音声の長さ（秒）。0は自動調整（今は1.0秒に固定）
VOICEVOX_STREAM_SEGMENT = _stream_segment_seconds(
  os.getenv("VOICEVOX_STREAM_SEGMENT", "0")
)
BACKUP_DIR           = os.getenv("BACKUP_DIR", "backup")
# インストーラーへ起動の準備完了を知らせる ready.json の置き場所（空欄なら書かない）
UPDATE_STATE_DIR     = os.getenv("UPDATE_STATE_DIR", "")
BACKUP_TIMES         = os.getenv("BACKUP_TIMES", "12:00")
BACKUP_INTERVAL_DAYS = os.getenv("BACKUP_INTERVAL_DAYS", "1")
BACKUP_KEEP          = os.getenv("BACKUP_KEEP", "7")

# バージョン情報
VERSION      = "v4.4.0.72 (beta 17)"
LAST_UPDATED = "2026/11/16"
