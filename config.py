from dotenv import load_dotenv
load_dotenv()

import os

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SERVER_CONFIG_DB  = os.getenv("SERVER_CONFIG_DB", "db/config.db")
WORD_DICT_DB      = os.getenv("WORD_DICT_DB", "db/dict.db")
SOUND_DICT_DB     = os.getenv("SOUND_DICT_DB", "db/sounddict.db")
SOUND_BOARDS_DB   = os.getenv("SOUND_BOARDS_DB", "db/soundboards.db")
EMOJI_JA_JSON     = os.getenv("EMOJI_JA_JSON", "db/emoji_ja.json")
SPEAKERS_JSON     = os.getenv("SPEAKERS_JSON", "speakers.json")
TMP_DIR           = os.getenv("TMP_DIR", "tmp")
MESSAGES_DIR      = os.getenv("MESSAGES_DIR", "messages")
VOICEVOX_URL      = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
BACKUP_DIR           = os.getenv("BACKUP_DIR", "backup")
BACKUP_TIMES         = os.getenv("BACKUP_TIMES", "")
BACKUP_INTERVAL_DAYS = os.getenv("BACKUP_INTERVAL_DAYS", "1")
BACKUP_KEEP          = os.getenv("BACKUP_KEEP", "7")