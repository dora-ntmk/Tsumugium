"""
ファイル名：config.py
作者：どら
説明：設定値モジュール。
      .env ファイルから環境変数を読み込み、プロジェクト全体で使用する定数として公開する。
依存関係：python-dotenv
"""
from dotenv import load_dotenv
load_dotenv()

import os


def _optional_user_id(value: str | None) -> int | None:
  if value is None or not value.strip():
    return None
  try:
    user_id = int(value)
  except ValueError:
    print("OPERATOR_USER_ID must be a Discord user ID; DM notifications are disabled")
    return None
  if user_id <= 0:
    print("OPERATOR_USER_ID must be a Discord user ID; DM notifications are disabled")
    return None
  return user_id

STATUS_MESSAGE = os.getenv("STATUS_MESSAGE", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPERATOR_USER_ID = _optional_user_id(os.getenv("OPERATOR_USER_ID"))
SERVER_CONFIG_DB  = os.getenv("SERVER_CONFIG_DB", "db/config.db")
DICT_DB      = os.getenv("DICT_DB", "db/dict.db")
SOUND_BOARDS_DB   = os.getenv("SOUND_BOARDS_DB", "db/soundboards.db")
EMOJI_JA_JSON     = os.getenv("EMOJI_JA_JSON", "db/emoji_ja.json")
SPEAKERS_JSON     = os.getenv("SPEAKERS_JSON", "db/speakers.json")
DEFAULT_SPEAKER      = int(os.getenv("DEFAULT_SPEAKER", "8"))
TMP_DIR           = os.getenv("TMP_DIR", "tmp")
VOICEVOX_URL      = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
BACKUP_DIR           = os.getenv("BACKUP_DIR", "backup")
BACKUP_TIMES         = os.getenv("BACKUP_TIMES", "")
BACKUP_INTERVAL_DAYS = os.getenv("BACKUP_INTERVAL_DAYS", "1")
BACKUP_KEEP          = os.getenv("BACKUP_KEEP", "7")

# バージョン情報
VERSION      = "3.6.0.68"
LAST_UPDATED = "2026-08-31"
