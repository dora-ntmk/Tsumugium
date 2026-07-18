import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


def _install_dependency_stubs():
    """Allow the routing tests to run without the Discord runtime installed."""
    try:
        __import__("dotenv")
    except ModuleNotFoundError:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv

    try:
        __import__("discord")
    except ModuleNotFoundError:
        discord = types.ModuleType("discord")

        class _Color:
            green = staticmethod(lambda: 1)
            red = staticmethod(lambda: 2)
            blue = staticmethod(lambda: 3)
            yellow = staticmethod(lambda: 4)

        class _Embed:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def add_field(self, **kwargs):
                return None

            def set_footer(self, **kwargs):
                return None

        class _Locale:
            japanese = "ja"
            american_english = "en-US"
            british_english = "en-GB"
            chinese = "zh-CN"
            taiwan_chinese = "zh-TW"
            korean = "ko"

        class _Translator:
            pass

        discord.Color = _Color
        discord.Embed = _Embed
        discord.Locale = _Locale
        discord.app_commands = types.SimpleNamespace(
            Translator=_Translator,
            locale_str=str,
            TranslationContext=object,
        )
        sys.modules["discord"] = discord

    try:
        __import__("aiohttp")
    except ModuleNotFoundError:
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")


_install_dependency_stubs()

from models.audio_item import SoundboardItem, TTSItem  # noqa: E402
from play import Play  # noqa: E402
from services.message_service import MessageService  # noqa: E402
from services.voice_service import VoiceService  # noqa: E402


class _Tree:
    def __init__(self):
        self.commands = {}

    def command(self, **metadata):
        def decorator(function):
            self.commands[metadata["name"]] = function
            return function

        return decorator


class _Client:
    def __init__(self):
        self.events = {}
        self.user = types.SimpleNamespace(id=9999)

    def event(self, function):
        self.events[function.__name__] = function
        return function


class _ServerConfig:
    def __init__(self, text_target=None):
        self.text_target = text_target

    def get(self, guild_id, key):
        if key == "TextTarget":
            return self.text_target
        raise AssertionError(f"Unexpected config key: {key}")


class _VoiceClient:
    def __init__(self, channel_id: int):
        self.channel = types.SimpleNamespace(id=channel_id)

    def is_playing(self):
        return False


class _Message:
    def __init__(self, guild, channel_id: int, *, bot: bool = False):
        self.guild = guild
        self.channel = types.SimpleNamespace(id=channel_id)
        self.author = types.SimpleNamespace(bot=bot, voice=None)
        self.content = "ordinary message"
        self.flags = types.SimpleNamespace(silent=False)
        self.attachments = []
        self.mentions = []
        self.id = 123


class ChannelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def make_services(self, *, persistent_target=None, voice_channel=30):
        client = _Client()
        voice_service = VoiceService(client)
        speech_service = types.SimpleNamespace(add_message=AsyncMock())
        message_service = MessageService(
            client,
            _ServerConfig(persistent_target),
            speech_service,
            voice_service,
        )
        Play(client, _Tree(), message_service, voice_service)
        guild = types.SimpleNamespace(
            id=1,
            voice_client=_VoiceClient(voice_channel),
        )
        return voice_service, speech_service, client.events["on_message"], guild

    async def dispatch(self, handler, message):
        await handler(message)
        await asyncio.sleep(0)

    async def test_sessions_are_reused_per_guild_and_isolated_between_guilds(self):
        voice_service, _, _, guild = self.make_services()

        first = voice_service.get_session(guild.id)
        same = voice_service.get_session(guild.id)
        other = voice_service.get_session(2)

        self.assertIs(first, same)
        self.assertIsNot(first, other)

    async def test_enqueue_stores_typed_item_and_starts_player_task(self):
        voice_service, _, _, guild = self.make_services()
        voice_service.play_loop = AsyncMock()
        item = TTSItem("tmp/message.wav")

        await voice_service.enqueue(guild, item)
        await asyncio.sleep(0)

        session = voice_service.get_session(guild.id)
        self.assertIs(session.queue.get_nowait(), item)
        session.queue.task_done()
        voice_service.play_loop.assert_awaited_once_with(guild)

    async def test_clear_removes_only_tts_files_and_resets_clearing_state(self):
        voice_service, _, _, guild = self.make_services()
        session = voice_service.get_session(guild.id)
        await session.queue.put(TTSItem("tmp/message.wav"))
        await session.queue.put(SoundboardItem("sound-1"))
        voice_service.safe_remove = AsyncMock()

        cleared, pending_files = voice_service.begin_clear(guild, instant=False)

        self.assertEqual(cleared, 2)
        self.assertEqual(pending_files, ["tmp/message.wav"])
        self.assertEqual(session.queue.qsize(), 0)
        self.assertTrue(session.skipping)
        self.assertTrue(session.clearing)

        with patch("services.voice_service.asyncio.sleep", new=AsyncMock()):
            await voice_service.finish_clear(guild.id, pending_files)

        voice_service.safe_remove.assert_awaited_once_with("tmp/message.wav")
        self.assertFalse(session.clearing)

    async def test_temporary_target_takes_precedence_over_persistent_target(self):
        voice_service, speech_service, handler, guild = self.make_services(
            persistent_target=20
        )
        voice_service.get_session(guild.id).temporary_text_channel_id = 10

        await self.dispatch(handler, _Message(guild, 20))
        speech_service.add_message.assert_not_awaited()

        accepted = _Message(guild, 10)
        await self.dispatch(handler, accepted)
        speech_service.add_message.assert_awaited_once_with(accepted)

    async def test_configured_target_and_voice_channel_text_are_both_accepted(self):
        _, speech_service, handler, guild = self.make_services(
            persistent_target=20,
            voice_channel=30,
        )

        configured = _Message(guild, 20)
        await self.dispatch(handler, configured)
        speech_service.add_message.assert_awaited_once_with(configured)

        speech_service.add_message.reset_mock()
        voice_text = _Message(guild, 30)
        await self.dispatch(handler, voice_text)
        speech_service.add_message.assert_awaited_once_with(voice_text)

    async def test_without_target_only_voice_channel_text_is_accepted(self):
        _, speech_service, handler, guild = self.make_services(
            persistent_target=None,
            voice_channel=30,
        )

        await self.dispatch(handler, _Message(guild, 20))
        speech_service.add_message.assert_not_awaited()

        accepted = _Message(guild, 30)
        await self.dispatch(handler, accepted)
        speech_service.add_message.assert_awaited_once_with(accepted)

    async def test_bot_message_uses_same_channel_policy_and_sounddict_only_mode(self):
        _, speech_service, handler, guild = self.make_services(
            persistent_target=20,
            voice_channel=30,
        )

        await self.dispatch(handler, _Message(guild, 99, bot=True))
        speech_service.add_message.assert_not_awaited()

        accepted = _Message(guild, 20, bot=True)
        await self.dispatch(handler, accepted)
        speech_service.add_message.assert_awaited_once_with(
            accepted,
            sounddict_only=True,
        )

    async def test_disconnected_guild_does_not_queue_messages(self):
        _, speech_service, handler, guild = self.make_services(persistent_target=20)
        guild.voice_client = None

        await self.dispatch(handler, _Message(guild, 20))

        speech_service.add_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
