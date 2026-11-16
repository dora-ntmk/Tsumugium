"""Botの運営者（OPERATOR_USER_ID）だけが使うコマンドを登録する。

コマンドはBotとのDMにだけ出し、運営者以外が使ったときは断る。
"""

from datetime import datetime, timedelta, timezone

import discord

from models.generation_record import (
  FAILED,
  SKIP_CLEARED,
  SKIP_DELETED,
  SKIP_EMPTY,
  SKIPPED,
  SUCCESS,
)
from presentation.embeds import EmbedType, make_embed
from presentation.error_handler import handle_internal_error
from services.error_notification_service import ensure_error_notifier


JST = timezone(timedelta(hours=9), "JST")
DEFAULT_LOG_COUNT = 10
MAX_LOG_COUNT = 25

_OUTCOME_LABELS = {SUCCESS: "成功", FAILED: "失敗", SKIPPED: "スキップ"}
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
      "最初の音まで（成功のみ）",
      f"平均 {_seconds(summary.first_audio_average)}"
      f" / 中央値 {_seconds(summary.first_audio_median)}"
      f" / 最大 {_seconds(summary.first_audio_max)}"
      f"（ストリーミング {summary.streamed}件）",
    ),
    (
      "順番待ち",
      f"平均 {_seconds(summary.wait_average)} / 最大 {_seconds(summary.wait_max)}",
    ),
  ]


def format_record(record) -> str:
  """記録1件を1行にする。"""
  at = datetime.fromtimestamp(record.at, JST).strftime("%m/%d %H:%M:%S")
  outcome = _OUTCOME_LABELS.get(record.outcome, record.outcome)
  if record.outcome == SKIPPED:
    detail = _SKIP_LABELS.get(record.reason, record.reason or "")
    if record.chars:
      detail += f" {record.chars}字"
    return f"{at} {outcome} {detail}"
  parts = [
    f"{record.chars}字",
    f"生成{record.synth_seconds:.2f}秒",
  ]
  if record.seconds_per_char is not None:
    parts.append(f"({record.seconds_per_char * 1000:.0f}ms/字)")
  if record.streamed and record.first_audio_seconds is not None:
    parts.append(f"最初の音{record.first_audio_seconds:.2f}秒")
  parts.append(f"待ち{record.wait_seconds:.2f}秒")
  parts.append(f"話者{record.speaker}")
  if record.attempts > 1:
    parts.append(f"試行{record.attempts}回")
  if record.outcome == FAILED and record.reason:
    parts.append(record.reason)
  return f"{at} {outcome} {' '.join(parts)}"


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

  def build_log_embed(self, count: int) -> discord.Embed:
    records = self.generation_log.recent(count)
    lines = [format_record(record) for record in records]
    description = None
    if lines:
      description = f"直近{len(lines)}件（新しい順）\n```\n" + "\n".join(lines) + "\n```"
    embed = make_embed("VOICEVOXの生成記録", description)
    for name, value in format_summary_fields(
        self.generation_log.summarize(), self.generation_log.capacity
    ):
      embed.add_field(name=name, value=value, inline=False)
    return embed

  def _register(self) -> None:
    @self.tree.command(
      name="voicevox-log",
      description="（運営者用）VOICEVOXの音声生成の記録を表示します",
    )
    @discord.app_commands.allowed_contexts(
      guilds=False,
      dms=True,
      private_channels=False,
    )
    @discord.app_commands.allowed_installs(guilds=True, users=False)
    @discord.app_commands.describe(
      count=f"表示する件数（1〜{MAX_LOG_COUNT}、既定は{DEFAULT_LOG_COUNT}）",
    )
    async def voicevox_log(
        ctx,
        count: discord.app_commands.Range[int, 1, MAX_LOG_COUNT] = DEFAULT_LOG_COUNT,
    ):
      try:
        if not self.is_operator(ctx.user):
          await ctx.response.send_message(
            embed=make_embed(
              "VOICEVOXの生成記録",
              "このコマンドはBotの運営者だけが使えます",
              embed_type=EmbedType.ERROR,
            ),
            ephemeral=True,
          )
          return
        await ctx.response.defer()
        await ctx.edit_original_response(embed=self.build_log_embed(count))
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as error:
        self.error_notifier.report_exception(error, "voicevox-log HTTP")
      except Exception as error:
        await handle_internal_error(ctx, error, "voicevox-log", self.error_notifier)
