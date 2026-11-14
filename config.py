"""
ファイル名：config.py
作者：どら
説明：設定値モジュール。
      .env ファイルから環境変数を読み込み、プロジェクト全体で使用する定数として公開する。
依存関係：python-dotenv
"""
import os

from dotenv import load_dotenv

# インストーラーで入れたときは、unit が TSUMUGIUM_ENV_FILE で読む .env を指定する。
# 指定がなければ、今までどおり .env を探して読む。
# どちらの場合も、すでにある環境変数（unit の Environment= など）は上書きしない。
load_dotenv(os.getenv("TSUMUGIUM_ENV_FILE") or None)


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
USER_CONFIG_DB    = os.getenv("USER_CONFIG_DB", "db/users.db")
EMOJI_JA_JSON     = os.getenv("EMOJI_JA_JSON", "db/emoji_ja.json")
SPEAKERS_JSON     = os.getenv("SPEAKERS_JSON", "db/speakers.json")
DEFAULT_SPEAKER      = int(os.getenv("DEFAULT_SPEAKER", "8"))
TMP_DIR           = os.getenv("TMP_DIR", "tmp")
VOICEVOX_URL      = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
BACKUP_DIR           = os.getenv("BACKUP_DIR", "backup")
# インストーラーへ起動の準備完了を知らせる ready.json の置き場所（空欄なら書かない）
UPDATE_STATE_DIR     = os.getenv("UPDATE_STATE_DIR", "")
BACKUP_TIMES         = os.getenv("BACKUP_TIMES", "12:00")
BACKUP_INTERVAL_DAYS = os.getenv("BACKUP_INTERVAL_DAYS", "1")
BACKUP_KEEP          = os.getenv("BACKUP_KEEP", "7")

# バージョン情報
VERSION      = "v4.3.0.71 (beta 11)"
LAST_UPDATED = "2026/11/14"
