"""音声生成の記録（/synthesis log と自動送信）の表示。

集計（Components v2 の文）と、記録の一覧（txtファイル）を作る。
/synthesis log の応答と、運営者へ自動で送る記録の両方で同じ見た目にする。
"""

import io
from datetime import datetime, timedelta, timezone

import discord

from models.generation_record import (
  CHAR_BUCKET_LIMITS,
  MODE_FALLBACK,
  MODE_STREAM,
  MODE_SYNTHESIS,
  MODES,
  SKIP_CLEARED,
  SKIP_DELETED,
  SKIP_EMPTY,
  SKIPPED,
)
from presentation.embeds import EmbedType
from presentation.layouts import LayoutMessage, make_layout


JST = timezone(timedelta(hours=9), "JST")
LOG_TITLE = "音声生成の記録"
LOG_COLUMNS = (
  "datetime", "result", "chars", "synthesis", "first", "wait", "speaker", "note",
)

def _log_seconds(value) -> str:
  return "-" if value is None else f"{value:.2f}s"


_SKIP_LABELS = {
  SKIP_EMPTY: "読む文字なし",
  SKIP_CLEARED: "クリア",
  SKIP_DELETED: "削除",
}


def _summary_seconds(value) -> str:
  return "—" if value is None else f"{value:.2f}秒"


_MODE_LABELS = {
  MODE_STREAM: "ストリーミング",
  MODE_SYNTHESIS: "通常",
  MODE_FALLBACK: "非対応→通常",
}


def _char_bucket_labels() -> list[str]:
  labels = []
  lower = 0
  for limit in CHAR_BUCKET_LIMITS:
    if limit is None:
      labels.append(f"{lower + 1}字〜")
    elif lower == 0:
      labels.append(f"〜{limit}字")
    else:
      labels.append(f"{lower + 1}〜{limit}字")
    if limit is not None:
      lower = limit
  return labels


def _synthesis_line(stats) -> str:
  line = f"平均 {_summary_seconds(stats.synth_average)}"
  if stats.seconds_per_char_average is not None:
    line += f"（{stats.seconds_per_char_average * 1000:.0f}ms/字）"
  return line + (
    f" / 中央値 {_summary_seconds(stats.synth_median)}"
    f" / 95% {_summary_seconds(stats.synth_p95)}"
    f" / 最大 {_summary_seconds(stats.synth_max)}"
  )


def _first_line(stats) -> str:
  return (
    f"平均 {_summary_seconds(stats.first_average)}"
    f" / 中央値 {_summary_seconds(stats.first_median)}"
    f" / 最大 {_summary_seconds(stats.first_max)}"
    f"（{stats.first_count}件）"
  )


def format_summary_fields(summary) -> list[tuple[str, str]]:
  """集計をEmbedのフィールド（名前と値）の一覧にする。項目は添付の表の列に合わせる。

  生成時間と最初の音までは、作り方（ストリーミング・通常・非対応→通常）ごとに分けて並べ、
  ストリーミングあり・なしを比べられるようにする。
  """
  counts = (
    f"成功 {summary.success} / スキップ {summary.skipped} / 失敗 {summary.failed}"
  )
  if summary.skip_reasons:
    counts += "\n（スキップの内訳: " + "・".join(
      f"{_SKIP_LABELS.get(reason, reason)} {count}"
      for reason, count in sorted(summary.skip_reasons.items())
    ) + "）"
  if summary.mode_counts:
    counts += "\n（作り方: " + "・".join(
      f"{_MODE_LABELS[mode]} {summary.mode_counts[mode]}"
      for mode in MODES
      if mode in summary.mode_counts
    ) + "）"

  if summary.by_mode:
    bucket_labels = _char_bucket_labels()
    synthesis = "\n".join(
      f"{_MODE_LABELS[mode]}: {_synthesis_line(stats)}"
      for mode, stats in summary.by_mode.items()
    )
    first_lines = []
    for mode, stats in summary.by_mode.items():
      first_lines.append(f"{_MODE_LABELS[mode]}: {_first_line(stats)}")
      first_lines.append(
        "　文字数別の中央値: " + " / ".join(
          f"{label} {_summary_seconds(value)}"
          for label, value in zip(bucket_labels, stats.first_median_by_chars)
        )
      )
    first = "\n".join(first_lines)
  else:
    synthesis = "平均 — / 中央値 — / 95% — / 最大 —"
    first = "平均 — / 中央値 — / 最大 —"
  return [
    ("件数", counts),
    ("生成時間（成功のみ）", synthesis),
    ("最初の音まで（成功のみ・前の読み上げを待たなかったもの）", first),
    (
      "待ち時間（生成と再生の順番待ち）",
      f"平均 {_summary_seconds(summary.wait_average)}"
      f" / 最大 {_summary_seconds(summary.wait_max)}",
    ),
  ]


def _log_row(record) -> tuple[str, ...]:
  at = datetime.fromtimestamp(record.at, JST).strftime("%m/%d %H:%M:%S")
  skipped = record.outcome == SKIPPED
  if skipped:
    synthesis = "-"
  else:
    synthesis = f"{record.synth_seconds:.2f}s"
    if record.seconds_per_char is not None:
      synthesis += f" ({record.seconds_per_char * 1000:.0f}ms/char)"
  notes = []
  if record.mode:
    notes.append(record.mode)
  if record.attempts > 1:
    notes.append(f"attempts={record.attempts}")
  if record.reason:
    notes.append(record.reason)
  return (
    at,
    record.outcome.upper(),
    str(record.chars),
    synthesis,
    _log_seconds(record.first_seconds),
    "-" if skipped else _log_seconds(record.wait_seconds),
    "-" if record.speaker is None else str(record.speaker),
    " ".join(notes),
  )


def format_log_table(records) -> str:
  """記録を、列をそろえた表のテキストにする（1行目は見出し）。"""
  rows = [LOG_COLUMNS] + [_log_row(record) for record in records]
  widths = [max(len(row[index]) for row in rows) for index in range(len(LOG_COLUMNS))]
  lines = [
    " | ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
    for row in rows
  ]
  return "\n".join(lines) + "\n"


def build_log_file(records, now: datetime | None = None) -> discord.File:
  if now is None:
    now = datetime.now(timezone.utc)
  stamp = now.astimezone(JST).strftime("%Y%m%d-%H%M%S")
  data = format_log_table(records).encode("utf-8")
  return discord.File(io.BytesIO(data), filename=f"synthesis-log-{stamp}.txt")


def make_log_layout(
    summary,
    description: str,
    *,
    layout_type: EmbedType = EmbedType.INFO,
) -> LayoutMessage:
  """集計を載せたメッセージ（Components v2）を作る。

  Components v2 の中ではtxtファイルの中身を表示できないので、記録の一覧
  （build_log_file()）はこのメッセージに載せず、直後に別のメッセージで送る。
  """
  layout = make_layout(LOG_TITLE, description, layout_type=layout_type)
  layout.add_item(discord.ui.Separator())
  text = "\n".join(
    f"**{name}**\n{value}" for name, value in format_summary_fields(summary)
  )
  layout.add_item(discord.ui.TextDisplay(text))
  return layout
