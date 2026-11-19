"""Botの運営者（OPERATOR_USER_ID）だけが使うコマンドを登録する。

コマンドはBotとのDMにだけ出し、運営者以外が使ったときは断る。
"""

import io
from datetime import datetime, timedelta, timezone

import discord

from models.generation_record import (
  SKIP_CLEARED,
  SKIP_DELETED,
  SKIP_EMPTY,
  SKIPPED,
)
from presentation.embeds import EmbedType, make_embed
from presentation.error_handler import handle_internal_error
from services.error_notification_service import ensure_error_notifier


JST = timezone(timedelta(hours=9), "JST")
# 一覧はtxtファイルにして添付するので、記録をすべて出せる
MAX_LOG_COUNT = 500
DEFAULT_LOG_COUNT = MAX_LOG_COUNT
LOG_COLUMNS = (
  "datetime", "result", "chars", "synthesis", "first", "wait", "speaker", "note",
)

_SKIP_LABELS = {
  SKIP_EMPTY: "読む文字なし",
  SKIP_CLEARED: "クリア",
  SKIP_DELETED: "削除",
}


def _seconds(value) -> str:
  return "—" if value is None else f"{value:.2f}秒"


def _milliseconds(value) -> str:
  return "—" if value is None else f"{value * 1000:.0f}ms"


def format_summary_fields(summary, capacity: int) -> list[tuple[str, str]]:
  """集計をEmbedのフィールド（名前と値）の一覧にする。"""
  skip_detail = "・".join(
    f"{_SKIP_LABELS.get(reason, reason)} {count}"
    for reason, count in sorted(summary.skip_reasons.items())
  )
  counts = (
    f"成功 {summary.success} / スキップ {summary.skipped} / 失敗 {summary.failed}"
  )
  if skip_detail:
    counts += f"\n（スキップの内訳: {skip_detail}）"
  if summary.first_at is None:
    period = f"記録はまだありません（直近{capacity}件まで・再起動で消えます）"
  else:
    period = (
      f"<t:{int(summary.first_at)}:f> から {summary.total}件"
      f"（直近{capacity}件まで・再起動で消えます）"
    )
  return [
    ("記録の範囲", period),
    ("件数", counts),
    (
      "生成時間（成功のみ）",
      f"平均 {_seconds(summary.synth_average)} / 中央値 {_seconds(summary.synth_median)}"
      f" / 95% {_seconds(summary.synth_p95)} / 最大 {_seconds(summary.synth_max)}",
    ),
    ("1文字あたり（成功のみ）", f"平均 {_milliseconds(summary.seconds_per_char_average)}"),
    (
      "最初の音まで（メッセージから・順番待ちを除く）",
      f"平均 {_seconds(summary.first_average)}"
      f" / 中央値 {_seconds(summary.first_median)}"
      f" / 最大 {_seconds(summary.first_max)}"
      f"（ストリーミング {summary.streamed}件）",
    ),
    (
      "順番待ち",
      f"平均 {_seconds(summary.wait_average)} / 最大 {_seconds(summary.wait_max)}",
    ),
  ]


def _log_seconds(value) -> str:
  return "-" if value is None else f"{value:.2f}s"


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
  if record.streamed:
    notes.append("stream")
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


class OperatorCog:
  def __init__(
      self,
      tree,
      generation_log,
      operator_user_id: int | None,
      error_notifier=None,
  ):
    self.tree = tree
    self.generation_log = generation_log
    self.operator_user_id = operator_user_id
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._register()

  def is_operator(self, user) -> bool:
    return self.operator_user_id is not None and user.id == self.operator_user_id

  def build_log_embed(self, record_count: int) -> discord.Embed:
    description = None
    if record_count:
      description = f"直近{record_count}件（新しい順）を添付のファイルに書き出しました"
    embed = make_embed("音声生成の記録", description)
    for name, value in format_summary_fields(
        self.generation_log.summarize(), self.generation_log.capacity
    ):
      embed.add_field(name=name, value=value, inline=False)
    return embed

  def _register(self) -> None:
    @self.tree.command(
      name="synthesis-log",
      description="（運営者用）音声生成の記録を表示します",
    )
    @discord.app_commands.allowed_contexts(
      guilds=False,
      dms=True,
      private_channels=False,
    )
    @discord.app_commands.allowed_installs(guilds=True, users=False)
    @discord.app_commands.describe(
      count=f"書き出す件数（1〜{MAX_LOG_COUNT}、既定はすべて）",
    )
    async def synthesis_log(
        ctx,
        count: discord.app_commands.Range[int, 1, MAX_LOG_COUNT] = DEFAULT_LOG_COUNT,
    ):
      try:
        if not self.is_operator(ctx.user):
          await ctx.response.send_message(
            embed=make_embed(
              "音声生成の記録",
              "このコマンドはBotの運営者だけが使えます",
              embed_type=EmbedType.ERROR,
            ),
            ephemeral=True,
          )
          return
        await ctx.response.defer()
        records = self.generation_log.recent(count)
        attachments = [build_log_file(records)] if records else []
        await ctx.edit_original_response(
          embed=self.build_log_embed(len(records)),
          attachments=attachments,
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as error:
        self.error_notifier.report_exception(error, "synthesis-log HTTP")
      except Exception as error:
        await handle_internal_error(ctx, error, "synthesis-log", self.error_notifier)
