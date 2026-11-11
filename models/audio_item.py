"""再生キューに格納する音声アイテム。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TTSItem:
  path: str
  message_id: int | None = None


@dataclass(frozen=True, slots=True)
class SoundboardItem:
  sound_id: str


AudioItem = TTSItem | SoundboardItem

