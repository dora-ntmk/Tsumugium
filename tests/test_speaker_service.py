"""VOICEVOXの話者一覧（services/speaker_service.py）のテスト。"""

import asyncio
import json

from services.speaker_service import SpeakerService
from tests.support import RecordingNotifier, TempDirTestCase


def character(name, *styles):
  return {
    "name": name,
    "speaker_uuid": "00000000-0000-0000-0000-000000000000",
    "styles": [
      {"name": style_name, "id": style_id, **({"type": style_type} if style_type else {})}
      for style_name, style_id, style_type in styles
    ],
    "version": "0.0.0",
  }


class FakeVoicevox:
  def __init__(self, payload=None, error=None):
    self.payload = payload
    self.error = error

  async def get_speakers(self):
    if self.error is not None:
      raise self.error
    return self.payload


class FlattenSpeakersTest(TempDirTestCase):
  def test_streaming_talk_is_used(self):
    # 今のVOICEVOXは読み上げ用のスタイルを "streaming_talk" で返す。
    payload = [
      character("話者A", ("ノーマル", 2, "streaming_talk"), ("あまあま", 0, "streaming_talk")),
    ]
    self.assertEqual(
      SpeakerService.flatten_speakers(payload),
      [(2, "話者A（ノーマル）"), (0, "話者A（あまあま）")],
    )

  def test_talk_and_missing_type_are_used(self):
    payload = [character("話者B", ("ノーマル", 3, "talk"), ("ささやき", 4, None))]
    self.assertEqual(
      SpeakerService.flatten_speakers(payload),
      [(3, "話者B（ノーマル）"), (4, "話者B（ささやき）")],
    )

  def test_singing_styles_are_skipped(self):
    payload = [
      character(
        "話者C",
        ("ノーマル", 5, "streaming_talk"),
        ("歌", 6000, "sing"),
        ("先生", 6001, "singing_teacher"),
        ("フレーム", 6002, "frame_decode"),
      ),
    ]
    self.assertEqual(SpeakerService.flatten_speakers(payload), [(5, "話者C（ノーマル）")])

  def test_duplicate_ids_are_skipped(self):
    payload = [
      character("話者D", ("ノーマル", 7, "talk")),
      character("話者E", ("ノーマル", 7, "talk")),
    ]
    self.assertEqual(SpeakerService.flatten_speakers(payload), [(7, "話者D（ノーマル）")])


class RefreshTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.fallback = self.tmp_path("speakers.json")
    with open(self.fallback, "w", encoding="utf-8") as file:
      json.dump([{"id": 1, "name": "控えの話者（ノーマル）"}], file, ensure_ascii=False)
    self.notifier = RecordingNotifier()
    self.service = SpeakerService(self.fallback, self.notifier)

  def test_streaming_talk_payload_is_loaded_from_api(self):
    payload = [character("話者A", ("ノーマル", 2, "streaming_talk"))]
    self.assertTrue(asyncio.run(self.service.refresh(FakeVoicevox(payload))))
    self.assertEqual(self.service.source, "api")
    self.assertEqual(self.service.speakers, [(2, "話者A（ノーマル）")])

  def test_empty_list_keeps_fallback(self):
    payload = [character("話者C", ("歌", 6000, "sing"))]
    self.assertFalse(asyncio.run(self.service.refresh(FakeVoicevox(payload))))
    self.assertEqual(self.service.source, "json")
    self.assertEqual(self.service.speakers, [(1, "控えの話者（ノーマル）")])
    self.assertEqual(len(self.notifier.reports), 1)

  def test_error_keeps_fallback(self):
    voicevox = FakeVoicevox(error=RuntimeError("dummy failure"))
    self.assertFalse(asyncio.run(self.service.refresh(voicevox)))
    self.assertEqual(self.service.speakers, [(1, "控えの話者（ノーマル）")])
    self.assertEqual([context for _, context in self.notifier.exceptions], ["VOICEVOX speakers fetch"])
