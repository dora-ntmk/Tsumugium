"""再生キューに格納する音声アイテム。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TTSItem:
  path: str


@dataclass(frozen=True, slots=True)
class SoundboardItem:
  sound_id: str


AudioItem = TTSItem | SoundboardItem

