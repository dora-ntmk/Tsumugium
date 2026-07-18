"""Tsumugiumの依存関係を組み立て、Discord Botを起動する。"""

import discord

from clients.discord_soundboard_client import DiscordSoundboardClient
from clients.managed_discord_client import ManagedDiscordClient
from cogs.connection_cog import ConnectionCog
from cogs.general_cog import GeneralCog
from cogs.lifecycle_cog import LifecycleCog
from cogs.playback_cog import PlaybackCog
from config import (
  DICT_DB,
  DISCORD_BOT_TOKEN,
  LAST_UPDATED,
  SERVER_CONFIG_DB,
  SOUND_BOARDS_DB,
  STATUS_MESSAGE,
  VERSION,
  VOICEVOX_URL,
)
from server_config import ServerConfig
from services.connection_service import ConnectionService
from services.message_service import MessageService
from services.speech_service import SpeechService
from services.voice_service import VoiceService
from setting import Setting
from sound_dict import SoundDict, SoundDictView, UpdateSoundBoards
from vvtts import VvTTS
from word_dict import DictManager, WordDict


intents = discord.Intents.default()
intents.message_content = True
client = ManagedDiscordClient(intents=intents, enable_debug_events=True)
tree = discord.app_commands.CommandTree(client)

vvtts = VvTTS(VOICEVOX_URL)
discord_soundboard_client = DiscordSoundboardClient(DISCORD_BOT_TOKEN)
client.register_closeable(vvtts)
client.register_closeable(discord_soundboard_client)

server_config = ServerConfig(SERVER_CONFIG_DB)
dict_manager = DictManager(DICT_DB)
sound_dict = SoundDict(dict_manager)
sound_boards = UpdateSoundBoards(
  SOUND_BOARDS_DB,
  dict_manager,
  discord_soundboard_client,
)

voice_service = VoiceService(client, discord_soundboard_client)
speech_service = SpeechService(
  vvtts,
  server_config,
  dict_manager,
  voice_service,
)
connection_service = ConnectionService(
  server_config,
  dict_manager,
  speech_service,
  voice_service,
)
message_service = MessageService(
  client,
  server_config,
  speech_service,
  voice_service,
  connection_service,
)

playback_cog = PlaybackCog(
  client,
  tree,
  message_service,
  voice_service,
)
connection_cog = ConnectionCog(client, tree, connection_service)
general_cog = GeneralCog(tree, VERSION, LAST_UPDATED)
lifecycle_cog = LifecycleCog(
  client,
  tree,
  server_config,
  dict_manager,
  sound_boards,
  backup_databases=[SERVER_CONFIG_DB, DICT_DB],
  status_message=STATUS_MESSAGE,
)

setting = Setting(client, tree, server_config)
word_dict = WordDict(client, tree, dict_manager, server_config)
sound_dict_view = SoundDictView(
  client,
  tree,
  sound_dict,
  dict_manager,
  server_config,
  sound_boards,
)

client.run(DISCORD_BOT_TOKEN)
