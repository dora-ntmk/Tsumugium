"""テキスト前処理の結果。"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class PreprocessResult:
  text: str
  replaced_ranges: list[tuple[int, int]] = field(default_factory=list)
  sound_id: str | None = None
  spaced_out: bool = False
