"""運営者からのお知らせ（Repository・Service・表示・/announce・接続メッセージ）のテスト。"""

import asyncio
import os
import sqlite3
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import discord
from discord import app_commands

from cogs.announcement_cog import (
  AnnouncementCog,
  AnnouncementConfirm,
  AnnouncementForm,
  AnnouncementModal,
  ConfirmView,
  RetryView,
)
from presentation.embeds import EmbedType, embed_color
from presentation.announcement_layout import add_announcements
from presentation.layouts import make_layout
from repositories.announcement_repository import AnnouncementRepository
from repositories.guild_config_repository import GuildConfigRepository
from models.announcement import JST
from services.announcement_service import (
  MAX_BODY_LENGTH,
  MAX_DISPLAY,
  MAX_IMAGE_BYTES,
  MAX_TITLE_LENGTH,
  AnnouncementDraft,
  AnnouncementError,
  AnnouncementService,
  check_image,
  parse_time,
  validate,
)
from services.connection_service import ConnectionService
from services.voice_service import VoiceService
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_OTHER_USER_ID,
  DUMMY_TEXT_CHANNEL_ID,
  DUMMY_USER_ID,
  DUMMY_VOICE_CHANNEL_ID,
  RecordingNotifier,
  TempDirTestCase,
)


# 2027-01-15 17:00（日本時間）。テストの「今」
NOW = 1_800_000_000
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class Clock:
  def __init__(self, now=NOW):
    self.now = now

  def __call__(self):
    return self.now


def walk(view):
  return list(view.walk_children())


def texts(view):
  return [item.content for item in walk(view) if isinstance(item, discord.ui.TextDisplay)]


class ServiceTestCase(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.clock = Clock()
    self.repository = AnnouncementRepository(self.tmp_path("db", "config.db"))
    self.addCleanup(self.repository.close)
    self.service = AnnouncementService(self.repository, clock=self.clock)

  def add(self, title="たいとる", body="ほんぶん", **kwargs):
    return self.service.add(AnnouncementDraft(title=title, body=body, **kwargs))


class RepositoryTest(ServiceTestCase):
  def test_shares_config_db_with_guild_config(self):
    db_path = self.tmp_path("db", "shared.db")
    guild_config = GuildConfigRepository(db_path, error_notifier=RecordingNotifier())
    self.addCleanup(guild_config.close)
    repository = AnnouncementRepository(db_path)
    self.addCleanup(repository.close)
    repository.add(title="t", body="b")
    with sqlite3.connect(db_path) as conn:
      tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    self.assertTrue({"guild_config", "announcements"} <= tables)

  def test_add_get_update_delete(self):
    announcement_id = self.add(image=PNG, image_name="image.png", link_url="https://example.com")
    stored = self.repository.get(announcement_id)
    self.assertEqual(stored.image, PNG)
    self.assertEqual(stored.created_at, NOW)
    self.assertTrue(self.repository.update(announcement_id, title="あたらしい", image=None, image_name=None))
    self.assertEqual(self.repository.get(announcement_id).title, "あたらしい")
    self.assertIsNone(self.repository.get(announcement_id).image)
    self.assertTrue(self.repository.delete(announcement_id))
    self.assertIsNone(self.repository.get(announcement_id))
    self.assertFalse(self.repository.delete(announcement_id))

  def test_update_rejects_unknown_column(self):
    announcement_id = self.add()
    with self.assertRaises(ValueError):
      self.repository.update(announcement_id, created_at=0)

  def test_expired_rows_are_removed_on_start(self):
    self.add(expires_at=NOW + 60)
    self.add(title="恒久")
    self.clock.now = NOW + 61
    AnnouncementService(self.repository, clock=self.clock)
    self.assertEqual([item.title for item in self.repository.list_all()], ["恒久"])


class ParseTimeTest(unittest.TestCase):
  def test_relative(self):
    self.assertEqual(parse_time("3d", NOW), NOW + 3 * 86400)
    self.assertEqual(parse_time("12h", NOW), NOW + 12 * 3600)
    self.assertEqual(parse_time("1d12h30m", NOW), NOW + 86400 + 12 * 3600 + 1800)
    self.assertEqual(parse_time("１２Ｈ", NOW), NOW + 12 * 3600)

  def test_relative_counts_from_base(self):
    self.assertEqual(parse_time("1h", NOW, base=NOW + 100), NOW + 100 + 3600)

  def test_absolute_is_japan_time(self):
    expected = int(datetime(2027, 2, 1, 18, 0, tzinfo=JST).timestamp())
    self.assertEqual(parse_time("2027-02-01 18:00", NOW), expected)
    self.assertEqual(parse_time("2027/02/01  18:00", NOW), expected)
    self.assertEqual(
      parse_time("2027-02-01", NOW),
      int(datetime(2027, 2, 1, tzinfo=JST).timestamp()),
    )

  def test_blank_is_none(self):
    self.assertIsNone(parse_time(None, NOW))
    self.assertIsNone(parse_time("  ", NOW))

  def test_invalid(self):
    for text in ("あした", "0d", "2027-13-01 00:00", "3w"):
      with self.subTest(text=text), self.assertRaises(AnnouncementError):
        parse_time(text, NOW)


class ValidateTest(unittest.TestCase):
  def check(self, **kwargs):
    values = {"title": "たいとる", "body": "ほんぶん"}
    values.update(kwargs)
    return validate(AnnouncementDraft(**values), NOW)

  def test_strips_and_blanks_become_none(self):
    checked = self.check(title="  たいとる ", link_url=" ", link_label="")
    self.assertEqual(checked.title, "たいとる")
    self.assertIsNone(checked.link_url)
    self.assertIsNone(checked.link_label)

  def test_errors(self):
    cases = {
      "empty title": {"title": " "},
      "empty body": {"body": ""},
      "long body": {"body": "あ" * (MAX_BODY_LENGTH + 1)},
      "label without url": {"link_label": "みる"},
      "not http": {"link_url": "javascript:alert(1)"},
      "no host": {"link_url": "https://"},
      "expired": {"expires_at": NOW},
      "starts after expires": {"starts_at": NOW + 100, "expires_at": NOW + 50},
      "not image": {"image": b"hello", "image_name": "image.png"},
    }
    for name, kwargs in cases.items():
      with self.subTest(name), self.assertRaises(AnnouncementError):
        self.check(**kwargs)

  def test_image_types_and_size(self):
    self.assertEqual(check_image(PNG), "png")
    self.assertEqual(check_image(b"\xff\xd8\xff\xe0rest"), "jpg")
    self.assertEqual(check_image(b"GIF89a..."), "gif")
    self.assertEqual(check_image(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "webp")
    with self.assertRaises(AnnouncementError):
      check_image(PNG + b"\x00" * MAX_IMAGE_BYTES)


class DisplayTest(ServiceTestCase):
  def test_permanent_first_then_nearest_expiry(self):
    self.add(title="期限おそい", expires_at=NOW + 7200)
    self.add(title="恒久1")
    self.add(title="期限ちかい", expires_at=NOW + 3600)
    self.clock.now += 1
    self.add(title="恒久2")
    self.assertEqual(
      [item.title for item in self.service.active()],
      ["恒久1", "恒久2", "期限ちかい", "期限おそい"],
    )

  def test_display_limit_and_hidden_count(self):
    for index in range(MAX_DISPLAY + 2):
      self.add(title=f"お知らせ{index}")
    shown, hidden = self.service.display()
    self.assertEqual(len(shown), MAX_DISPLAY)
    self.assertEqual(hidden, 2)

  def test_scheduled_and_expired_are_not_displayed(self):
    self.add(title="予約", starts_at=NOW + 60)
    self.add(title="すぐ消える", expires_at=NOW + 60)
    self.assertEqual([item.title for item in self.service.active()], ["すぐ消える"])
    self.clock.now = NOW + 60
    self.assertEqual([item.title for item in self.service.active()], ["予約"])
    self.assertEqual([item.title for item in self.service.list_all()], ["予約"])

  def test_list_all_puts_scheduled_last(self):
    self.add(title="予約", starts_at=NOW + 60)
    self.add(title="表示中")
    self.assertEqual([item.title for item in self.service.list_all()], ["表示中", "予約"])

  def test_status_text(self):
    permanent = self.service.get(self.add())
    scheduled = self.service.get(self.add(starts_at=NOW + 3600, expires_at=NOW + 7200))
    self.assertEqual(self.service.status_text(permanent), "表示中・恒久")
    self.assertEqual(self.service.status_text(scheduled), "予約・01/15 18:00から（01/15 19:00まで）")

  def test_update_missing_raises(self):
    with self.assertRaises(AnnouncementError):
      self.service.update(999, AnnouncementDraft(title="t", body="b"))


class LayoutTest(ServiceTestCase):
  def test_without_announcements_has_no_separator(self):
    layout = make_layout("接続しました", "<#1>に接続しました。", layout_type=EmbedType.SUCCESS)
    container = layout.view.children[0]
    self.assertIsInstance(container, discord.ui.Container)
    self.assertEqual(container.accent_colour, embed_color(EmbedType.SUCCESS))
    self.assertEqual(texts(layout.view), ["## 接続しました\n<#1>に接続しました。"])
    self.assertFalse(any(isinstance(item, discord.ui.Separator) for item in walk(layout.view)))
    self.assertTrue(layout.view.has_components_v2())
    self.assertEqual(layout.files, [])

  def test_add_item_appends_inside_container(self):
    layout = make_layout("t", "b", layout_type=EmbedType.WARNING)
    returned = layout.add_item(discord.ui.TextDisplay("足した"))
    self.assertIs(returned, layout)
    self.assertEqual(layout.container.children[-1].content, "足した")
    self.assertEqual(layout.view.total_children_count, 3)

  def test_no_announcements_adds_nothing(self):
    layout = add_announcements(make_layout("t"), [], hidden_count=3)
    self.assertEqual(texts(layout.view), ["## t"])

  def test_announcements_with_image_link_and_period(self):
    self.add(title="恒久", body="ずっと")
    self.add(
      title="メンテ",
      body="あした",
      expires_at=NOW + 3600,
      image=PNG,
      image_name="image.png",
      link_url="https://example.com/a",
    )
    shown, hidden = self.service.display()
    layout = add_announcements(make_layout("接続しました", "本文"), shown, hidden_count=hidden)
    children = layout.container.children
    # 「お知らせ」の見出しは付けず、お知らせごとに large の区切り線を引く
    self.assertEqual(
      [type(item).__name__ for item in children],
      ["TextDisplay", "Separator", "TextDisplay", "Separator", "Section", "MediaGallery"],
    )
    self.assertTrue(all(
      item.spacing == discord.SeparatorSpacing.large
      for item in children if isinstance(item, discord.ui.Separator)
    ))
    self.assertNotIn("📢", "".join(texts(layout.view)))
    self.assertEqual(children[2].content, "## 恒久\nずっと")
    section = children[4]
    # 期限は表示しない
    self.assertEqual(section.children[0].content, "## メンテ\nあした")
    self.assertEqual(section.accessory.url, "https://example.com/a")
    self.assertEqual(section.accessory.label, "詳しくはこちら")
    self.assertEqual(section.accessory.style, discord.ButtonStyle.link)
    filename = layout.files[0].filename
    self.assertEqual(children[5].items[0].media.url, f"attachment://{filename}")
    self.assertTrue(filename.endswith(".png"))

  def test_without_image_permission_skips_images(self):
    self.add(image=PNG, image_name="image.png")
    layout = add_announcements(make_layout("t"), self.service.active(), include_images=False)
    self.assertEqual(layout.files, [])
    self.assertFalse(any(isinstance(item, discord.ui.MediaGallery) for item in walk(layout.view)))

  def test_hidden_count_and_header(self):
    self.add()
    layout = add_announcements(make_layout("t", header="ヘッダー"), self.service.active(), hidden_count=2)
    self.assertIsInstance(layout.view.children[0], discord.ui.TextDisplay)
    self.assertEqual(layout.view.children[0].content, "ヘッダー")
    self.assertIn("-# ほか2件のお知らせがあります", texts(layout.view))

  def test_maximum_content_fits_discord_limits(self):
    for index in range(MAX_DISPLAY):
      self.add(
        title="あ" * 100,
        body="い" * MAX_BODY_LENGTH,
        expires_at=NOW + 60,
        image=PNG,
        image_name="image.png",
        link_url="https://example.com/" + "a" * 400,
        link_label="う" * 80,
      )
    layout = add_announcements(
      make_layout("接続しました", "<#1>に接続しました。", header="h"),
      self.service.active(),
      hidden_count=99,
    )
    self.assertLessEqual(layout.view.content_length(), 4000)
    self.assertLessEqual(layout.view.total_children_count, 40)


class FakeResponse:
  def __init__(self):
    self.done = False
    self.modal = None
    self.sent = []

  def is_done(self):
    return self.done

  async def send_modal(self, modal):
    self.done = True
    self.modal = modal

  async def send_message(self, **kwargs):
    self.done = True
    self.sent.append(kwargs)

  async def defer(self, **kwargs):
    self.done = True

  async def edit_message(self, **kwargs):
    self.done = True
    self.edited = kwargs


class FakeMessage:
  def __init__(self, kwargs):
    self.sent = kwargs
    self.edits = []

  async def edit(self, **kwargs):
    self.edits.append(kwargs)


class FakeFollowup:
  def __init__(self):
    self.sent = []
    self.messages = []

  async def send(self, **kwargs):
    self.sent.append(kwargs)
    message = FakeMessage(kwargs)
    self.messages.append(message)
    return message


class FakeInteraction:
  def __init__(self, user_id=DUMMY_USER_ID):
    self.user = SimpleNamespace(id=user_id)
    self.response = FakeResponse()
    self.followup = FakeFollowup()


class FakeAttachment:
  def __init__(self, data, size=None):
    self.data = data
    self.size = len(data) if size is None else size
    self.read = mock.AsyncMock(return_value=data)


def fill(modal, *, title="たいとる", body="ほんぶん", link_url="", link_label="", uploads=()):
  modal.title_input._value = title
  modal.body_input._value = body
  modal.link_url_input._value = link_url
  modal.link_label_input._value = link_label
  modal.image_input._values = list(uploads)


class AnnounceCommandTest(ServiceTestCase):
  def setUp(self):
    super().setUp()
    self.client = discord.Client(intents=discord.Intents.none())
    self.tree = app_commands.CommandTree(self.client)
    self.notifier = RecordingNotifier()
    self.cog = AnnouncementCog(self.tree, self.service, DUMMY_USER_ID, self.notifier)
    self.group = self.tree.get_command("announce")

  def command(self, name):
    return self.group.get_command(name).callback

  def run_command(self, name, interaction=None, **kwargs):
    interaction = interaction or FakeInteraction()
    asyncio.run(self.command(name)(interaction, **kwargs))
    return interaction

  def submit(self, modal, **kwargs):
    fill(modal, **kwargs)
    interaction = FakeInteraction()
    asyncio.run(modal.on_submit(interaction))
    return interaction

  def press(self, view, button, user_id=DUMMY_USER_ID):
    """ConfirmView のボタンを押す（interaction_check も通す）。"""
    interaction = FakeInteraction(user_id)

    async def run():
      if await view.interaction_check(interaction):
        await button.callback(interaction)

    asyncio.run(run())
    return interaction

  def submit_and_send(self, modal, **kwargs):
    view = self.submit(modal, **kwargs).followup.sent[0]["view"]
    return self.press(view, view.send_button)

  def test_group_is_dm_only(self):
    self.assertFalse(self.group.allowed_contexts.guild)
    self.assertTrue(self.group.allowed_contexts.dm_channel)
    self.assertEqual(
      sorted(command.name for command in self.group.commands),
      ["add", "del", "edit", "list", "preview"],
    )

  def test_non_operator_is_denied(self):
    for name, kwargs in (("add", {}), ("list", {}), ("del", {"id": 1}), ("preview", {})):
      with self.subTest(name):
        interaction = self.run_command(name, FakeInteraction(DUMMY_OTHER_USER_ID), **kwargs)
        self.assertIsNone(interaction.response.modal)
        self.assertTrue(interaction.response.sent[0]["ephemeral"])

  def test_any_of_several_operators_can_use(self):
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    cog = AnnouncementCog(tree, self.service, (DUMMY_USER_ID, DUMMY_OTHER_USER_ID), self.notifier)
    self.assertTrue(cog.is_operator(type("U", (), {"id": DUMMY_OTHER_USER_ID})()))
    callback = tree.get_command("announce").get_command("add").callback
    interaction = FakeInteraction(DUMMY_OTHER_USER_ID)
    asyncio.run(callback(interaction))
    self.assertIsInstance(interaction.response.modal, AnnouncementModal)

  def test_add_opens_modal_with_period(self):
    interaction = self.run_command("add", expires="3d", starts="1h")
    modal = interaction.response.modal
    self.assertIsInstance(modal, AnnouncementModal)
    self.assertEqual(modal.form.starts_at, NOW + 3600)
    # 期間は表示開始から数える
    self.assertEqual(modal.form.expires_at, NOW + 3600 + 3 * 86400)
    self.assertEqual(len(modal.children), 5)

  def test_add_with_bad_period_does_not_open_modal(self):
    interaction = self.run_command("add", expires="あした")
    self.assertIsNone(interaction.response.modal)
    view = interaction.response.sent[0]["view"]
    self.assertTrue(view.has_components_v2())
    self.assertEqual(view.children[0].accent_colour, embed_color(EmbedType.ERROR))

  def test_submit_shows_confirm_and_saves_only_after_send(self):
    modal = AnnouncementModal(self.cog, AnnouncementForm(expires_at=NOW + 3600))
    interaction = self.submit(
      modal,
      title="  たいとる ",
      link_url="https://example.com",
      uploads=[FakeAttachment(PNG)],
    )
    # 確認を出しただけでは保存しない
    self.assertEqual(self.service.list_all(), [])
    sent = interaction.followup.sent[0]
    view = sent["view"]
    self.assertIsInstance(view, ConfirmView)
    self.assertTrue(view.has_components_v2())
    self.assertEqual(len(sent["files"]), 1)
    self.assertIn("次のお知らせを追加します", view.children[0].content)
    self.assertIn("## たいとる\nほんぶん", texts(view))
    row = view.children[-1]
    self.assertIsInstance(row, discord.ui.ActionRow)
    self.assertEqual(
      [button.label for button in row.children],
      ["送信する", "内容を変更する", "破棄する"],
    )

    pressed = self.press(view, view.send_button)
    stored = self.service.list_all()[0]
    self.assertEqual(stored.title, "たいとる")
    self.assertEqual(stored.image, PNG)
    self.assertEqual(stored.image_name, "image.png")
    self.assertEqual(stored.expires_at, NOW + 3600)
    edited = pressed.response.edited
    self.assertIn("ID 1 のお知らせを追加しました", edited["view"].children[0].content)
    self.assertEqual(len(edited["attachments"]), 1)
    self.assertFalse(any(isinstance(item, discord.ui.ActionRow) for item in edited["view"].children))
    self.assertTrue(view.is_finished())

    # 2回押しても二重に保存しない
    again = self.press(view, view.send_button)
    self.assertEqual(len(self.service.list_all()), 1)
    self.assertTrue(again.response.sent[0]["ephemeral"])

  def test_discard_does_not_save(self):
    view = self.submit(AnnouncementModal(self.cog, AnnouncementForm())).followup.sent[0]["view"]
    pressed = self.press(view, view.discard_button)
    self.assertEqual(self.service.list_all(), [])
    self.assertIn("## お知らせを破棄しました", "".join(texts(pressed.response.edited["view"])))
    self.assertEqual(pressed.response.edited["attachments"], [])
    self.press(view, view.send_button)
    self.assertEqual(self.service.list_all(), [])

  def test_change_reopens_modal_with_values_and_image(self):
    first = self.submit(
      AnnouncementModal(self.cog, AnnouncementForm(expires_at=NOW + 3600)),
      link_label="みる",
      link_url="https://example.com",
      uploads=[FakeAttachment(PNG)],
    )
    view = first.followup.sent[0]["view"]
    modal = self.press(view, view.change_button).response.modal
    self.assertIsInstance(modal, AnnouncementModal)
    self.assertEqual(modal.title_input.default, "たいとる")
    self.assertEqual(modal.link_label_input.default, "みる")
    self.assertFalse(view.is_finished())

    second = self.submit(modal, title="なおした", link_url="https://example.com")
    # 前の確認はボタンを外し、もう使えない
    old_message = first.followup.messages[0]
    self.assertIn("## 内容を変更しました", "".join(texts(old_message.edits[0]["view"])))
    self.assertTrue(view.is_finished())
    self.press(view, view.send_button)
    self.assertEqual(self.service.list_all(), [])

    new_view = second.followup.sent[0]["view"]
    self.press(new_view, new_view.send_button)
    stored = self.service.list_all()[0]
    self.assertEqual(stored.title, "なおした")
    # 画像は選び直さなくても残る
    self.assertEqual(stored.image, PNG)
    self.assertEqual(stored.expires_at, NOW + 3600)

  def test_confirm_buttons_are_operator_only(self):
    view = self.submit(AnnouncementModal(self.cog, AnnouncementForm())).followup.sent[0]["view"]
    pressed = self.press(view, view.send_button, user_id=DUMMY_OTHER_USER_ID)
    self.assertTrue(pressed.response.sent[0]["ephemeral"])
    self.assertEqual(self.service.list_all(), [])
    self.assertFalse(view.is_finished())

  def test_confirm_timeout_edits_message(self):
    interaction = self.submit(AnnouncementModal(self.cog, AnnouncementForm()))
    view = interaction.followup.sent[0]["view"]
    asyncio.run(view.on_timeout())
    edit = interaction.followup.messages[0].edits[0]
    self.assertIn("/announce add", "".join(texts(edit["view"])))
    self.press(view, view.send_button)
    self.assertEqual(self.service.list_all(), [])

  def test_send_error_after_confirm_offers_retry(self):
    interaction = self.submit(AnnouncementModal(self.cog, AnnouncementForm(expires_at=NOW + 60)))
    view = interaction.followup.sent[0]["view"]
    self.clock.now = NOW + 120
    pressed = self.press(view, view.send_button)
    self.assertEqual(self.service.list_all(), [])
    self.assertIn("すでに過ぎています", "".join(texts(pressed.response.edited["view"])))
    retry_view = pressed.followup.sent[0]["view"]
    self.assertIsInstance(retry_view, RetryView)
    self.assertIn("入力し直してください", "".join(texts(retry_view)))
    self.assertEqual(retry_view.children[-1].children[0].label, "入力し直す")

  def test_submit_error_offers_retry_with_entered_values(self):
    modal = AnnouncementModal(self.cog, AnnouncementForm())
    interaction = self.submit(modal, link_label="みる", uploads=[FakeAttachment(PNG)])
    self.assertEqual(self.service.list_all(), [])
    sent = interaction.followup.sent[0]
    retry_view = sent["view"]
    self.assertIsInstance(retry_view, RetryView)
    self.assertTrue(retry_view.has_components_v2())
    self.assertIn("選び直して", "".join(texts(retry_view)))
    self.assertIsInstance(retry_view.children[-1], discord.ui.ActionRow)
    self.assertEqual(retry_view.form.link_label, "みる")
    retry = FakeInteraction()
    asyncio.run(retry_view.retry.callback(retry))
    self.assertEqual(retry.response.modal.title_input.default, "たいとる")

  def test_too_large_image_is_not_read(self):
    attachment = FakeAttachment(PNG, size=MAX_IMAGE_BYTES + 1)
    interaction = self.submit(AnnouncementModal(self.cog, AnnouncementForm()), uploads=[attachment])
    attachment.read.assert_not_awaited()
    self.assertIn("5MB", "".join(texts(interaction.followup.sent[0]["view"])))

  def test_edit_prefills_and_keeps_or_removes_image(self):
    announcement_id = self.add(
      title="もと", image=PNG, image_name="image.png", expires_at=NOW + 3600,
      link_url="https://example.com", link_label="みる",
    )
    modal = self.run_command("edit", id=announcement_id).response.modal
    self.assertEqual(modal.title_input.default, "もと")
    self.assertEqual(modal.link_label_input.default, "みる")
    view = self.submit(modal, title="なおした", link_url="https://example.com").followup.sent[0]["view"]
    self.assertIn(f"ID {announcement_id} のお知らせを次のように更新します", view.children[0].content)
    self.assertEqual(self.service.get(announcement_id).title, "もと")
    self.press(view, view.send_button)
    stored = self.service.get(announcement_id)
    self.assertEqual(stored.title, "なおした")
    self.assertEqual(stored.image, PNG)
    self.assertEqual(stored.expires_at, NOW + 3600)
    self.assertIsNone(stored.link_label)

    modal = self.run_command("edit", id=announcement_id, expires="none", remove_image=True).response.modal
    self.submit_and_send(modal)
    stored = self.service.get(announcement_id)
    self.assertIsNone(stored.image)
    self.assertIsNone(stored.expires_at)

  def test_edit_missing(self):
    interaction = self.run_command("edit", id=99)
    self.assertIsNone(interaction.response.modal)
    self.assertIn("見つかりません", "".join(texts(interaction.response.sent[0]["view"])))

  def test_list_and_delete(self):
    announcement_id = self.add(title="けす", image=PNG, image_name="image.png")
    view = self.run_command("list").response.sent[0]["view"]
    self.assertTrue(view.has_components_v2())
    listed = texts(view)[-1]
    self.assertIn(f"### ID {announcement_id}｜けす\n", listed)
    self.assertIn("画像あり", listed)
    deleted = self.run_command("del", id=announcement_id).response.sent[0]["view"]
    self.assertIn("## お知らせを削除しました", texts(deleted)[0])
    self.assertIsNone(self.service.get(announcement_id))

  def test_list_fits_components_v2_limits(self):
    for number in range(40):
      self.add(title="た" * MAX_TITLE_LENGTH, body="ほ" * 100)
    view = self.run_command("list").response.sent[0]["view"]
    self.assertLessEqual(sum(len(text) for text in texts(view)), 4000)
    self.assertLessEqual(len(walk(view)), 40)
    self.assertIn("表示しきれませんでした", texts(view)[-1])

  def test_deny_non_operator_with_components_v2(self):
    interaction = self.run_command("list", interaction=FakeInteraction(DUMMY_OTHER_USER_ID))
    sent = interaction.response.sent[0]
    self.assertTrue(sent["ephemeral"])
    self.assertIn("運営者だけ", "".join(texts(sent["view"])))

  def test_preview_current_message(self):
    self.add(title="ひとつめ")
    interaction = self.run_command("preview")
    view = interaction.followup.sent[0]["view"]
    self.assertIn("## ひとつめ\nほんぶん", texts(view))

  def test_autocomplete(self):
    self.add(title="りんご")
    self.add(title="みかん")
    choices = asyncio.run(self.cog.id_autocomplete(FakeInteraction(), "みかん"))
    self.assertEqual([choice.value for choice in choices], [2])


class ConnectionMessageTest(ServiceTestCase, unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    super().setUp()
    self.add(title="おしらせ", image=PNG, image_name="image.png")
    self.voice_service = VoiceService(client=None, error_notifier=RecordingNotifier())
    self.connection = ConnectionService(
      server_config=mock.MagicMock(),
      dict_manager=None,
      speech_service=None,
      voice_service=self.voice_service,
      error_notifier=RecordingNotifier(),
      announcement_service=self.service,
    )
    self.connection._connect_voice = mock.AsyncMock()
    self.attach_files = True
    permissions = SimpleNamespace(
      connect=True, speak=True, view_channel=True, send_messages=True,
    )
    self.guild = SimpleNamespace(id=DUMMY_GUILD_ID, me=object(), voice_client=None)
    self.voice_channel = SimpleNamespace(
      id=DUMMY_VOICE_CHANNEL_ID,
      mention="<#vc>",
      guild=self.guild,
      permissions_for=lambda member: permissions,
    )
    self.text_channel = SimpleNamespace(
      id=DUMMY_TEXT_CHANNEL_ID,
      mention="<#text>",
      send=mock.AsyncMock(),
      permissions_for=lambda member: SimpleNamespace(
        view_channel=True, send_messages=True, attach_files=self.attach_files,
      ),
    )

  def check_message(self, view, title, embed_type):
    self.assertEqual(texts(view)[0], f"## {title}\n<#vc>に接続しました。")
    self.assertEqual(view.children[0].accent_colour, embed_color(embed_type))
    self.assertIn("## おしらせ\nほんぶん", texts(view))

  async def test_mention_join(self):
    message = SimpleNamespace(
      guild=self.guild,
      author=SimpleNamespace(voice=SimpleNamespace(channel=self.voice_channel)),
      channel=self.text_channel,
    )
    await self.connection.handle_mention_toggle(message)
    kwargs = self.text_channel.send.await_args.kwargs
    self.check_message(kwargs["view"], "接続しました", EmbedType.SUCCESS)
    self.assertEqual(len(kwargs["files"]), 1)
    self.assertEqual(
      self.voice_service.get_session(DUMMY_GUILD_ID).temporary_text_channel_id,
      DUMMY_TEXT_CHANNEL_ID,
    )

  async def test_slash_join(self):
    ctx = SimpleNamespace(
      guild=self.guild,
      user=SimpleNamespace(voice=SimpleNamespace(channel=self.voice_channel)),
      channel=self.text_channel,
      response=SimpleNamespace(defer=mock.AsyncMock()),
      edit_original_response=mock.AsyncMock(),
    )
    await self.connection.join(ctx)
    kwargs = ctx.edit_original_response.await_args.kwargs
    self.check_message(kwargs["view"], "接続しました", EmbedType.SUCCESS)
    self.assertEqual(len(kwargs["attachments"]), 1)

  def test_auto_join_title_and_color(self):
    layout = self.connection.connected_message(
      "自動で接続しました", self.voice_channel, self.text_channel, layout_type=EmbedType.INFO,
    )
    self.check_message(layout.view, "自動で接続しました", EmbedType.INFO)

  def test_no_attach_permission_omits_image(self):
    self.attach_files = False
    layout = self.connection.connected_message("接続しました", self.voice_channel, self.text_channel)
    self.assertEqual(layout.files, [])
    self.assertIn("## おしらせ\nほんぶん", texts(layout.view))

  def test_announcement_failure_still_sends_connection_message(self):
    self.connection.announcement_service = mock.MagicMock()
    self.connection.announcement_service.display.side_effect = sqlite3.OperationalError("locked")
    layout = self.connection.connected_message("接続しました", self.voice_channel, self.text_channel)
    self.assertEqual(texts(layout.view), ["## 接続しました\n<#vc>に接続しました。"])
    self.assertEqual(len(self.connection.error_notifier.exceptions), 1)


if __name__ == "__main__":
  unittest.main()
