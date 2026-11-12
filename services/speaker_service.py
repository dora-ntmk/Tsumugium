"""VOICEVOX話者一覧を保持し、名前とIDの相互変換を担当する。"""

from __future__ import annotations

import json
import os

from services.error_notification_service import ensure_error_notifier


class SpeakerService:
  """起動時にVOICEVOX APIから話者一覧を取得し、失敗時は静的JSONを使う。"""

  def __init__(self, fallback_json_path: str, error_notifier=None):
    self.fallback_json_path = fallback_json_path
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.source = "json"
    self._speakers: list[tuple[int, str]] = self._load_fallback()

  def _load_fallback(self) -> list[tuple[int, str]]:
    if not os.path.exists(self.fallback_json_path):
      return []
    with open(self.fallback_json_path, encoding="utf-8") as file:
      return [(int(item["id"]), str(item["name"])) for item in json.load(file)]

  @staticmethod
  def flatten_speakers(payload) -> list[tuple[int, str]]:
    """VOICEVOX /speakers のレスポンスを (style_id, "キャラ（スタイル）") へ平坦化する。"""
    speakers: list[tuple[int, str]] = []
    seen_ids: set[int] = set()
    for character in payload:
      character_name = character["name"]
      for style in character.get("styles", []):
        if style.get("type", "talk") != "talk":
          continue
        style_id = int(style["id"])
        if style_id in seen_ids:
          continue
        seen_ids.add(style_id)
        speakers.append((style_id, f"{character_name}（{style['name']}）"))
    return speakers

  async def refresh(self, voicevox_client) -> bool:
    """VOICEVOXから話者一覧を取得し、成功した場合はTrueを返す。"""
    try:
      speakers = self.flatten_speakers(await voicevox_client.get_speakers())
    except Exception as error:
      self.error_notifier.report_exception(
        error,
        "VOICEVOX speakers fetch",
        {"fallback": self.fallback_json_path},
      )
      return False
    if not speakers:
      self.error_notifier.report(
        "VOICEVOXの話者一覧が空だったため、静的JSONの一覧を使用します"
      )
      return False
    self._speakers = speakers
    self.source = "api"
    return True

  @property
  def speakers(self) -> list[tuple[int, str]]:
    return list(self._speakers)

  def name_of(self, speaker_id: int) -> str | None:
    return next((name for sid, name in self._speakers if sid == speaker_id), None)

  def id_of(self, name: str) -> int | None:
    return next((sid for sid, sname in self._speakers if sname == name), None)

  def search(self, current: str) -> list[str]:
    return [name for _, name in self._speakers if current in name]
