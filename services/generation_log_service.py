"""VOICEVOXの音声生成の記録を、直近の一定件数だけメモリに持つ。

成功・失敗はVoicevoxClientが、スキップ（読む文字がない、クリア・削除で
捨てた）はSpeechServiceが記録する。Botを再起動すると記録は消える。
読み上げた文章そのものは持たず、文字数だけを残す。
"""

import math
import time
from collections import Counter, deque

from models.generation_record import (
  FAILED,
  SKIPPED,
  SUCCESS,
  GenerationRecord,
  GenerationSummary,
)


class GenerationLogService:
  DEFAULT_CAPACITY = 500

  def __init__(self, capacity: int = DEFAULT_CAPACITY, *, clock=time.time):
    self._records: deque[GenerationRecord] = deque(maxlen=capacity)
    self._clock = clock

  @property
  def capacity(self) -> int:
    return self._records.maxlen

  def _add(self, **values) -> None:
    self._records.append(GenerationRecord(at=self._clock(), **values))

  def record_success(
      self, guild_id, chars, speaker, wait_seconds, synth_seconds, attempts
  ) -> None:
    self._add(
      guild_id=guild_id,
      outcome=SUCCESS,
      chars=chars,
      speaker=speaker,
      wait_seconds=wait_seconds,
      synth_seconds=synth_seconds,
      attempts=attempts,
    )

  def record_failure(
      self, guild_id, chars, speaker, wait_seconds, synth_seconds, attempts, reason
  ) -> None:
    self._add(
      guild_id=guild_id,
      outcome=FAILED,
      chars=chars,
      speaker=speaker,
      wait_seconds=wait_seconds,
      synth_seconds=synth_seconds,
      attempts=attempts,
      reason=reason,
    )

  def record_skip(self, guild_id, reason, chars: int = 0) -> None:
    self._add(guild_id=guild_id, outcome=SKIPPED, chars=chars, reason=reason)

  def recent(self, count: int) -> list[GenerationRecord]:
    """新しいものから`count`件を返す。"""
    if count <= 0:
      return []
    records = list(self._records)
    return records[-count:][::-1]

  def summarize(self) -> GenerationSummary:
    records = list(self._records)
    if not records:
      return GenerationSummary()
    outcomes = Counter(record.outcome for record in records)
    skip_reasons = Counter(
      record.reason for record in records if record.outcome == SKIPPED
    )
    successes = [record for record in records if record.outcome == SUCCESS]
    synth = sorted(record.synth_seconds for record in successes)
    per_char = [
      record.seconds_per_char
      for record in successes
      if record.seconds_per_char is not None
    ]
    waits = [record.wait_seconds for record in records if record.outcome != SKIPPED]
    return GenerationSummary(
      total=len(records),
      success=outcomes[SUCCESS],
      failed=outcomes[FAILED],
      skipped=outcomes[SKIPPED],
      skip_reasons=dict(skip_reasons),
      synth_average=_average(synth),
      synth_median=_percentile(synth, 50),
      synth_p95=_percentile(synth, 95),
      synth_max=synth[-1] if synth else None,
      seconds_per_char_average=_average(per_char),
      wait_average=_average(waits),
      wait_max=max(waits) if waits else None,
      first_at=records[0].at,
    )


def _average(values) -> float | None:
  if not values:
    return None
  return sum(values) / len(values)


def _percentile(sorted_values, percent) -> float | None:
  """最近順位法でパーセンタイルを返す（並べ替え済みの値を受け取る）。"""
  if not sorted_values:
    return None
  rank = math.ceil(percent / 100 * len(sorted_values))
  return sorted_values[max(rank, 1) - 1]
