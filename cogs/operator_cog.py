"""Botの運営者（OPERATOR_USER_ID）だけが使うコマンドを登録する。

- /synthesis view・set・reset: VOICEVOXのストリーミング合成の設定を見る・変える・初期値に戻す
- /synthesis log: 音声生成の記録を表示する
- /update: 新しいバージョンを確かめ、あればボタン付きのDMで更新を頼めるようにする

コマンドはBotとのDMにだけ出し、運営者以外が使ったときは断る。
"""

import discord

from presentation.embeds import EmbedType
from presentation.error_handler import handle_internal_error
from presentation.generation_log_format import build_log_file, make_log_layout
from presentation.layouts import make_layout
from services.error_notification_service import ensure_error_notifier, operator_ids


# 一覧はtxtファイルにして添付するので、記録をすべて出せる
MAX_LOG_COUNT = 500
DEFAULT_LOG_COUNT = MAX_LOG_COUNT

# ストリーミングの設定の上限（/synthesis set の引数）
MAX_STREAM_THRESHOLD = 500
MAX_STREAM_SEGMENT = 10.0

_STREAMING_MODE_LABELS = {
  "off": "使わない（off）",
  "on": "常に使う（on）",
  "hybrid": "文字数で振り分ける（hybrid）",
}
STREAMING_MODE_CHOICES = [
  discord.app_commands.Choice(name="off（使わない）", value="off"),
  discord.app_commands.Choice(name="on（常に使う）", value="on"),
  discord.app_commands.Choice(name="hybrid（文字数で振り分ける）", value="hybrid"),
]


def _format_number(value: float) -> str:
  return f"{value:g}"


def format_streaming_setting(setting) -> str:
  """ストリーミングの設定を、/synthesis view などの本文にする。"""
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

  def build_log_layout(self, records):
    """記録全体の集計を載せたメッセージを作る（一覧のファイルは別に送る）。"""
    if records:
      description = f"直近{len(records)}件（新しい順）を次のメッセージのファイルに書き出しました"
    else:
      description = "記録はまだありません"
    return make_log_layout(self.generation_log.summarize(), description)

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

    @group.command(name="reset", description="（運営者用）ストリーミング合成の設定を初期値に戻します")
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
          "ストリーミング合成の設定を初期値に戻しました",
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
          layout = make_layout(
            "音声生成の記録",
            "このコマンドはBotの運営者だけが使えます",
            layout_type=EmbedType.ERROR,
          )
          await ctx.response.send_message(view=layout.view, files=layout.files, ephemeral=True)
          return
        await ctx.response.defer()
        records = self.generation_log.recent(count)
        layout = self.build_log_layout(records)
        await ctx.edit_original_response(view=layout.view, attachments=layout.files)
        if records:
          # Components v2 の中ではtxtの中身を表示できないので、ファイルだけのメッセージで送る
          await ctx.followup.send(file=build_log_file(records))
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
