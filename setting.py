import discord
import json
from messages import build_embed, get_desc, handle_os_error
from config import SPEAKERS_JSON

with open(SPEAKERS_JSON, encoding="utf-8") as _f:
    VOICEVOX_SPEAKERS = [(s["id"], s["name"]) for s in json.load(_f)]


def _lstr(key: str) -> discord.app_commands.locale_str:
    return discord.app_commands.locale_str(get_desc(key), key=key)


class Setting:
  def __init__(self, client, tree, server_config):
    self.client = client
    self.tree = tree
    self.server_config = server_config
    self._register()

  def _register(self):
    setting_group = discord.app_commands.Group(
      name="setting",
      description=_lstr("commands.setting._group")
    )

    @setting_group.command(
      name="view",
      description=_lstr("commands.setting.view.description")
    )
    async def setting_view(ctx):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        cfg = self.server_config.get_all(ctx.guild.id)
        embed = build_embed("setting.view", lang=lang)
        not_set = get_desc("setting.view.not_set", lang=lang)
        lbl = lambda k: get_desc(f"setting.view.labels.{k}", lang=lang)
        text_ch  = ctx.guild.get_channel(cfg["TextTarget"])
        voice_ch = ctx.guild.get_channel(cfg["VoiceTarget"])
        embed.add_field(name=lbl("TextTarget"),
                        value=not_set if text_ch is None else text_ch.mention,
                        inline=False)
        embed.add_field(name=lbl("VoiceTarget"),
                        value=not_set if voice_ch is None else voice_ch.mention,
                        inline=False)
        embed.add_field(name=lbl("Speaker"),
                        value=str(cfg["Speaker"]),
                        inline=True)
        embed.add_field(name=lbl("Volume"),
                        value=str(cfg["Volume"]),
                        inline=True)
        embed.add_field(name=lbl("Speed"),
                        value=str(cfg["Speed"]),
                        inline=True)
        embed.add_field(name=lbl("MaxChar"),
                        value=str(cfg["MaxChar"]),
                        inline=True)
        embed.add_field(name=lbl("AutoJoin"),
                        value=str(cfg["AutoJoin"]),
                        inline=True)
        embed.add_field(name=lbl("AccessNotice"),
                        value=str(cfg["AccessNotice"]),
                        inline=True)
        embed.add_field(name=lbl("Language"),
                        value=str(cfg["Language"]),
                        inline=True)
        await ctx.edit_original_response(embed=embed)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_view: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_view", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_view: {e}")

    @setting_group.command(
      name="text-target",
      description=_lstr("commands.setting.text_target.description")
    )
    @discord.app_commands.describe(
      channel=_lstr("commands.setting.text_target.args.channel")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target(ctx, channel: discord.TextChannel = None):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        target = channel or ctx.channel
        perms = target.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages):
          await ctx.edit_original_response(
            embed=build_embed("setting.text_target.no_permission", lang=lang, channel=target.mention)
          )
          return
        self.server_config.set(ctx.guild.id, "TextTarget", target.id)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.text_target.success",
            lang=lang,
            target=target.mention
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_text_target: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_text_target", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_text_target: {e}")

    @setting_group.command(
      name="text-target-reset",
      description=_lstr("commands.setting.text_target_reset.description")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target_reset(ctx):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        self.server_config.reset(ctx.guild.id, "TextTarget")
        await ctx.edit_original_response(embed=build_embed("setting.text_target.reset", lang=lang))
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_text_target_reset: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_text_target_reset", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_text_target_reset: {e}")

    @setting_group.command(
      name="voice-target",
      description=_lstr("commands.setting.voice_target.description")
    )
    @discord.app_commands.describe(
      channel=_lstr("commands.setting.voice_target.args.channel")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target(ctx, channel: discord.VoiceChannel = None):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        if channel is None:
          if ctx.user.voice is None:
            await ctx.edit_original_response(
              embed=build_embed("setting.voice_target.no_vc", lang=lang)
            )
            return
          channel = ctx.user.voice.channel
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.connect and perms.speak):
          await ctx.edit_original_response(
            embed=build_embed("setting.voice_target.no_permission", lang=lang, channel=channel.mention)
          )
          return
        self.server_config.set(ctx.guild.id, "VoiceTarget", channel.id)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.voice_target.success",
            lang=lang,
            channel=channel.mention
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_voice_target: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_voice_target", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_voice_target: {e}")

    @setting_group.command(
      name="voice-target-reset",
      description=_lstr("commands.setting.voice_target_reset.description")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target_reset(ctx):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        self.server_config.reset(ctx.guild.id, "VoiceTarget")
        await ctx.edit_original_response(embed=build_embed("setting.voice_target.reset", lang=lang))
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_voice_target_reset: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_voice_target_reset", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_voice_target_reset: {e}")

    @setting_group.command(
      name="speaker",
      description=_lstr("commands.setting.speaker.description")
    )
    @discord.app_commands.describe(
      speaker=_lstr("commands.setting.speaker.args.speaker")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speaker(ctx, speaker: str):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        speaker_id = next(
          (sid for sid, name in VOICEVOX_SPEAKERS if name == speaker), None
        )
        if speaker_id is None:
          await ctx.edit_original_response(
            embed=build_embed("setting.speaker.not_found", lang=lang)
          )
          return
        self.server_config.set(ctx.guild.id, "Speaker", speaker_id)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.speaker.success",
            lang=lang,
            speaker=speaker,
            speaker_id=speaker_id
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_speaker: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_speaker", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_speaker: {e}")

    # noinspection PyUnusedLocal
    @setting_speaker.autocomplete("speaker")
    async def speaker_autocomplete(ctx, current: str):
      filtered = [
        discord.app_commands.Choice(name=name, value=name)
        for _, name in VOICEVOX_SPEAKERS
        if current in name
      ]
      return filtered[:25]

    @setting_group.command(
      name="volume",
      description=_lstr("commands.setting.volume.description")
    )
    @discord.app_commands.describe(
      volume=_lstr("commands.setting.volume.args.volume")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_volume(ctx, volume: int):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        try:
          self.server_config.set(ctx.guild.id, "Volume", volume)
        except ValueError:
          await ctx.edit_original_response(
            embed=build_embed("setting.volume.invalid", lang=lang)
          )
          return
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.volume.success",
            lang=lang,
            volume=volume
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_volume: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_volume", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_volume: {e}")

    @setting_group.command(
      name="speed",
      description=_lstr("commands.setting.speed.description")
    )
    @discord.app_commands.describe(
      speed=_lstr("commands.setting.speed.args.speed")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speed(ctx, speed: int):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        try:
          self.server_config.set(ctx.guild.id, "Speed", speed)
        except ValueError:
          await ctx.edit_original_response(
            embed=build_embed("setting.speed.invalid", lang=lang)
          )
          return
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.speed.success",
            lang=lang,
            speed=speed
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_speed: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_speed", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_speed: {e}")

    @setting_group.command(
      name="max-char",
      description=_lstr("commands.setting.max_char.description")
    )
    @discord.app_commands.describe(
      chars=_lstr("commands.setting.max_char.args.chars")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_max_char(ctx, chars: int):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        if chars == 0:
          chars = 50
        try:
          self.server_config.set(ctx.guild.id, "MaxChar", chars)
        except ValueError:
          await ctx.edit_original_response(
            embed=build_embed("setting.max_char.invalid", lang=lang)
          )
          return
        limit = get_desc("setting.max_char.limited", lang=lang).format(chars=chars)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.max_char.success",
            lang=lang,
            chars=chars,
            limit=limit
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_max_char: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_max_char", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_max_char: {e}")

    @setting_group.command(
      name="auto-join",
      description=_lstr("commands.setting.auto_join.description")
    )
    @discord.app_commands.describe(
      enabled=_lstr("commands.setting.auto_join.args.enabled")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_auto_join(ctx, enabled: bool):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        self.server_config.set(ctx.guild.id, "AutoJoin", enabled)
        state_key = "setting.states.enabled" if enabled else "setting.states.disabled"
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.auto_join.success",
            lang=lang,
            state=get_desc(state_key, lang=lang)
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_auto_join: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_auto_join", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_auto_join: {e}")

    @setting_group.command(
      name="access-notice",
      description=_lstr("commands.setting.access_notice.description")
    )
    @discord.app_commands.describe(
      enabled=_lstr("commands.setting.access_notice.args.enabled")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_access_notice(ctx, enabled: bool):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        self.server_config.set(ctx.guild.id, "AccessNotice", enabled)
        state_key = "setting.states.enabled" if enabled else "setting.states.disabled"
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.access_notice.success",
            lang=lang,
            state=get_desc(state_key, lang=lang)
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_access_notice: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_access_notice", lang=self.server_config.get(ctx.guild.id, "Language"))
      except Exception as e:
        print(f"Exception in setting_access_notice: {e}")

    @setting_group.command(
      name="language",
      description=_lstr("commands.setting.language.description")
    )
    @discord.app_commands.describe(
      language=_lstr("commands.setting.language.args.language")
    )
    @discord.app_commands.choices(language=[
      discord.app_commands.Choice(name="日本語",   value="ja"),
      discord.app_commands.Choice(name="English",  value="en"),
      discord.app_commands.Choice(name="简体中文", value="zh-CN"),
      discord.app_commands.Choice(name="繁體中文", value="zh-TW"),
      discord.app_commands.Choice(name="한국어",   value="ko"),
      discord.app_commands.Choice(name="𓂀 Hieroglyphs", value="hg"),
    ])
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_language(ctx, language: str):
      try:
        await ctx.response.defer()
        self.server_config.set(ctx.guild.id, "Language", language)
        await ctx.edit_original_response(
          embed=build_embed("setting.language.success", lang=language, language=language)
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_language: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_language", lang=language)
      except Exception as e:
        print(f"Exception in setting_language: {e}")

    @setting_group.error
    async def setting_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=build_embed("setting.error.no_permission"),
          ephemeral=True
        )

    self.tree.add_command(setting_group)
