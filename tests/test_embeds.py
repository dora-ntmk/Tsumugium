"""Embedの共通ひな形（presentation/embeds.py）のテスト。"""

import unittest

import discord

from presentation.embeds import EmbedType, make_embed


class MakeEmbedTest(unittest.TestCase):
  def test_title_and_description(self):
    embed = make_embed("たいとる", "ほんぶん")
    self.assertEqual(embed.title, "たいとる")
    self.assertEqual(embed.description, "ほんぶん")

  def test_empty_description_becomes_none(self):
    self.assertIsNone(make_embed("たいとる").description)
    self.assertIsNone(make_embed("たいとる", "").description)

  def test_default_type_is_info(self):
    self.assertEqual(make_embed("たいとる").color, discord.Color.blue())

  def test_color_per_type(self):
    expected = {
      EmbedType.INFO: discord.Color.blue(),
      EmbedType.SUCCESS: discord.Color.green(),
      EmbedType.WARNING: discord.Color.yellow(),
      EmbedType.ERROR: discord.Color.red(),
    }
    for embed_type, color in expected.items():
      with self.subTest(embed_type=embed_type):
        self.assertEqual(make_embed("たいとる", embed_type=embed_type).color, color)

  def test_each_call_returns_new_embed(self):
    first = make_embed("たいとる")
    first.add_field(name="なまえ", value="あたい")
    self.assertEqual(len(make_embed("たいとる").fields), 0)


if __name__ == "__main__":
  unittest.main()
