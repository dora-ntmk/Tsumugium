"""Botの運営者（OPERATOR_USER_ID）だけが使うコマンドを登録する。

- /synthesis view・set・reset: VOICEVOXのストリーミング合成の設定を見る・変える・既定に戻す
- /synthesis log: 音声生成の記録を表示する
- /update: 新しいバージョンを確かめ、あればボタン付きのDMで更新を頼めるようにする

コマンドはBotとのDMにだけ出し、運営者以外が使ったときは断る。
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
from presentation.embeds import EmbedType, make_embed
from presentation.error_handler import handle_internal_error
from presentation.layouts import make_layout
from services.error_notification_service import ensure_error_notifier, operator_ids
from services.streaming_setting_service import MODE_AUTO


JST = timezone(timedelta(hours=9), "JST")
# 一覧はtxtファイルにして添付するので、記録をすべて出せる
MAX_LOG_COUNT = 500
DEFAULT_LOG_COUNT = MAX_LOG_COUNT
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


# ストリーミングの設定の上限（/synthesis set の引数）
MAX_STREAM_THRESHOLD = 500
MAX_STREAM_SEGMENT = 10.0

_STREAMING_MODE_LABELS = {
  "off": "使わない（off）",
  "on": "常に使う（on）",
  "hybrid": "文字数で振り分ける（hybrid）",
}
STREAMING_MODE_CHOICES = [
  discord.app_commands.Choice(name="auto（このバージョンの既定に従う）", value=MODE_AUTO),
  discord.app_commands.Choice(name="off（使わない）", value="off"),
  discord.app_commands.Choice(name="on（常に使う）", value="on"),
  discord.app_commands.Choice(name="hybrid（文字数で振り分ける）", value="hybrid"),
]


def _format_number(value: float) -> str:
  return f"{value:g}"


def format_streaming_setting(setting) -> str:
  """ストリーミングの設定を、/synthesis view などの本文にする。"""
  if setting.mode_setting == MODE_AUTO:
    mode = f"auto（今の既定: {_STREAMING_MODE_LABELS[setting.mode]}）"
  else:
    mode = _STREAMING_MODE_LABELS[setting.mode]
  if setting.threshold_chars > 0:
    threshold = f"{setting.threshold_chars}文字以上をストリーミング"
  else:
    threshold = "自動調整（今は文字数によらず五分五分で振り分ける）"
  if setting.segment_seconds > 0:
    segment = f"{_format_number(setting.segment_seconds)}秒"
  else:
    segment = f"自動調整（今は{_format_number(setting.effective_segment_seconds)}秒）"
  lines = [
    f"**モード**: {mode}",
    f"**基準の文字数**（hybridのとき）: {threshold}",
    f"**一度に作る長さ**: {segment}",
  ]
  if not setting.engine_supported:
    lines.append(
      "-# VOICEVOXが/streaming_synthesisに対応していないため、"
      "再起動するまで通常の生成を使います"
    )
  if setting.non_streaming_speakers:
    speakers = ", ".join(str(speaker) for speaker in setting.non_streaming_speakers)
    lines.append(f"-# ストリーミングに対応していない話者（通常の生成を使う）: {speakers}")
  return "\n".join(lines)


class OperatorCog:
  def __init__(
      self,
      tree,
      generation_log,
      operator_user_ids,
      error_notifier=None,
      update_watch=None,
      streaming_setting=None,
  ):
    self.tree = tree
    self.generation_log = generation_log
    self.update_watch = update_watch
    self.streaming_setting = streaming_setting
    self.operator_user_ids = operator_ids(operator_user_ids)
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._register()

  def is_operator(self, user) -> bool:
    return user.id in self.operator_user_ids

  def build_log_embed(self, record_count: int) -> discord.Embed:
    if record_count:
      description = f"直近{record_count}件（新しい順）を添付のファイルに書き出しました"
    else:
      description = "記録はまだありません"
    embed = make_embed("音声生成の記録", description)
    for name, value in format_summary_fields(self.generation_log.summarize()):
      embed.add_field(name=name, value=value, inline=False)
    return embed

  async def _refuse(self, ctx) -> None:
    layout = make_layout(
      "Bot運営者専用コマンドです",
      "このコマンドはBotの運営者以外は使用できません",
      layout_type=EmbedType.ERROR,
    )
    await ctx.response.send_message(view=layout.view, files=layout.files, ephemeral=True)

  async def _send_layout(self, ctx, layout) -> None:
    await ctx.response.send_message(view=layout.view, files=layout.files)

  def _streaming_unavailable(self):
    return make_layout(
      "ストリーミング合成の設定",
      "今は設定を変えることができません",
      layout_type=EmbedType.ERROR,
    )

  def _register(self) -> None:
    group = discord.app_commands.Group(
      name="synthesis",
      description="（運営者用）VOICEVOXの音声生成を設定・確認します",
      allowed_contexts=discord.app_commands.AppCommandContext(
        guild=False,
        dm_channel=True,
        private_channel=False,
      ),
      allowed_installs=discord.app_commands.AppInstallationType(guild=True, user=False),
    )

    @group.command(name="view", description="（運営者用）ストリーミング合成の設定を表示します")
    async def synthesis_view(ctx):
      try:
        if not self.is_operator(ctx.user):
          await self._refuse(ctx)
          return
        if self.streaming_setting is None:
          await self._send_layout(ctx, self._streaming_unavailable())
          return
        layout = make_layout(
          "ストリーミング合成の設定",
          format_streaming_setting(self.streaming_setting.current()),
        )
        await self._send_layout(ctx, layout)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as error:
        self.error_notifier.report_exception(error, "synthesis view HTTP")
      except Exception as error:
        await handle_internal_error(ctx, error, "synthesis view", self.error_notifier)

    @group.command(name="set", description="（運営者用）ストリーミング合成の設定を変えます")
    @discord.app_commands.describe(
      mode="ストリーミングを使うか",
      threshold=f"hybridのとき、この文字数以上をストリーミングする（0で自動調整、{MAX_STREAM_THRESHOLD}まで）",
      segment=f"一度に作る音声の長さ（秒。0で自動調整、{_format_number(MAX_STREAM_SEGMENT)}まで）",
    )
    @discord.app_commands.choices(mode=STREAMING_MODE_CHOICES)
    async def synthesis_set(
        ctx,
        mode: discord.app_commands.Choice[str] | None = None,
        threshold: discord.app_commands.Range[int, 0, MAX_STREAM_THRESHOLD] | None = None,
        segment: discord.app_commands.Range[float, 0.0, MAX_STREAM_SEGMENT] | None = None,
    ):
      try:
        if not self.is_operator(ctx.user):
          await self._refuse(ctx)
          return
        if self.streaming_setting is None:
          await self._send_layout(ctx, self._streaming_unavailable())
          return
        if mode is None and threshold is None and segment is None:
          layout = make_layout(
            "ストリーミング合成の設定",
            "変える項目（mode・threshold・segment）を1つ以上指定してください",
            layout_type=EmbedType.ERROR,
          )
          await self._send_layout(ctx, layout)
          return
        setting = self.streaming_setting.update(
          mode=None if mode is None else mode.value,
          threshold_chars=threshold,
          segment_seconds=segment,
        )
        layout = make_layout(
          "ストリーミング合成の設定を変えました",
          format_streaming_setting(setting)
          + "\n-# 次の生成から使います。/synthesis log には変える前の記録も残ります",
          layout_type=EmbedType.SUCCESS,
        )
        await self._send_layout(ctx, layout)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as error:
        self.error_notifier.report_exception(error, "synthesis set HTTP")
      except Exception as error:
        await handle_internal_error(ctx, error, "synthesis set", self.error_notifier)

    @group.command(name="reset", description="（運営者用）ストリーミング合成の設定を既定に戻します")
    async def synthesis_reset(ctx):
      try:
        if not self.is_operator(ctx.user):
          await self._refuse(ctx)
          return
        if self.streaming_setting is None:
          await self._send_layout(ctx, self._streaming_unavailable())
          return
        setting = self.streaming_setting.reset()
        layout = make_layout(
          "ストリーミング合成の設定を既定に戻しました",
          format_streaming_setting(setting)
          + "\n-# 次の生成から使います",
          layout_type=EmbedType.SUCCESS,
        )
        await self._send_layout(ctx, layout)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as error:
        self.error_notifier.report_exception(error, "synthesis reset HTTP")
      except Exception as error:
        await handle_internal_error(ctx, error, "synthesis reset", self.error_notifier)

    @group.command(name="log", description="（運営者用）音声生成の記録を表示します")
    @discord.app_commands.describe(
      count=f"書き出す件数（1〜{MAX_LOG_COUNT}、既定はすべて）",
    )
    async def synthesis_log(
        ctx,
        count: discord.app_commands.Range[int, 1, MAX_LOG_COUNT] = DEFAULT_LOG_COUNT,
    ):
      try:
        if not self.is_operator(ctx.user):
          embed = make_embed("音声生成の記録", "このコマンドはBotの運営者だけが使えます", embed_type=EmbedType.ERROR)
          await ctx.response.send_message(embed=embed, ephemeral=True)
          return
        await ctx.response.defer()
        records = self.generation_log.recent(count)
        attachments = [build_log_file(records)] if records else []
        embed = self.build_log_embed(len(records))
        await ctx.edit_original_response(embed=embed, attachments=attachments)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as error:
        self.error_notifier.report_exception(error, "synthesis log HTTP")
      except Exception as error:
        await handle_internal_error(ctx, error, "synthesis log", self.error_notifier)

    self.tree.add_command(group)

    @self.tree.command(
      name="update",
      description="（運営者用）Tsumugiumの更新を確認します",
    )
    @discord.app_commands.allowed_contexts(
      guilds=False,
      dms=True,
      private_channels=False,
    )
    @discord.app_commands.allowed_installs(guilds=True, users=False)
    async def update(ctx):
      try:
        if not self.is_operator(ctx.user):
          await self._refuse(ctx)
          return
        if self.update_watch is None:
          layout = make_layout("更新できません", "今は更新を受け付けることができません", layout_type=EmbedType.ERROR)
          await ctx.response.send_message(view=layout.view, files=layout.files)
          return
        # インストーラーが GitHub へ問い合わせるのを待つので、先に応答しておく
        await ctx.response.defer()
        layout = await self.update_watch.request_check(ctx.user.id)
        await ctx.edit_original_response(view=layout.view, attachments=layout.files)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as error:
        self.error_notifier.report_exception(error, "update HTTP")
      except Exception as error:
        await handle_internal_error(ctx, error, "update", self.error_notifier)
