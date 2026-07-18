"""テキスト前処理に渡す辞書データのスナップショット。"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SoundEntry:
  word: str
  sound_id: str
  full_match: bool = True
  trigger_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class DictionarySnapshot:
  sounds: tuple[SoundEntry, ...] = ()
  priority_readings: dict[str, str] = field(default_factory=dict)
  normal_readings: dict[str, str] = field(default_factory=dict)
  common_readings: dict[str, str] = field(default_factory=dict)

