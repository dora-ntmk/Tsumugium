"""Tsumugiumの依存関係を組み立て、Discord Botを起動する。"""

import asyncio

import discord

from clients.discord_soundboard_client import DiscordSoundboardClient
from clients.managed_discord_client import ManagedDiscordClient
from clients.voicevox_client import VoicevoxClient
from cogs.connection_cog import ConnectionCog
from cogs.general_cog import GeneralCog
from cogs.lifecycle_cog import LifecycleCog
from cogs.playback_cog import PlaybackCog
from cogs.user_cog import UserCog
from config import (
  DICT_DB,
  DISCORD_BOT_TOKEN,
  LAST_UPDATED,
  OPERATOR_USER_ID,
  SERVER_CONFIG_DB,
  SOUND_BOARDS_DB,
  SPEAKERS_JSON,
  STATUS_MESSAGE,
  TMP_DIR,
  UPDATE_STATE_DIR,
  USER_CONFIG_DB,
  VERSION,
  VOICEVOX_URL,
)
from repositories.guild_config_repository import GuildConfigRepository
from repositories.user_config_repository import UserConfigRepository
from services.connection_service import ConnectionService
from services.error_notification_service import ErrorNotificationService
from services.message_service import MessageService
from services.ready_state_service import ReadyStateService
from services.speaker_service import SpeakerService
from services.speech_service import SpeechService
from services.status_service import StatusService
from services.reconnect_service import ReconnectService
from services.update_watch_service import UpdateButton, UpdateWatchService
from services.user_reading_service import UserReadingService
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
speaker_service = SpeakerService(SPEAKERS_JSON, error_notifier)


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
  if await speaker_service.refresh(voicevox_client):
    print(f"VOICEVOX話者一覧を取得しました: {len(speaker_service.speakers)}件")
  else:
    print(
      "VOICEVOX話者一覧の取得に失敗したため、"
      f"{SPEAKERS_JSON} の一覧を使用します"
    )


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
user_reading_service = UserReadingService(user_config)
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
  user_reading_service=user_reading_service,
)
connection_service = ConnectionService(
  server_config,
  dict_manager,
  speech_service,
  voice_service,
  error_notifier,
  user_reading_service=user_reading_service,
)
status_service = StatusService(client, STATUS_MESSAGE, error_notifier)
client.register_closeable(status_service)
ready_state = ReadyStateService(UPDATE_STATE_DIR, VERSION)
client.register_closeable(ready_state)
update_watch = UpdateWatchService(
  client,
  UPDATE_STATE_DIR,
  status_service,
  error_notifier,
  operator_user_id=OPERATOR_USER_ID,
  current_version=VERSION,
)
client.register_closeable(update_watch)
# 運営者へのDMの「更新する」ボタンを、Botを再起動したあとも押せるようにする
client.update_watch = update_watch
client.add_dynamic_items(UpdateButton)
reconnect_service = ReconnectService(
  client,
  UPDATE_STATE_DIR,
  connection_service,
  voice_service,
  error_notifier,
)
client.register_closeable(reconnect_service)
# Discordとの接続を閉じる前に、読み上げチャンネルへ知らせてVCから抜ける
client.register_shutdown_hook(reconnect_service.shutdown)
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
  reconnect_service,
)
general_cog = GeneralCog(tree, VERSION, LAST_UPDATED, error_notifier)
user_cog = UserCog(tree, user_config, error_notifier)
lifecycle_cog = LifecycleCog(
  client,
  tree,
  server_config,
  dict_manager,
  sound_boards,
  backup_databases=[SERVER_CONFIG_DB, DICT_DB, USER_CONFIG_DB],
  status_service=status_service,
  error_notifier=error_notifier,
  ready_state=ready_state,
  update_watch=update_watch,
  reconnect_service=reconnect_service,
)

setting = Setting(
  client,
  tree,
  server_config,
  error_notifier,
  speaker_service,
  voice_service=voice_service,
)
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

discord.utils.setup_logging()
try:
  asyncio.run(client.run_until_stopped(DISCORD_BOT_TOKEN))
except VoicevoxStartupError as error:
  print(error)
except KeyboardInterrupt:
  pass
