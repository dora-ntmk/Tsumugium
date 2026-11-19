"""VOICEVOXの音声生成1回分の記録と、その集計結果の型。"""

from dataclasses import dataclass, field


SUCCESS = "success"
FAILED = "failed"
SKIPPED = "skipped"

# スキップの理由
SKIP_EMPTY = "empty"      # 読む文字がない（embedだけのメッセージなど）
SKIP_CLEARED = "cleared"  # 生成中にクリアされ、再生しなかった
SKIP_DELETED = "deleted"  # 生成中にメッセージが消され、再生しなかった

# 音声の作り方（成功・失敗の記録だけが持つ）
MODE_STREAM = "stream"        # /streaming_synthesisで少しずつ受け取った
MODE_SYNTHESIS = "synthesis"  # /synthesisで全体を作った（設定・振り分けでストリーミングしない）
MODE_FALLBACK = "fallback"    # ストリーミングを試したかったが、非対応で/synthesisへ戻した
MODES = (MODE_STREAM, MODE_SYNTHESIS, MODE_FALLBACK)


@dataclass(slots=True)
class GenerationRecord:
  """生成1回分の記録。

  読み上げた文章とサーバーは持たない（運営者が内容を推し量れないようにするため）。
  `first_seconds`だけは、記録したあとに再生が始まったときに書き込まれる。
  """

  at: float                 # 記録した時刻（UNIX時間）
  outcome: str              # SUCCESS / FAILED / SKIPPED
  chars: int = 0
  speaker: int | None = None
  wait_seconds: float = 0.0   # 生成の順番（全サーバー共通で1件ずつ）を待った時間
  synth_seconds: float = 0.0  # 順番が来てから生成し終える（または諦める）までの時間
  attempts: int = 0
  reason: str | None = None   # スキップの理由、または失敗した例外の名前
  mode: str | None = None     # 音声の作り方（MODE_*）。スキップはNone
  # メッセージを受け取ってから最初の音を再生するまでの時間（順番待ちを除く）。
  # 再生しなかったもの・メッセージから作っていないもの（入退室通知など）はNone
  first_seconds: float | None = None

  @property
  def seconds_per_char(self) -> float | None:
    if self.outcome != SUCCESS or self.chars <= 0:
      return None
    return self.synth_seconds / self.chars


# 文字数で分けて比べるときの区切り（その文字数まで）。最後のNoneは「それより多い」
CHAR_BUCKET_LIMITS = (30, 100, None)


@dataclass(frozen=True, slots=True)
class ModeSummary:
  """作り方ごとの集計（成功したものだけ）。ストリーミングあり・なしを比べるために使う。"""

  success: int = 0
  synth_average: float | None = None
  synth_median: float | None = None
  synth_p95: float | None = None
  synth_max: float | None = None
  seconds_per_char_average: float | None = None
  first_average: float | None = None
  first_median: float | None = None
  first_max: float | None = None
  # CHAR_BUCKET_LIMITS の区切りごとの、firstの中央値（ない区切りはNone）
  first_median_by_chars: tuple[float | None, ...] = ()


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
  # 作り方ごとの、生成した（成功・失敗の）件数
  mode_counts: dict[str, int] = field(default_factory=dict)
  # 作り方ごとの集計（成功したものがある作り方だけ）
  by_mode: dict[str, ModeSummary] = field(default_factory=dict)
  first_average: float | None = None
  first_median: float | None = None
  first_max: float | None = None
  first_at: float | None = None
