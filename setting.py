import discord
import json
from messages import build_embed, get_desc, handle_os_error

with open("speakers.json", encoding="utf-8") as _f:
    VOICEVOX_SPEAKERS = [(s["id"], s["name"]) for s in json.load(_f)]


class Setting:
  def __init__(self, client, tree, server_config):
    self.client = client
    self.tree = tree
    self.server_config = server_config
    self._register()

  def _register(self):
    setting_group = discord.app_commands.Group(
      name="setting",
      description=get_desc("commands.setting._group")
    )

    @setting_group.command(
      name="view",
      description=get_desc("commands.setting.view.description")
    )
    async def setting_view(ctx):
      try:
        await ctx.response.defer()
        cfg = self.server_config.get_all(ctx.guild.id)
        embed = build_embed("setting.view")
        not_set = get_desc("setting.view.not_set")
        text_ch  = ctx.guild.get_channel(cfg["TextTarget"])
        voice_ch = ctx.guild.get_channel(cfg["VoiceTarget"])
        embed.add_field(name="TextTarget",
                        value=not_set if text_ch is None else text_ch.mention,
                        inline=False)
        embed.add_field(name="VoiceTarget",
                        value=not_set if voice_ch is None else voice_ch.mention,
                        inline=False)
        embed.add_field(name="Speaker",
                        value=str(cfg["Speaker"]),
                        inline=True)
        embed.add_field(name="Volume",
                        value=str(cfg["Volume"]),
                        inline=True)
        embed.add_field(name="Speed",
                        value=str(cfg["Speed"]),
                        inline=True)
        embed.add_field(name="MaxChar",
                        value=str(cfg["MaxChar"]),
                        inline=True)
        embed.add_field(name="AutoJoin",
                        value=str(cfg["AutoJoin"]),
                        inline=True)
        embed.add_field(name="AccessNotice",
                        value=str(cfg["AccessNotice"]),
                        inline=True)
        await ctx.edit_original_response(embed=embed)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_view: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_view")
      except Exception as e:
        print(f"Exception in setting_view: {e}")

    @setting_group.command(
      name="text-target",
      description=get_desc("commands.setting.text_target.description")
    )
    @discord.app_commands.describe(
      channel=get_desc("commands.setting.text_target.args.channel")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target(ctx, channel: discord.TextChannel = None):
      try:
        await ctx.response.defer()
        target = channel or ctx.channel
        self.server_config.set(ctx.guild.id, "TextTarget", target.id)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.text_target.success",
            target=target.mention
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_text_target: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_text_target")
      except Exception as e:
        print(f"Exception in setting_text_target: {e}")

    @setting_group.command(
      name="text-target-reset",
      description=get_desc("commands.setting.text_target_reset.description")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target_reset(ctx):
      try:
        await ctx.response.defer()
        self.server_config.reset(ctx.guild.id, "TextTarget")
        await ctx.edit_original_response(embed=build_embed("setting.text_target.reset"))
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_text_target_reset: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_text_target_reset")
      except Exception as e:
        print(f"Exception in setting_text_target_reset: {e}")

    @setting_group.command(
      name="voice-target",
      description=get_desc("commands.setting.voice_target.description")
    )
    @discord.app_commands.describe(
      channel=get_desc("commands.setting.voice_target.args.channel")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target(ctx, channel: discord.VoiceChannel = None):
      try:
        await ctx.response.defer()
        if channel is None:
          if ctx.user.voice is None:
            await ctx.edit_original_response(
              embed=build_embed("setting.voice_target.no_vc")
            )
            return
          channel = ctx.user.voice.channel
        self.server_config.set(ctx.guild.id, "VoiceTarget", channel.id)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.voice_target.success",
            channel=channel.mention
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_voice_target: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_voice_target")
      except Exception as e:
        print(f"Exception in setting_voice_target: {e}")

    @setting_group.command(
      name="voice-target-reset",
      description=get_desc("commands.setting.voice_target_reset.description")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target_reset(ctx):
      try:
        await ctx.response.defer()
        self.server_config.reset(ctx.guild.id, "VoiceTarget")
        await ctx.edit_original_response(embed=build_embed("setting.voice_target.reset"))
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_voice_target_reset: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_voice_target_reset")
      except Exception as e:
        print(f"Exception in setting_voice_target_reset: {e}")

    @setting_group.command(
      name="speaker",
      description=get_desc("commands.setting.speaker.description")
    )
    @discord.app_commands.describe(
      speaker=get_desc("commands.setting.speaker.args.speaker")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speaker(ctx, speaker: str):
      try:
        await ctx.response.defer()
        speaker_id = next(
          (sid for sid, name in VOICEVOX_SPEAKERS if name == speaker), None
        )
        if speaker_id is None:
          await ctx.edit_original_response(
            embed=build_embed("setting.speaker.not_found")
          )
          return
        self.server_config.set(ctx.guild.id, "Speaker", speaker_id)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.speaker.success",
            speaker=speaker,
            speaker_id=speaker_id
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_speaker: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_speaker")
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
      description=get_desc("commands.setting.volume.description")
    )
    @discord.app_commands.describe(
      volume=get_desc("commands.setting.volume.args.volume")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_volume(ctx, volume: int):
      try:
        await ctx.response.defer()
        try:
          self.server_config.set(ctx.guild.id, "Volume", volume)
        except ValueError:
          await ctx.edit_original_response(
            embed=build_embed("setting.volume.invalid")
          )
          return
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.volume.success",
            volume=volume
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_volume: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_volume")
      except Exception as e:
        print(f"Exception in setting_volume: {e}")

    @setting_group.command(
      name="speed",
      description=get_desc("commands.setting.speed.description")
    )
    @discord.app_commands.describe(
      speed=get_desc("commands.setting.speed.args.speed")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speed(ctx, speed: int):
      try:
        await ctx.response.defer()
        try:
          self.server_config.set(ctx.guild.id, "Speed", speed)
        except ValueError:
          await ctx.edit_original_response(
            embed=build_embed("setting.speed.invalid")
          )
          return
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.speed.success",
            speed=speed
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_speed: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_speed")
      except Exception as e:
        print(f"Exception in setting_speed: {e}")

    @setting_group.command(
      name="max-char",
      description=get_desc("commands.setting.max_char.description")
    )
    @discord.app_commands.describe(
      chars=get_desc("commands.setting.max_char.args.chars")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_max_char(ctx, chars: int):
      try:
        await ctx.response.defer()
        try:
          self.server_config.set(ctx.guild.id, "MaxChar", chars)
        except ValueError:
          await ctx.edit_original_response(
            embed=build_embed("setting.max_char.invalid")
          )
          return
        limit = "無制限" if chars == 0 else f"{chars}文字"
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.max_char.success",
            chars=chars,
            limit=limit
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_max_char: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_max_char")
      except Exception as e:
        print(f"Exception in setting_max_char: {e}")

    @setting_group.command(
      name="auto-join",
      description=get_desc("commands.setting.auto_join.description")
    )
    @discord.app_commands.describe(
      enabled=get_desc("commands.setting.auto_join.args.enabled")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_auto_join(ctx, enabled: bool):
      try:
        await ctx.response.defer()
        self.server_config.set(ctx.guild.id, "AutoJoin", enabled)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.auto_join.success",
            state="有効" if enabled else "無効"
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_auto_join: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_auto_join")
      except Exception as e:
        print(f"Exception in setting_auto_join: {e}")

    @setting_group.command(
      name="access-notice",
      description=get_desc("commands.setting.access_notice.description")
    )
    @discord.app_commands.describe(
      enabled=get_desc("commands.setting.access_notice.args.enabled")
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_access_notice(ctx, enabled: bool):
      try:
        await ctx.response.defer()
        self.server_config.set(ctx.guild.id, "AccessNotice", enabled)
        await ctx.edit_original_response(
          embed=build_embed(
            "setting.access_notice.success",
            state="有効" if enabled else "無効"
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in setting_access_notice: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_access_notice")
      except Exception as e:
        print(f"Exception in setting_access_notice: {e}")

    @setting_group.error
    async def setting_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=build_embed("setting.error.no_permission"),
          ephemeral=True
        )

    self.tree.add_command(setting_group)