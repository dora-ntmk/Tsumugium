"""VOICEVOXの音声生成1回分の記録と、その集計結果の型。"""

from dataclasses import dataclass, field


SUCCESS = "success"
FAILED = "failed"
SKIPPED = "skipped"

# スキップの理由
SKIP_EMPTY = "empty"      # 読む文字がない（embedだけのメッセージなど）
SKIP_CLEARED = "cleared"  # 生成中にクリアされ、再生しなかった
SKIP_DELETED = "deleted"  # 生成中にメッセージが消され、再生しなかった


@dataclass(frozen=True, slots=True)
class GenerationRecord:
  """生成1回分の記録。読み上げた文章そのものは持たない（文字数だけ）。"""

  at: float                 # 記録した時刻（UNIX時間）
  guild_id: int
  outcome: str              # SUCCESS / FAILED / SKIPPED
  chars: int = 0
  speaker: int | None = None
  wait_seconds: float = 0.0   # 生成の順番（全サーバー共通で1件ずつ）を待った時間
  synth_seconds: float = 0.0  # 順番が来てから生成し終える（または諦める）までの時間
  attempts: int = 0
  reason: str | None = None   # スキップの理由、または失敗した例外の名前

  @property
  def seconds_per_char(self) -> float | None:
    if self.outcome != SUCCESS or self.chars <= 0:
      return None
    return self.synth_seconds / self.chars


@dataclass(frozen=True, slots=True)
class GenerationSummary:
  """記録の集計。時間の統計は成功したものだけで出す。"""

  total: int = 0
  success: int = 0
  failed: int = 0
  skipped: int = 0
  skip_reasons: dict[str, int] = field(default_factory=dict)
  synth_average: float | None = None
  synth_median: float | None = None
  synth_p95: float | None = None
  synth_max: float | None = None
  seconds_per_char_average: float | None = None
  wait_average: float | None = None
  wait_max: float | None = None
  first_at: float | None = None
