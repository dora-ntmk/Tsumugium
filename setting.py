import discord
import json
from messages import build_embed, get_desc

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

    @setting_group.command(name="view", description=get_desc("commands.setting.view"))
    async def setting_view(ctx):
      await ctx.response.defer()
      cfg = self.server_config.get_all(ctx.guild.id)
      embed = build_embed("setting.view")
      text_ch  = ctx.guild.get_channel(cfg["TextTarget"])
      voice_ch = ctx.guild.get_channel(cfg["VoiceTarget"])
      embed.add_field(name="TextTarget",  value=text_ch.mention  if text_ch  is not None else "未設定", inline=False)
      embed.add_field(name="VoiceTarget", value=voice_ch.mention if voice_ch is not None else "未設定", inline=False)
      embed.add_field(name="Speaker",     value=str(cfg["Speaker"]),    inline=True)
      embed.add_field(name="Volume",      value=str(cfg["Volume"]),     inline=True)
      embed.add_field(name="MaxChar",     value=str(cfg["MaxChar"]),    inline=True)
      embed.add_field(name="AutoJoin",    value=str(cfg["AutoJoin"]),   inline=True)
      embed.add_field(name="JoinNotice",  value=str(cfg["JoinNotice"]), inline=True)
      await ctx.edit_original_response(embed=embed)

    @setting_group.command(name="text-target", description=get_desc("commands.setting.text_target"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target(ctx, channel: discord.TextChannel = None):
      await ctx.response.defer()
      target = channel or ctx.channel
      self.server_config.set(ctx.guild.id, "TextTarget", target.id)
      await ctx.edit_original_response(embed=build_embed("setting.text_target.success", target=target.mention))

    @setting_group.command(name="voice-target", description=get_desc("commands.setting.voice_target"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target(ctx, channel: discord.VoiceChannel = None):
      await ctx.response.defer()
      if channel is None:
        if ctx.user.voice is None:
          await ctx.edit_original_response(embed=build_embed("setting.voice_target.no_vc"))
          return
        channel = ctx.user.voice.channel
      self.server_config.set(ctx.guild.id, "VoiceTarget", channel.id)
      await ctx.edit_original_response(embed=build_embed("setting.voice_target.success", channel=channel.mention))

    @setting_group.command(name="speaker", description=get_desc("commands.setting.speaker"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speaker(ctx, speaker: str):
      await ctx.response.defer()
      speaker_id = next((sid for sid, name in VOICEVOX_SPEAKERS if name == speaker), None)
      if speaker_id is None:
        await ctx.edit_original_response(embed=build_embed("setting.speaker.not_found"))
        return
      self.server_config.set(ctx.guild.id, "Speaker", speaker_id)
      await ctx.edit_original_response(embed=build_embed("setting.speaker.success", speaker=speaker, speaker_id=speaker_id))

    # noinspection PyUnusedLocal
    @setting_speaker.autocomplete("speaker")
    async def speaker_autocomplete(ctx, current: str):
      filtered = [
        discord.app_commands.Choice(name=name, value=name)
        for _, name in VOICEVOX_SPEAKERS
        if current in name
      ]
      return filtered[:25]

    @setting_group.command(name="volume", description=get_desc("commands.setting.volume"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_volume(ctx, volume: int):
      await ctx.response.defer()
      try:
        self.server_config.set(ctx.guild.id, "Volume", volume)
      except ValueError:
        await ctx.edit_original_response(embed=build_embed("setting.volume.invalid"))
        return
      await ctx.edit_original_response(embed=build_embed("setting.volume.success", volume=volume))

    @setting_group.command(name="max-char", description=get_desc("commands.setting.max_char"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_max_char(ctx, chars: int):
      await ctx.response.defer()
      try:
        self.server_config.set(ctx.guild.id, "MaxChar", chars)
      except ValueError:
        await ctx.edit_original_response(embed=build_embed("setting.max_char.invalid"))
        return
      limit = "無制限" if chars == 0 else f"{chars}文字"
      await ctx.edit_original_response(embed=build_embed("setting.max_char.success", chars=chars, limit=limit))

    @setting_group.command(name="auto-join", description=get_desc("commands.setting.auto_join"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_auto_join(ctx, enabled: bool):
      await ctx.response.defer()
      self.server_config.set(ctx.guild.id, "AutoJoin", enabled)
      await ctx.edit_original_response(embed=build_embed("setting.auto_join.success", state="有効" if enabled else "無効"))

    @setting_group.command(name="join-notice", description=get_desc("commands.setting.join_notice"))
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_join_notice(ctx, enabled: bool):
      await ctx.response.defer()
      self.server_config.set(ctx.guild.id, "JoinNotice", enabled)
      await ctx.edit_original_response(embed=build_embed("setting.join_notice.success", state="有効" if enabled else "無効"))

    @setting_group.error
    async def setting_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=build_embed("setting.error.no_permission"),
          ephemeral=True
        )

    self.tree.add_command(setting_group)