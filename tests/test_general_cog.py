"""/version（cogs/general_cog.py）のテスト。"""

import asyncio
import unittest
from urllib.parse import urlparse

import discord
from discord import app_commands

from cogs.general_cog import CHANGELOG_URL, GeneralCog
from tests.support import RecordingNotifier


class FakeResponse:
  async def defer(self, **kwargs):
    pass


class FakeInteraction:
  def __init__(self):
    self.response = FakeResponse()
    self.embeds = []

  async def edit_original_response(self, *, embed):
    self.embeds.append(embed)


class VersionCommandTest(unittest.TestCase):
  def run_version(self):
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    GeneralCog(tree, "v9.9.9.99 (beta 1)", "2099/01/01", RecordingNotifier())
    interaction = FakeInteraction()
    asyncio.run(tree.get_command("version").callback(interaction))
    self.assertEqual(len(interaction.embeds), 1)
    return {field.name: field.value for field in interaction.embeds[0].fields}

  def test_version_and_date(self):
    fields = self.run_version()
    self.assertEqual(fields["Tsumugiumバージョン"], "v9.9.9.99 (beta 1)")
    self.assertEqual(fields["Tsumugium最終更新日"], "2099/01/01")

  def test_changelog_link_points_to_page_without_fragment(self):
    # バージョンごとの見出し（#以降）を付けると、新しい版を出しても古い見出しを指したままになる。
    self.assertEqual(urlparse(CHANGELOG_URL).fragment, "")
    self.assertNotIn("#", CHANGELOG_URL)
    self.assertEqual(self.run_version()["更新内容"], f"[こちらをご覧ください]({CHANGELOG_URL})")


if __name__ == "__main__":
  unittest.main()
