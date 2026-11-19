"""運営者（OPERATOR_USER_ID）が接続時のお知らせを管理する /announce コマンド群を登録する。

コマンドはBotとのDMにだけ出し、運営者以外が使ったときは断る。
タイトル・本文・画像・リンクは Modal で受け取り、期間はコマンドの引数で受け取る。
"""

from dataclasses import dataclass, replace

import discord

from presentation.embeds import EmbedType, make_embed
from presentation.layouts import make_layout
from services.announcement_service import (
  MAX_BODY_LENGTH,
  MAX_IMAGE_BYTES,
  MAX_LINK_LABEL_LENGTH,
  MAX_LINK_URL_LENGTH,
  MAX_TITLE_LENGTH,
  TIME_FORMAT_HELP,
  AnnouncementDraft,
  AnnouncementError,
  check_image,
  check_image_size,
  is_clear_word,
  parse_time,
)
from services.error_notification_service import ensure_error_notifier


FORM_TIMEOUT_SECONDS = 15 * 60
PREVIEW_TITLE = "接続しました"
PREVIEW_DESCRIPTION = "（ボイスチャンネル）に接続しました。"
_IMAGE_HELP = f"PNG・JPEG・GIF・WebP、{MAX_IMAGE_BYTES // (1024 * 1024)}MBまで"


@dataclass(frozen=True)
class AnnouncementForm:
  """Modal に入れておく値と、Modal の外で決まった値（期間・今の画像）。"""
  announcement_id: int | None = None
  title: str = ""
  body: str = ""
  link_url: str = ""
  link_label: str = ""
  starts_at: int | None = None
  expires_at: int | None = None
  # 編集で残す今の画像（Modal のファイル欄には今の画像を入れておけないため）
  image: bytes | None = None
  image_name: str | None = None


class AnnouncementModal(discord.ui.Modal):
  def __init__(self, cog, form: AnnouncementForm):
    if form.announcement_id is None:
      title = "お知らせを追加"
      image_description = f"任意。{_IMAGE_HELP}"
    else:
      title = f"お知らせを編集（ID {form.announcement_id}）"
      current = "あり" if form.image is not None else "なし"
      image_description = f"空欄なら今の画像のまま（今: {current}）。{_IMAGE_HELP}"
    super().__init__(title=title, timeout=FORM_TIMEOUT_SECONDS)
    self.cog = cog
    self.form = form
    self.title_input = discord.ui.TextInput(
      style=discord.TextStyle.short,
      default=form.title or None,
      max_length=MAX_TITLE_LENGTH,
    )
    self.body_input = discord.ui.TextInput(
      style=discord.TextStyle.paragraph,
      default=form.body or None,
      max_length=MAX_BODY_LENGTH,
    )
    self.image_input = discord.ui.FileUpload(
      required=False,
      min_values=0,
      max_values=1,
    )
    self.link_url_input = discord.ui.TextInput(
      style=discord.TextStyle.short,
      default=form.link_url or None,
      placeholder="https://...",
      required=False,
      max_length=MAX_LINK_URL_LENGTH,
    )
    self.link_label_input = discord.ui.TextInput(
      style=discord.TextStyle.short,
      default=form.link_label or None,
      placeholder="空欄なら「詳しくはこちら」",
      required=False,
      max_length=MAX_LINK_LABEL_LENGTH,
    )
    self.add_item(discord.ui.Label(text="タイトル", component=self.title_input))
    self.add_item(discord.ui.Label(text="本文", component=self.body_input))
    self.add_item(
      discord.ui.Label(
        text="画像",
        description=image_description,
        component=self.image_input,
      )
    )
    self.add_item(discord.ui.Label(text="リンクURL", description="任意", component=self.link_url_input))
    self.add_item(
      discord.ui.Label(text="リンクの表示名", description="任意", component=self.link_label_input)
    )

  def entered_form(self) -> AnnouncementForm:
    """入力した文字を残したフォーム（入力し直すときに使う）。"""
    return replace(
      self.form,
      title=self.title_input.value or "",
      body=self.body_input.value or "",
      link_url=self.link_url_input.value or "",
      link_label=self.link_label_input.value or "",
    )

  async def on_submit(self, interaction: discord.Interaction) -> None:
    await self.cog.submit(interaction, self)

  async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
    await self.cog.report_error(interaction, error, "announce modal")


class RetryView(discord.ui.View):
  """入力に誤りがあったときに、入力した値を残したまま Modal を開き直すボタン。"""

  def __init__(self, cog, form: AnnouncementForm):
    super().__init__(timeout=FORM_TIMEOUT_SECONDS)
    self.cog = cog
    self.form = form

  @discord.ui.button(label="入力し直す", style=discord.ButtonStyle.primary)
  async def retry(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
    if not self.cog.is_operator(interaction.user):
      await self.cog.deny(interaction)
      return
    await interaction.response.send_modal(AnnouncementModal(self.cog, self.form))


class AnnouncementCog:
  def __init__(
      self,
      tree,
      announcement_service,
      operator_user_id: int | None,
      error_notifier=None,
  ):
    self.tree = tree
    self.announcement_service = announcement_service
    self.operator_user_id = operator_user_id
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._register()

  def is_operator(self, user) -> bool:
    return self.operator_user_id is not None and user.id == self.operator_user_id

  async def deny(self, interaction) -> None:
    await interaction.response.send_message(
      embed=make_embed(
        "お知らせ",
        "このコマンドはBotの運営者だけが使えます",
        embed_type=EmbedType.ERROR,
      ),
      ephemeral=True,
    )

  async def _send_error(self, interaction, message: str, view=None) -> None:
    embed = make_embed("お知らせ", message, embed_type=EmbedType.ERROR)
    kwargs = {"embed": embed}
    if view is not None:
      kwargs["view"] = view
    if interaction.response.is_done():
      await interaction.followup.send(**kwargs)
    else:
      await interaction.response.send_message(**kwargs)

  async def report_error(self, interaction, error: Exception, context: str) -> None:
    self.error_notifier.report_exception(error, context)
    try:
      await self._send_error(interaction, f"内部エラーが発生しました: {type(error).__name__}")
    except Exception as inner:
      self.error_notifier.report_exception(inner, f"{context} error response")

  def preview(self, announcements, *, header: str, hidden_count: int = 0):
    return make_layout(
      PREVIEW_TITLE,
      PREVIEW_DESCRIPTION,
      layout_type=EmbedType.SUCCESS,
      announcements=announcements,
      hidden_count=hidden_count,
      header=header,
      now=self.announcement_service.now(),
    )

  def resolve_period(
      self,
      starts: str | None,
      expires: str | None,
      *,
      current_starts: int | None = None,
      current_expires: int | None = None,
  ) -> tuple[int | None, int | None]:
    """引数の starts・expires を時刻にする。省いた引数は今の値のまま、none で外す。

    expires の `3d` などは、starts を指定したときは starts から数える。
    """
    now = self.announcement_service.now()
    starts_at = current_starts
    if starts is not None:
      starts_at = None if is_clear_word(starts) else parse_time(starts, now)
    expires_at = current_expires
    if expires is not None:
      base = starts_at if starts is not None else None
      expires_at = None if is_clear_word(expires) else parse_time(expires, now, base=base)
    return starts_at, expires_at

  async def submit(self, interaction: discord.Interaction, modal: AnnouncementModal) -> None:
    if not self.is_operator(interaction.user):
      await self.deny(interaction)
      return
    await interaction.response.defer(thinking=True)
    form = modal.entered_form()
    uploads = list(modal.image_input.values or [])
    try:
      image, image_name = form.image, form.image_name
      if uploads:
        attachment = uploads[0]
        check_image_size(attachment.size)
        data = await attachment.read()
        image, image_name = data, f"image.{check_image(data)}"
      draft = AnnouncementDraft(
        title=form.title,
        body=form.body,
        link_url=form.link_url,
        link_label=form.link_label,
        starts_at=form.starts_at,
        expires_at=form.expires_at,
        image=image,
        image_name=image_name,
      )
      if form.announcement_id is None:
        announcement_id = self.announcement_service.add(draft)
        verb = "追加"
      else:
        announcement_id = form.announcement_id
        self.announcement_service.update(announcement_id, draft)
        verb = "更新"
    except AnnouncementError as error:
      message = str(error)
      if uploads:
        message += "\n画像は入力し直すときに選び直してください"
      await self._send_error(interaction, message, view=RetryView(self, form))
      return
    except discord.HTTPException as error:
      self.error_notifier.report_exception(error, "announce image download")
      await self._send_error(
        interaction,
        "画像を読み込めませんでした。時間をおいて入力し直してください",
        view=RetryView(self, form),
      )
      return

    announcement = self.announcement_service.repository.get(announcement_id)
    status = self.announcement_service.status_text(announcement)
    layout = self.preview(
      [announcement],
      header=f"ID {announcement_id} のお知らせを{verb}しました（{status}）。"
      "接続したときは次のように表示されます",
    )
    await interaction.followup.send(view=layout.view, files=layout.files)

  async def id_autocomplete(self, interaction, current: str):
    current = (current or "").strip()
    choices = []
    for announcement in self.announcement_service.list_all():
      name = (
        f"{announcement.id}: {announcement.title}"
        f"（{self.announcement_service.status_text(announcement)}）"
      )
      if current and current not in name:
        continue
      if len(name) > 100:
        name = name[:99] + "…"
      choices.append(discord.app_commands.Choice(name=name, value=announcement.id))
      if len(choices) >= 25:
        break
    return choices

  def build_list_embed(self) -> discord.Embed:
    announcements = self.announcement_service.list_all()
    if not announcements:
      return make_embed("お知らせの一覧", "お知らせはありません")
    embed = make_embed(
      "お知らせの一覧",
      f"{len(announcements)}件（接続メッセージには表示中のものを上から3件まで載せます）",
    )
    for announcement in announcements[:25]:
      extras = []
      if announcement.image is not None:
        extras.append("画像あり")
      if announcement.link_url:
        extras.append("リンクあり")
      value = self.announcement_service.status_text(announcement)
      if extras:
        value += "・" + "・".join(extras)
      body = announcement.body.replace("\n", " ")
      if len(body) > 60:
        body = body[:59] + "…"
      title = announcement.title
      if len(title) > 200:
        title = title[:199] + "…"
      embed.add_field(
        name=f"ID {announcement.id}｜{title}",
        value=f"{value}\n{body}",
        inline=False,
      )
    return embed

  def _register(self) -> None:
    group = discord.app_commands.Group(
      name="announce",
      description="（運営者用）接続時のお知らせを管理します",
      allowed_contexts=discord.app_commands.AppCommandContext(
        guild=False,
        dm_channel=True,
        private_channel=False,
      ),
      allowed_installs=discord.app_commands.AppInstallationType(guild=True, user=False),
    )

    @group.command(name="add", description="（運営者用）お知らせを追加します")
    @discord.app_commands.describe(
      expires=f"表示終了。省くと恒久（{TIME_FORMAT_HELP}）",
      starts=f"表示開始。省くとすぐ（{TIME_FORMAT_HELP}）",
    )
    async def add(ctx, expires: str | None = None, starts: str | None = None):
      try:
        if not self.is_operator(ctx.user):
          await self.deny(ctx)
          return
        try:
          starts_at, expires_at = self.resolve_period(starts, expires)
        except AnnouncementError as error:
          await self._send_error(ctx, str(error))
          return
        form = AnnouncementForm(starts_at=starts_at, expires_at=expires_at)
        await ctx.response.send_modal(AnnouncementModal(self, form))
      except discord.errors.InteractionResponded:
        return
      except Exception as error:
        await self.report_error(ctx, error, "announce add")

    @group.command(name="edit", description="（運営者用）お知らせを編集します")
    @discord.app_commands.describe(
      id="編集するお知らせ",
      expires=f"表示終了。省くと今のまま、noneで恒久（{TIME_FORMAT_HELP}）",
      starts=f"表示開始。省くと今のまま、noneですぐ（{TIME_FORMAT_HELP}）",
      remove_image="Trueにすると画像を消します（新しい画像を選んだときはそちらを使います）",
    )
    @discord.app_commands.autocomplete(id=self.id_autocomplete)
    async def edit(
        ctx,
        id: int,
        expires: str | None = None,
        starts: str | None = None,
        remove_image: bool = False,
    ):
      try:
        if not self.is_operator(ctx.user):
          await self.deny(ctx)
          return
        announcement = self.announcement_service.get(id)
        if announcement is None:
          await self._send_error(ctx, f"ID {id} のお知らせが見つかりません")
          return
        try:
          starts_at, expires_at = self.resolve_period(
            starts,
            expires,
            current_starts=announcement.starts_at,
            current_expires=announcement.expires_at,
          )
        except AnnouncementError as error:
          await self._send_error(ctx, str(error))
          return
        form = AnnouncementForm(
          announcement_id=announcement.id,
          title=announcement.title,
          body=announcement.body,
          link_url=announcement.link_url or "",
          link_label=announcement.link_label or "",
          starts_at=starts_at,
          expires_at=expires_at,
          image=None if remove_image else announcement.image,
          image_name=None if remove_image else announcement.image_name,
        )
        await ctx.response.send_modal(AnnouncementModal(self, form))
      except discord.errors.InteractionResponded:
        return
      except Exception as error:
        await self.report_error(ctx, error, "announce edit")

    @group.command(name="list", description="（運営者用）お知らせの一覧を表示します")
    async def list_(ctx):
      try:
        if not self.is_operator(ctx.user):
          await self.deny(ctx)
          return
        await ctx.response.send_message(embed=self.build_list_embed())
      except discord.errors.InteractionResponded:
        return
      except Exception as error:
        await self.report_error(ctx, error, "announce list")

    @group.command(name="del", description="（運営者用）お知らせを削除します")
    @discord.app_commands.describe(id="削除するお知らせ")
    @discord.app_commands.autocomplete(id=self.id_autocomplete)
    async def delete(ctx, id: int):
      try:
        if not self.is_operator(ctx.user):
          await self.deny(ctx)
          return
        announcement = self.announcement_service.get(id)
        if announcement is None or not self.announcement_service.delete(id):
          await self._send_error(ctx, f"ID {id} のお知らせが見つかりません")
          return
        await ctx.response.send_message(
          embed=make_embed(
            "お知らせを削除しました",
            f"ID {id}「{announcement.title}」",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except Exception as error:
        await self.report_error(ctx, error, "announce del")

    @group.command(
      name="preview",
      description="（運営者用）接続メッセージでのお知らせの見え方を表示します",
    )
    @discord.app_commands.describe(id="1件だけ見るときに指定（省くと今の接続メッセージと同じもの）")
    @discord.app_commands.autocomplete(id=self.id_autocomplete)
    async def preview(ctx, id: int | None = None):
      try:
        if not self.is_operator(ctx.user):
          await self.deny(ctx)
          return
        if id is None:
          announcements, hidden_count = self.announcement_service.display()
          header = (
            "今の接続メッセージ" if announcements
            else "今表示するお知らせはありません。接続メッセージは次のようになります"
          )
        else:
          announcement = self.announcement_service.get(id)
          if announcement is None:
            await self._send_error(ctx, f"ID {id} のお知らせが見つかりません")
            return
          announcements, hidden_count = [announcement], 0
          header = (
            f"ID {id} のお知らせ"
            f"（{self.announcement_service.status_text(announcement)}）"
          )
        await ctx.response.defer(thinking=True)
        layout = self.preview(announcements, header=header, hidden_count=hidden_count)
        await ctx.followup.send(view=layout.view, files=layout.files)
      except discord.errors.InteractionResponded:
        return
      except Exception as error:
        await self.report_error(ctx, error, "announce preview")

    self.tree.add_command(group)
