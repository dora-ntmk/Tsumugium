"""Tsumugiumの依存関係を組み立て、Discord Botを起動する。"""

import discord

from clients.discord_soundboard_client import DiscordSoundboardClient
from clients.managed_discord_client import ManagedDiscordClient
from clients.voicevox_client import VoicevoxClient
from cogs.connection_cog import ConnectionCog
from cogs.general_cog import GeneralCog
from cogs.lifecycle_cog import LifecycleCog
from cogs.playback_cog import PlaybackCog
from config import (
  DICT_DB,
  DISCORD_BOT_TOKEN,
  LAST_UPDATED,
  OPERATOR_USER_ID,
  SERVER_CONFIG_DB,
  SOUND_BOARDS_DB,
  STATUS_MESSAGE,
  TMP_DIR,
  USER_CONFIG_DB,
  VERSION,
  VOICEVOX_URL,
)
from repositories.guild_config_repository import GuildConfigRepository
from repositories.user_config_repository import UserConfigRepository
from services.connection_service import ConnectionService
from services.error_notification_service import ErrorNotificationService
from services.message_service import MessageService
from services.speech_service import SpeechService
from services.status_service import StatusService
from services.voice_service import VoiceService
from setting import Setting
from sound_dict import SoundDict, SoundDictView, UpdateSoundBoards
from word_dict import DictManager, WordDict


intents = discord.Intents.default()
intents.message_content = True
client = ManagedDiscordClient(intents=intents, enable_debug_events=True)
tree = discord.app_commands.CommandTree(client)
error_notifier = ErrorNotificationService(client, OPERATOR_USER_ID)
client.set_error_notifier(error_notifier)

voicevox_client = VoicevoxClient(
  VOICEVOX_URL,
  tmp_dir=TMP_DIR,
  error_notifier=error_notifier,
)


class VoicevoxStartupError(RuntimeError):
  """VOICEVOX疎通失敗によってBotの起動を中止する例外。"""


async def check_voicevox_on_startup() -> None:
  try:
    engine_version = await voicevox_client.check_health()
  except Exception:
    raise VoicevoxStartupError(
      f"VOICEVOXへ接続できないため起動を中止します: {VOICEVOX_URL}"
    ) from None
  print(f"VOICEVOX接続確認完了: {engine_version}")


client.register_startup_check(check_voicevox_on_startup)
removed_tmp_wavs = voicevox_client.cleanup_tmp_wav_files()
if removed_tmp_wavs:
  print(f"起動時に一時WAVファイルを{removed_tmp_wavs}件削除しました")
discord_soundboard_client = DiscordSoundboardClient(DISCORD_BOT_TOKEN)
client.register_closeable(voicevox_client)
client.register_closeable(discord_soundboard_client)

server_config = GuildConfigRepository(
  SERVER_CONFIG_DB,
  error_notifier=error_notifier,
)
dict_manager = DictManager(DICT_DB, error_notifier)
user_config = UserConfigRepository(USER_CONFIG_DB)
sound_dict = SoundDict(dict_manager)
sound_boards = UpdateSoundBoards(
  SOUND_BOARDS_DB,
  dict_manager,
  discord_soundboard_client,
  error_notifier,
)
client.register_closeable(server_config)
client.register_closeable(dict_manager)
client.register_closeable(user_config)
client.register_closeable(sound_boards)

voice_service = VoiceService(
  client,
  discord_soundboard_client,
  error_notifier,
)
speech_service = SpeechService(
  voicevox_client,
  server_config,
  dict_manager,
  voice_service,
)
connection_service = ConnectionService(
  server_config,
  dict_manager,
  speech_service,
  voice_service,
  error_notifier,
)
status_service = StatusService(client, STATUS_MESSAGE, error_notifier)
client.register_closeable(status_service)
message_service = MessageService(
  client,
  server_config,
  speech_service,
  voice_service,
  connection_service,
  error_notifier,
)

playback_cog = PlaybackCog(
  client,
  tree,
  message_service,
  voice_service,
  error_notifier,
)
connection_cog = ConnectionCog(
  client,
  tree,
  connection_service,
  status_service,
)
general_cog = GeneralCog(tree, VERSION, LAST_UPDATED, error_notifier)
lifecycle_cog = LifecycleCog(
  client,
  tree,
  server_config,
  dict_manager,
  sound_boards,
  backup_databases=[SERVER_CONFIG_DB, DICT_DB, USER_CONFIG_DB],
  status_service=status_service,
  error_notifier=error_notifier,
)

setting = Setting(client, tree, server_config, error_notifier)
word_dict = WordDict(
  client,
  tree,
  dict_manager,
  server_config,
  error_notifier,
)
sound_dict_view = SoundDictView(
  client,
  tree,
  sound_dict,
  dict_manager,
  server_config,
  sound_boards,
  error_notifier,
)

try:
  client.run(DISCORD_BOT_TOKEN)
except VoicevoxStartupError as error:
  print(error)
