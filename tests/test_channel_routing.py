import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock


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

from play import Play  # noqa: E402


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
    def make_play(self, *, persistent_target=None, voice_channel=30):
        client = _Client()
        play = Play(
            client,
            _Tree(),
            vvtts=object(),
            server_config=_ServerConfig(persistent_target),
        )
        play.add_to_queue = AsyncMock()
        guild = types.SimpleNamespace(
            id=1,
            voice_client=_VoiceClient(voice_channel),
        )
        return play, client.events["on_message"], guild

    async def dispatch(self, handler, message):
        await handler(message)
        await asyncio.sleep(0)

    async def test_temporary_target_takes_precedence_over_persistent_target(self):
        play, handler, guild = self.make_play(persistent_target=20)
        play.temp_text_targets[guild.id] = 10

        await self.dispatch(handler, _Message(guild, 20))
        play.add_to_queue.assert_not_awaited()

        accepted = _Message(guild, 10)
        await self.dispatch(handler, accepted)
        play.add_to_queue.assert_awaited_once_with(accepted)

    async def test_configured_target_and_voice_channel_text_are_both_accepted(self):
        play, handler, guild = self.make_play(persistent_target=20, voice_channel=30)

        configured = _Message(guild, 20)
        await self.dispatch(handler, configured)
        play.add_to_queue.assert_awaited_once_with(configured)

        play.add_to_queue.reset_mock()
        voice_text = _Message(guild, 30)
        await self.dispatch(handler, voice_text)
        play.add_to_queue.assert_awaited_once_with(voice_text)

    async def test_without_target_only_voice_channel_text_is_accepted(self):
        play, handler, guild = self.make_play(persistent_target=None, voice_channel=30)

        await self.dispatch(handler, _Message(guild, 20))
        play.add_to_queue.assert_not_awaited()

        accepted = _Message(guild, 30)
        await self.dispatch(handler, accepted)
        play.add_to_queue.assert_awaited_once_with(accepted)

    async def test_bot_message_uses_same_channel_policy_and_sounddict_only_mode(self):
        play, handler, guild = self.make_play(persistent_target=20, voice_channel=30)

        await self.dispatch(handler, _Message(guild, 99, bot=True))
        play.add_to_queue.assert_not_awaited()

        accepted = _Message(guild, 20, bot=True)
        await self.dispatch(handler, accepted)
        play.add_to_queue.assert_awaited_once_with(
            accepted,
            sounddict_only=True,
        )

    async def test_disconnected_guild_does_not_queue_messages(self):
        play, handler, guild = self.make_play(persistent_target=20)
        guild.voice_client = None

        await self.dispatch(handler, _Message(guild, 20))

        play.add_to_queue.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
