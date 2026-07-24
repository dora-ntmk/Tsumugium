"""
ファイル名：setting.py
作者：どら
説明：サーバー設定コマンドモジュール。
      スラッシュコマンドグループ /setting を実装する。
      テキスト/ボイスチャンネルの設定、話者・音量・速度・最大文字数などの数値設定、
      AutoJoin・AccessNotice・言語などの状態設定をサーバー管理者向けに提供する。
依存関係：discord.py
"""

import discord
import json
from messages import build_embed, get_desc, handle_os_error, handle_internal_error
from config import SPEAKERS_JSON
from server_config import ServerConfig

with open(SPEAKERS_JSON, encoding="utf-8") as _f:
  VOICEVOX_SPEAKERS = [(s["id"], s["name"]) for s in json.load(_f)]

BOT_DEFAULT_LABEL = "Botのデフォルト"


def _lstr(key: str) -> discord.app_commands.locale_str:
  return discord.app_commands.locale_str(get_desc(key), key=key)


class Setting:
  def __init__(self, client: discord.Client, tree: discord.app_commands.CommandTree, server_config: ServerConfig) -> None:
    self.client = client
    self.tree = tree
    self.server_config = server_config
    self._register()

  def _register(self) -> None:
    setting_group = discord.app_commands.Group(name="setting", description=_lstr("commands.setting._group"), default_permissions=discord.Permissions(manage_guild=True))

    @setting_group.command(name="view", description=_lstr("commands.setting.view.description"))
    async def setting_view(ctx: discord.Interaction) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      cfg = self.server_config.get_all(ctx.guild.id)
      embed = build_embed("setting.view", lang=lang)
      def __lbl(k: str) -> str:
        return get_desc(f"setting.view.labels.{k}", lang=lang)
      not_set = get_desc("setting.view.not_set", lang=lang)
      text_ch = ctx.guild.get_channel(cfg["TextTarget"])
      voice_ch = ctx.guild.get_channel(cfg["VoiceTarget"])
      embed.add_field(name=_lbl("TextTarget"), value=not_set if text_ch is None else text_ch.mention, inline=False)
      embed.add_field(name=_lbl("VoiceTarget"), value=not_set if voice_ch is None else voice_ch.mention, inline=False)
      raw_speaker = self.server_config.get_raw_speaker(ctx.guild.id)
      if raw_speaker is None:
        speaker_display = BOT_DEFAULT_LABEL
      else:
        speaker_display = next((name for sid, name in VOICEVOX_SPEAKERS if sid == raw_speaker), str(raw_speaker))
      embed.add_field(name=_lbl("Speaker"), value=speaker_display, inline=True)
      embed.add_field(name=_lbl("Volume"), value=str(cfg["Volume"]), inline=True)
      embed.add_field(name=_lbl("Speed"), value=str(cfg["Speed"]), inline=True)
      embed.add_field(name=_lbl("MaxChar"), value=str(cfg["MaxChar"]), inline=True)
      embed.add_field(name=_lbl("AutoJoin"), value=str(cfg["AutoJoin"]), inline=True)
      embed.add_field(name=_lbl("AccessNotice"), value=str(cfg["AccessNotice"]), inline=True)
      embed.add_field(name=_lbl("Language"), value=str(cfg["Language"]), inline=True)
      embed.add_field(name=_lbl("AutoUpdate"), value=str(cfg["AutoUpdate"]), inline=True)
      embed.add_field(name=_lbl("AutoUpdateCheck"), value=str(cfg["AutoUpdateCheck"]), inline=True)
      await ctx.edit_original_response(embed=embed)

    @setting_group.command(name="text-target", description=_lstr("commands.setting.text_target.description"))
    @discord.app_commands.describe(channel=_lstr("commands.setting.text_target.args.channel"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target(ctx: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      target = channel or ctx.channel
      perms = target.permissions_for(ctx.guild.me)
      if not (perms.view_channel and perms.send_messages):
        await ctx.edit_original_response(embed=build_embed("setting.text_target.no_permission", lang=lang, channel=target.mention))
        return
      self.server_config.set(ctx.guild.id, "TextTarget", target.id)
      await ctx.edit_original_response(embed=build_embed("setting.text_target.success", lang=lang, target=target.mention))

    @setting_group.command(name="text-target-reset", description=_lstr("commands.setting.text_target_reset.description"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target_reset(ctx: discord.Interaction) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      self.server_config.reset(ctx.guild.id, "TextTarget")
      await ctx.edit_original_response(embed=build_embed("setting.text_target.reset", lang=lang))

    @setting_group.command(name="voice-target", description=_lstr("commands.setting.voice_target.description"))
    @discord.app_commands.describe(channel=_lstr("commands.setting.voice_target.args.channel"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target(ctx: discord.Interaction, channel: discord.VoiceChannel | None = None) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      if channel is None:
        if ctx.user.voice is None:
          await ctx.edit_original_response(embed=build_embed("setting.voice_target.no_vc", lang=lang))
          return
        channel = ctx.user.voice.channel
      perms = channel.permissions_for(ctx.guild.me)
      if not (perms.connect and perms.speak):
        await ctx.edit_original_response(embed=build_embed("setting.voice_target.no_permission", lang=lang, channel=channel.mention))
        return
      self.server_config.set(ctx.guild.id, "VoiceTarget", channel.id)
      await ctx.edit_original_response(embed=build_embed("setting.voice_target.success", lang=lang, channel=channel.mention))

    @setting_group.command(name="voice-target-reset", description=_lstr("commands.setting.voice_target_reset.description"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target_reset(ctx: discord.Interaction) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      self.server_config.reset(ctx.guild.id, "VoiceTarget")
      await ctx.edit_original_response(embed=build_embed("setting.voice_target.reset", lang=lang))

    @setting_group.command(name="speaker", description=_lstr("commands.setting.speaker.description"))
    @discord.app_commands.describe(speaker=_lstr("commands.setting.speaker.args.speaker"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speaker(ctx: discord.Interaction, speaker: str) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      if speaker == BOT_DEFAULT_LABEL:
        self.server_config.set(ctx.guild.id, "Speaker", None)
        await ctx.edit_original_response(embed=build_embed("setting.speaker.default", lang=lang))
        return
      speaker_id = next((sid for sid, name in VOICEVOX_SPEAKERS if name == speaker), None)
      if speaker_id is None:
        await ctx.edit_original_response(embed=build_embed("setting.speaker.not_found", lang=lang))
        return
      self.server_config.set(ctx.guild.id, "Speaker", speaker_id)
      await ctx.edit_original_response(embed=build_embed("setting.speaker.success", lang=lang, speaker=speaker, speaker_id=speaker_id))

    # noinspection PyUnusedLocal
    @setting_speaker.autocomplete("speaker")
    async def speaker_autocomplete(ctx: discord.Interaction, current: str) -> list[discord.app_commands.Choice]:
      choices = []
      if not current or current in BOT_DEFAULT_LABEL:
        choices.append(discord.app_commands.Choice(name=BOT_DEFAULT_LABEL, value=BOT_DEFAULT_LABEL))
      filtered = [discord.app_commands.Choice(name=name, value=name) for _, name in VOICEVOX_SPEAKERS if current in name]
      return (choices + filtered)[:25]

    @setting_group.command(name="volume", description=_lstr("commands.setting.volume.description"))
    @discord.app_commands.describe(volume=_lstr("commands.setting.volume.args.volume"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_volume(ctx: discord.Interaction, volume: int) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      try:
        self.server_config.set(ctx.guild.id, "Volume", volume)
      except ValueError:
        await ctx.edit_original_response(embed=build_embed("setting.volume.invalid", lang=lang))
        return
      await ctx.edit_original_response(embed=build_embed("setting.volume.success", lang=lang, volume=volume))

    @setting_group.command(name="speed", description=_lstr("commands.setting.speed.description"))
    @discord.app_commands.describe(speed=_lstr("commands.setting.speed.args.speed"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speed(ctx: discord.Interaction, speed: int) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      try:
        self.server_config.set(ctx.guild.id, "Speed", speed)
      except ValueError:
        await ctx.edit_original_response(embed=build_embed("setting.speed.invalid", lang=lang))
        return
      await ctx.edit_original_response(embed=build_embed("setting.speed.success", lang=lang, speed=speed))

    @setting_group.command(name="max-char", description=_lstr("commands.setting.max_char.description"))
    @discord.app_commands.describe(chars=_lstr("commands.setting.max_char.args.chars"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_max_char(ctx: discord.Interaction, chars: int) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      if chars == 0:
        chars = 50
      try:
        self.server_config.set(ctx.guild.id, "MaxChar", chars)
      except ValueError:
        await ctx.edit_original_response(embed=build_embed("setting.max_char.invalid", lang=lang))
        return
      limit = get_desc("setting.max_char.limited", lang=lang).format(chars=chars)
      await ctx.edit_original_response(embed=build_embed("setting.max_char.success", lang=lang, chars=chars, limit=limit))

    @setting_group.command(name="auto-join", description=_lstr("commands.setting.auto_join.description"))
    @discord.app_commands.describe(enabled=_lstr("commands.setting.auto_join.args.enabled"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_auto_join(ctx: discord.Interaction, enabled: bool) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      self.server_config.set(ctx.guild.id, "AutoJoin", enabled)
      state_key = "setting.states.enabled" if enabled else "setting.states.disabled"
      await ctx.edit_original_response(embed=build_embed("setting.auto_join.success", lang=lang, state=get_desc(state_key, lang=lang)))

    @setting_group.command(name="access-notice", description=_lstr("commands.setting.access_notice.description"))
    @discord.app_commands.describe(enabled=_lstr("commands.setting.access_notice.args.enabled"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_access_notice(ctx: discord.Interaction, enabled: bool) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      self.server_config.set(ctx.guild.id, "AccessNotice", enabled)
      state_key = "setting.states.enabled" if enabled else "setting.states.disabled"
      await ctx.edit_original_response(embed=build_embed("setting.access_notice.success", lang=lang, state=get_desc(state_key, lang=lang)))

    @setting_group.command(name="language", description=_lstr("commands.setting.language.description"))
    @discord.app_commands.describe(language=_lstr("commands.setting.language.args.language"))
    @discord.app_commands.choices(
      language=[
        discord.app_commands.Choice(name="日本語", value="ja"),
        discord.app_commands.Choice(name="English", value="en"),
        discord.app_commands.Choice(name="简体中文", value="zh-CN"),
        discord.app_commands.Choice(name="繁體中文", value="zh-TW"),
        discord.app_commands.Choice(name="한국어", value="ko"),
        discord.app_commands.Choice(name="𓂀 Hieroglyphs", value="hg"),
      ]
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_language(ctx: discord.Interaction, language: str) -> None:
      await ctx.response.defer()
      self.server_config.set(ctx.guild.id, "Language", language)
      await ctx.edit_original_response(embed=build_embed("setting.language.success", lang=language, language=language))

    @setting_group.command(name="auto-update", description=_lstr("commands.setting.auto_update.description"))
    @discord.app_commands.describe(enabled=_lstr("commands.setting.auto_update.args.enabled"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_auto_update(ctx: discord.Interaction, enabled: bool) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      self.server_config.set(ctx.guild.id, "AutoUpdate", enabled)
      state_key = "setting.states.enabled" if enabled else "setting.states.disabled"
      await ctx.edit_original_response(embed=build_embed("setting.auto_update.success", lang=lang, state=get_desc(state_key, lang=lang)))

    @setting_group.command(name="auto-update-check", description=_lstr("commands.setting.auto_update_check.description"))
    @discord.app_commands.describe(enabled=_lstr("commands.setting.auto_update_check.args.enabled"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_auto_update_check(ctx: discord.Interaction, enabled: bool) -> None:
      await ctx.response.defer()
      lang = self.server_config.get(ctx.guild.id, "Language")
      self.server_config.set(ctx.guild.id, "AutoUpdateCheck", enabled)
      state_key = "setting.states.enabled" if enabled else "setting.states.disabled"
      await ctx.edit_original_response(embed=build_embed("setting.auto_update_check.success", lang=lang, state=get_desc(state_key, lang=lang)))

    @setting_group.error
    async def setting_error(ctx: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(embed=build_embed("setting.error.no_permission"), ephemeral=True)

    self.tree.add_command(setting_group)
