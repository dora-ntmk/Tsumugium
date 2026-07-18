import unittest

import swap
from models.dictionary_snapshot import DictionarySnapshot, SoundEntry


class _NamedObject:
    def __init__(self, object_id: int, name: str):
        self.id = object_id
        self.name = name
        self.display_name = name


class _Guild:
    def __init__(self):
        self.members = {10: _NamedObject(10, "Alice")}
        self.channels = {20: _NamedObject(20, "general")}
        self.roles = {30: _NamedObject(30, "moderator")}

    def get_member(self, object_id: int):
        return self.members.get(object_id)

    def get_channel(self, object_id: int):
        return self.channels.get(object_id)

    def get_role(self, object_id: int):
        return self.roles.get(object_id)


class TextPreprocessorTests(unittest.TestCase):
    def setUp(self):
        self.sounds = []
        self.priority_readings = {}
        self.normal_readings = {}
        self.common_readings = {}
        self.guild = _Guild()

    def add_entry(
        self,
        guild_id: str,
        word: str,
        *,
        reading: str | None = None,
        sound_id: str | None = None,
        priority: bool = False,
        full_match: bool = True,
        trigger_user_id: str | None = None,
        added_at: int = 0,
    ):
        del added_at
        if sound_id is not None and guild_id == "1":
            self.sounds.append(
                SoundEntry(
                    word=word,
                    sound_id=sound_id,
                    full_match=full_match,
                    trigger_user_id=trigger_user_id,
                )
            )
        if reading is None:
            return
        if guild_id == "__common__":
            self.common_readings[word] = reading
        elif priority:
            self.priority_readings[word] = reading
        else:
            self.normal_readings[word] = reading

    def snapshot(self):
        return DictionarySnapshot(
            sounds=tuple(self.sounds),
            priority_readings=dict(self.priority_readings),
            normal_readings=dict(self.normal_readings),
            common_readings=dict(self.common_readings),
        )

    def preprocess(self, text: str, *, author_id: int | None = 42):
        return swap.preprocess_text(
            text,
            self.snapshot(),
            {},
            self.guild,
            [],
            [],
            author_id=author_id,
        )

    def test_full_match_soundboard_honors_trigger_user(self):
        self.add_entry(
            "1",
            "ping",
            sound_id="sound-full",
            full_match=True,
            trigger_user_id="42",
        )

        text, ranges, sound_id = self.preprocess("ping", author_id=42)
        self.assertEqual((text, ranges, sound_id), ("ping", [], "sound-full"))

        text, ranges, sound_id = self.preprocess("ping", author_id=99)
        self.assertEqual(text, "ping")
        self.assertEqual(ranges, [])
        self.assertIsNone(sound_id)

    def test_partial_match_soundboard_runs_after_full_match_check(self):
        self.add_entry("1", "alarm", sound_id="sound-partial", full_match=False)

        text, ranges, sound_id = self.preprocess("prefix alarm suffix")

        self.assertEqual(text, "prefix alarm suffix")
        self.assertEqual(ranges, [])
        self.assertEqual(sound_id, "sound-partial")

    def test_missing_author_id_does_not_apply_trigger_filter(self):
        self.add_entry(
            "1",
            "restricted",
            sound_id="restricted-sound",
            trigger_user_id="777",
        )

        _, _, sound_id = self.preprocess("restricted", author_id=None)

        self.assertEqual(sound_id, "restricted-sound")

    def test_priority_dictionary_replaces_url_before_url_filter(self):
        url = "https://example.com/path"
        self.add_entry("1", url, reading="CUSTOM_URL", priority=True)

        text, ranges, sound_id = self.preprocess(url)

        self.assertEqual(text, "CUSTOM_URL")
        self.assertEqual(ranges, [(0, len("CUSTOM_URL"))])
        self.assertIsNone(sound_id)

    def test_protected_priority_replacement_is_not_replaced_again(self):
        self.add_entry("1", "FIRST", reading="SECOND", priority=True, added_at=2)
        self.add_entry("1", "SECOND", reading="THIRD", added_at=1)

        text, _, _ = self.preprocess("FIRST")

        self.assertEqual(text, "SECOND")

    def test_normal_and_common_dictionaries_are_both_applied(self):
        self.add_entry("1", "local", reading="LOCAL_READING")
        self.add_entry("__common__", "common", reading="COMMON_READING")

        text, ranges, sound_id = self.preprocess("local common")

        self.assertIn("LOCAL_READING", text)
        self.assertIn("COMMON_READING", text)
        self.assertGreaterEqual(len(ranges), 3)
        self.assertIsNone(sound_id)

    def test_custom_emoji_is_removed_before_www_conversion(self):
        text, _, sound_id = self.preprocess("<:www:123> www")

        self.assertNotIn("<:www:123>", text)
        self.assertNotIn("www", text)
        self.assertIsNone(sound_id)

    def test_mentions_are_resolved_from_guild_objects(self):
        text, _, sound_id = self.preprocess("<@10> <#20> <@&30>")

        self.assertNotIn("<@10>", text)
        self.assertNotIn("<#20>", text)
        self.assertNotIn("<@&30>", text)
        self.assertIn("Alice", text)
        self.assertIn("general", text)
        self.assertIn("moderator", text)
        self.assertIsNone(sound_id)


if __name__ == "__main__":
    unittest.main()
