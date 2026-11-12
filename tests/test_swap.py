"""テキスト前処理（swap.preprocess_text）のテスト。

AGENTS.md「テキスト前処理パイプライン」の順番と動きを確かめる。
"""

import unittest
from types import SimpleNamespace

from models.dictionary_snapshot import DictionarySnapshot, SoundEntry
from swap import preprocess_text
from tests.support import (
  DUMMY_OTHER_USER_ID,
  DUMMY_ROLE_ID,
  DUMMY_SOUND_ID,
  DUMMY_TEXT_CHANNEL_ID,
  DUMMY_USER_ID,
)


class FakeGuild:
  """get_member / get_channel / get_role だけを持つ偽のサーバー。"""

  def __init__(self, members=None, channels=None, roles=None):
    self._members = members or {}
    self._channels = channels or {}
    self._roles = roles or {}

  def get_member(self, member_id):
    return self._members.get(member_id)

  def get_channel(self, channel_id):
    return self._channels.get(channel_id)

  def get_role(self, role_id):
    return self._roles.get(role_id)


def run(
    text,
    dictionary=None,
    emoji_ja=None,
    guild=None,
    attachments=(),
    mentions=None,
    author_id=None,
    user_readings=None,
):
  return preprocess_text(
    text,
    dictionary or DictionarySnapshot(),
    emoji_ja or {},
    guild,
    list(attachments),
    mentions,
    author_id=author_id,
    user_readings=user_readings,
  )


class SpacedTextTest(unittest.TestCase):
  def test_whole_message_spaced_out(self):
    result = run("あ い う")
    self.assertEqual(result.text, "あいう")
    self.assertTrue(result.spaced_out)

  def test_full_width_spaces_are_also_spaced_out(self):
    result = run("あ　い　う")
    self.assertEqual(result.text, "あいう")
    self.assertTrue(result.spaced_out)

  def test_two_units_are_not_spaced_out(self):
    result = run("あ い")
    self.assertFalse(result.spaced_out)

  def test_same_character_repeat_is_joined_without_flag(self):
    result = run("あ あ あ")
    self.assertEqual(result.text, "あああ")
    self.assertFalse(result.spaced_out)

  def test_spaced_part_inside_normal_sentence_is_joined_without_flag(self):
    result = run("きょうは あ い う です")
    self.assertIn("あいう", result.text)
    self.assertFalse(result.spaced_out)


class SoundboardTest(unittest.TestCase):
  def test_full_match_returns_sound_id(self):
    dictionary = DictionarySnapshot(sounds=(SoundEntry("やあ", DUMMY_SOUND_ID),))
    self.assertEqual(run("やあ", dictionary).sound_id, DUMMY_SOUND_ID)

  def test_full_match_does_not_match_part_of_message(self):
    dictionary = DictionarySnapshot(sounds=(SoundEntry("やあ", DUMMY_SOUND_ID),))
    self.assertIsNone(run("やあみんな", dictionary).sound_id)

  def test_partial_match_returns_sound_id(self):
    dictionary = DictionarySnapshot(
      sounds=(SoundEntry("おはよう", DUMMY_SOUND_ID, full_match=False),)
    )
    self.assertEqual(run("みんなおはようございます", dictionary).sound_id, DUMMY_SOUND_ID)

  def test_full_match_is_evaluated_before_partial_match(self):
    dictionary = DictionarySnapshot(sounds=(
      SoundEntry("や", "partial-sound", full_match=False),
      SoundEntry("やあ", "full-sound"),
    ))
    self.assertEqual(run("やあ", dictionary).sound_id, "full-sound")

  def test_full_match_uses_normalized_spaced_text(self):
    dictionary = DictionarySnapshot(sounds=(SoundEntry("あいう", DUMMY_SOUND_ID),))
    self.assertEqual(run("あ い う", dictionary).sound_id, DUMMY_SOUND_ID)

  def test_trigger_user_matches_only_that_author(self):
    dictionary = DictionarySnapshot(sounds=(
      SoundEntry("やあ", DUMMY_SOUND_ID, trigger_user_id=str(DUMMY_USER_ID)),
    ))
    self.assertEqual(
      run("やあ", dictionary, author_id=DUMMY_USER_ID).sound_id, DUMMY_SOUND_ID
    )
    self.assertIsNone(run("やあ", dictionary, author_id=DUMMY_OTHER_USER_ID).sound_id)

  def test_trigger_user_applies_to_partial_match(self):
    dictionary = DictionarySnapshot(sounds=(
      SoundEntry(
        "おはよう",
        DUMMY_SOUND_ID,
        full_match=False,
        trigger_user_id=str(DUMMY_USER_ID),
      ),
    ))
    self.assertIsNone(
      run("おはようございます", dictionary, author_id=DUMMY_OTHER_USER_ID).sound_id
    )


class OrderTest(unittest.TestCase):
  def test_priority_reading_is_applied_before_url_conversion(self):
    dictionary = DictionarySnapshot(
      priority_readings={"https://youtu.be/abc": "おきにいりのどうが"}
    )
    self.assertEqual(run("https://youtu.be/abc", dictionary).text, "おきにいりのどうが")

  def test_normal_reading_does_not_override_url_conversion(self):
    dictionary = DictionarySnapshot(normal_readings={"youtu": "ゆーつー"})
    result = run("https://youtu.be/abc", dictionary)
    self.assertEqual(result.text, "ユーチューブへのリンク,")

  def test_custom_emoji_is_removed_before_www_conversion(self):
    result = run("<:www:111111111111111111>")
    self.assertNotIn("わら", result.text)

  def test_animated_custom_emoji_is_removed(self):
    result = run("<a:dance:111111111111111111>")
    self.assertNotIn("dance", result.text)

  def test_www_in_url_is_not_converted(self):
    result = run("https://www.youtube.com/watch")
    self.assertEqual(result.text, "ユーチューブへのリンク,")

  def test_normal_reading_is_applied_before_common_reading(self):
    dictionary = DictionarySnapshot(
      normal_readings={"ねこ": "サーバーのよみ"},
      common_readings={"ねこ": "きょうつうのよみ"},
    )
    self.assertEqual(run("ねこ", dictionary).text, "サーバーのよみ")

  def test_common_reading_is_used_when_no_normal_reading(self):
    dictionary = DictionarySnapshot(common_readings={"ねこ": "きょうつうのよみ"})
    self.assertEqual(run("ねこ", dictionary).text, "きょうつうのよみ")


class ConversionTest(unittest.TestCase):
  def test_www_becomes_wara_per_character(self):
    self.assertEqual(run("www").text, "わらわらわら,")

  def test_single_w_is_not_converted(self):
    self.assertEqual(run("w").text, "w")

  def test_other_url_is_omitted(self):
    self.assertEqual(run("https://example.com/page").text, "リンク省略,")

  def test_markdown_formats(self):
    cases = {
      "||ひみつ||": ",ネタバレ,",
      "~~けした~~": ",取り消し線,",
      "`code`": ",コードブロック,",
      "<t:1700000000:R>": ",タイムスタンプ,",
    }
    for text, expected in cases.items():
      with self.subTest(text=text):
        self.assertEqual(run(text).text, expected)

  def test_newline_becomes_separator(self):
    self.assertEqual(run("あ\nい").text, "あ,い")

  def test_emoji_short_name_is_applied(self):
    self.assertEqual(run("😀", emoji_ja={"😀": "にっこり"}).text, "にっこり")

  def test_replaced_ranges_cover_converted_text(self):
    result = run("www")
    self.assertEqual(result.replaced_ranges, [(0, len("わらわらわら,"))])


class MentionTest(unittest.TestCase):
  def test_user_reading_has_priority_over_display_name(self):
    mentions = [SimpleNamespace(id=DUMMY_USER_ID, display_name="ひょうじめい")]
    result = run(
      f"<@{DUMMY_USER_ID}>",
      mentions=mentions,
      user_readings={DUMMY_USER_ID: "よみかた"},
    )
    self.assertEqual(result.text, "よみかたへのメンション,")

  def test_display_name_is_used_without_user_reading(self):
    mentions = [SimpleNamespace(id=DUMMY_USER_ID, display_name="ひょうじめい")]
    result = run(f"<@!{DUMMY_USER_ID}>", mentions=mentions)
    self.assertEqual(result.text, "ひょうじめいへのメンション,")

  def test_guild_member_is_used_when_not_in_mentions(self):
    guild = FakeGuild(members={
      DUMMY_USER_ID: SimpleNamespace(display_name="めんばー"),
    })
    self.assertEqual(run(f"<@{DUMMY_USER_ID}>", guild=guild).text, "めんばーへのメンション,")

  def test_unknown_user(self):
    self.assertEqual(run(f"<@{DUMMY_USER_ID}>").text, "ユーザーへのメンション,")

  def test_channel_link(self):
    guild = FakeGuild(channels={
      DUMMY_TEXT_CHANNEL_ID: SimpleNamespace(name="ざつだん"),
    })
    result = run(f"<#{DUMMY_TEXT_CHANNEL_ID}>", guild=guild)
    self.assertEqual(result.text, "ざつだんへのリンク,")
    self.assertEqual(run(f"<#{DUMMY_TEXT_CHANNEL_ID}>").text, "チャンネルへのリンク,")

  def test_role_mention(self):
    guild = FakeGuild(roles={DUMMY_ROLE_ID: SimpleNamespace(name="さんかしゃ")})
    result = run(f"<@&{DUMMY_ROLE_ID}>", guild=guild)
    self.assertEqual(result.text, "さんかしゃへのメンション,")
    self.assertEqual(run(f"<@&{DUMMY_ROLE_ID}>").text, "ロールへのメンション,")


class AttachmentTest(unittest.TestCase):
  def test_attachment_kinds(self):
    cases = {
      "photo.PNG": ",画像ファイル",
      "clip.mp4": ",動画ファイル",
      "voice.ogg": ",音声ファイル",
      "document.pdf": ",添付ファイル",
    }
    for filename, expected in cases.items():
      with self.subTest(filename=filename):
        result = run("", attachments=[SimpleNamespace(filename=filename)])
        self.assertEqual(result.text, expected)


if __name__ == "__main__":
  unittest.main()
