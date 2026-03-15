import asyncio
import discord
import json
import os
from collections import defaultdict

with open("speakers.json", encoding="utf-8") as _f:
    VOICEVOX_SPEAKERS = [(s["id"], s["name"]) for s in json.load(_f)]


class Play:
  def __init__(self, client, tree, vvtts, server_config):
    self.client = client
    self.tree = tree
    self.vvtts = vvtts
    self.server_config = server_config
    self.voice_queues = defaultdict(asyncio.Queue)
    self.playing_tasks = {}
    self.skip_flags = defaultdict(bool)
    self._register()

  def _register(self):

    # キュークリア
    @self.tree.command(
      name="clear",
      description="読み上げキューをすべてクリアします。"
    )
    async def clear(ctx, instant: bool = True):
      await ctx.response.defer()
      queue = self.voice_queues[ctx.guild.id]
      cleared = queue.qsize()
      print(cleared)
      while not queue.empty():
        try:
          queue.get_nowait()
          queue.task_done()
        except asyncio.QueueEmpty:
          break
      self.skip_flags[ctx.guild.id] = True
      if instant and ctx.guild.voice_client and ctx.guild.voice_client.is_playing():
        ctx.guild.voice_client.stop()
      embed = discord.Embed(
        title="キュークリア完了",
        description=f"{cleared}件の読み上げをキャンセルしました",
        color=discord.Color.green()
      )
      await ctx.edit_original_response(embed=embed)

    # メッセージ検出
    @self.client.event
    async def on_message(message):
      if message.author.bot:
        return
      if message.guild.voice_client is None:
        return
      text_target = self.server_config.get(message.guild.id, "TextTarget")
      if text_target is not None:
        if message.channel.id != text_target:
          return
      else:
        if message.guild.voice_client.channel.id != message.channel.id:
          return
      if message.content.startswith("!s ") or message.flags.silent:
        return
      asyncio.create_task(self.add_to_queue(message))

    # サーバー参加時にデフォルト設定を書き込む
    @self.client.event
    async def on_guild_join(guild):
      self.server_config.init_guild(guild.id)

    # VC入退室検知（AutoJoin / JoinNotice）
    @self.client.event
    async def on_voice_state_update(member, before, after):
      if member.bot:
        return
      guild = member.guild
      user_joined = before.channel is None and after.channel is not None
      if not user_joined:
        return

      # AutoJoin
      if self.server_config.get(guild.id, "AutoJoin") and guild.voice_client is None:
        voice_target = self.server_config.get(guild.id, "VoiceTarget")
        target_channel = guild.get_channel(voice_target) if voice_target is not None else after.channel
        if target_channel is not None:
          await target_channel.connect(timeout=60, self_deaf=True)

      # JoinNotice
      if self.server_config.get(guild.id, "JoinNotice") and guild.voice_client is not None:
        notice_text = f"{member.display_name}が入室しました"
        speaker = self.server_config.get(guild.id, "Speaker")
        volume = self.server_config.volume_to_vvtts(guild.id)
        src = await self.generate(notice_text, guild.id, member.id, speaker, volume=volume)
        if src is not None:
          await self.voice_queues[guild.id].put((guild.id, src))
          if guild.id not in self.playing_tasks or self.playing_tasks[guild.id].done():
            self.playing_tasks[guild.id] = asyncio.create_task(self.play_loop(guild))

    # 設定コマンドグループ
    setting_group = discord.app_commands.Group(
      name="setting",
      description="サーバー設定を管理します"
    )

    @setting_group.command(name="view", description="現在の設定を表示します")
    async def setting_view(ctx):
      await ctx.response.defer()
      cfg = self.server_config.get_all(ctx.guild.id)
      embed = discord.Embed(title="サーバー設定", color=discord.Color.blue())
      embed.add_field(name="TextTarget",  value=str(cfg["TextTarget"])  if cfg["TextTarget"]  is not None else "未設定", inline=False)
      embed.add_field(name="VoiceTarget", value=str(cfg["VoiceTarget"]) if cfg["VoiceTarget"] is not None else "未設定", inline=False)
      embed.add_field(name="Speaker",     value=str(cfg["Speaker"]),    inline=True)
      embed.add_field(name="Volume",      value=str(cfg["Volume"]),     inline=True)
      embed.add_field(name="MaxChar",     value=str(cfg["MaxChar"]),    inline=True)
      embed.add_field(name="AutoJoin",    value=str(cfg["AutoJoin"]),   inline=True)
      embed.add_field(name="JoinNotice",  value=str(cfg["JoinNotice"]), inline=True)
      await ctx.edit_original_response(embed=embed)

    @setting_group.command(name="text-target", description="読み上げ対象のテキストチャンネルを設定します（省略で現在のチャンネル）")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target(ctx, channel: discord.TextChannel = None):
      await ctx.response.defer()
      target = channel or ctx.channel
      self.server_config.set(ctx.guild.id, "TextTarget", target.id)
      embed = discord.Embed(
        title="設定完了",
        description=f"TextTarget を {target.mention} に設定しました",
        color=discord.Color.green()
      )
      await ctx.edit_original_response(embed=embed)

    @setting_group.command(name="voice-target", description="自動接続先VCを設定します（省略で現在入室中のVC）")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target(ctx, channel: discord.VoiceChannel = None):
      await ctx.response.defer()
      if channel is None:
        if ctx.user.voice is None:
          embed = discord.Embed(
            title="設定失敗",
            description="VCに入室していません。チャンネルを指定するか、VCに入室してから実行してください",
            color=discord.Color.red()
          )
          await ctx.edit_original_response(embed=embed)
          return
        channel = ctx.user.voice.channel
      self.server_config.set(ctx.guild.id, "VoiceTarget", channel.id)
      embed = discord.Embed(
        title="設定完了",
        description=f"VoiceTarget を {channel.mention} に設定しました",
        color=discord.Color.green()
      )
      await ctx.edit_original_response(embed=embed)

    @setting_group.command(name="speaker", description="VOICEVOXの話者を設定します")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speaker(ctx, speaker: str):
      await ctx.response.defer()
      speaker_id = next((sid for sid, name in VOICEVOX_SPEAKERS if name == speaker), None)
      if speaker_id is None:
        embed = discord.Embed(
          title="設定失敗",
          description="話者が見つかりません。一覧から選択してください",
          color=discord.Color.red()
        )
        await ctx.edit_original_response(embed=embed)
        return
      self.server_config.set(ctx.guild.id, "Speaker", speaker_id)
      embed = discord.Embed(
        title="設定完了",
        description=f"Speaker を {speaker}（ID: {speaker_id}）に設定しました",
        color=discord.Color.green()
      )
      await ctx.edit_original_response(embed=embed)

    @setting_speaker.autocomplete("speaker")
    async def speaker_autocomplete(ctx, current: str):
      filtered = [
        discord.app_commands.Choice(name=name, value=name)
        for _, name in VOICEVOX_SPEAKERS
        if current in name
      ]
      return filtered[:25]

    @setting_group.command(name="volume", description="音量を設定します（0〜100）")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_volume(ctx, volume: int):
      await ctx.response.defer()
      try:
        self.server_config.set(ctx.guild.id, "Volume", volume)
      except ValueError:
        embed = discord.Embed(
          title="設定失敗",
          description="音量は 0〜100 の整数で指定してください",
          color=discord.Color.red()
        )
        await ctx.edit_original_response(embed=embed)
        return
      embed = discord.Embed(
        title="設定完了",
        description=f"Volume を {volume} に設定しました",
        color=discord.Color.green()
      )
      await ctx.edit_original_response(embed=embed)

    @setting_group.command(name="max-char", description="読み上げ最大文字数を設定します（0で無制限）")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_max_char(ctx, chars: int):
      await ctx.response.defer()
      try:
        self.server_config.set(ctx.guild.id, "MaxChar", chars)
      except ValueError:
        embed = discord.Embed(
          title="設定失敗",
          description="最大文字数は 0 以上の整数で指定してください",
          color=discord.Color.red()
        )
        await ctx.edit_original_response(embed=embed)
        return
      embed = discord.Embed(
        title="設定完了",
        description=f"MaxChar を {chars} に設定しました（{'無制限' if chars == 0 else f'{chars}文字'}）",
        color=discord.Color.green()
      )
      await ctx.edit_original_response(embed=embed)

    @setting_group.command(name="auto-join", description="VCに人が入ったとき自動で参加するかを設定します")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_auto_join(ctx, enabled: bool):
      await ctx.response.defer()
      self.server_config.set(ctx.guild.id, "AutoJoin", enabled)
      embed = discord.Embed(
        title="設定完了",
        description=f"AutoJoin を {'有効' if enabled else '無効'} にしました",
        color=discord.Color.green()
      )
      await ctx.edit_original_response(embed=embed)

    @setting_group.command(name="join-notice", description="VCに人が入ったときTTSで通知するかを設定します")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_join_notice(ctx, enabled: bool):
      await ctx.response.defer()
      self.server_config.set(ctx.guild.id, "JoinNotice", enabled)
      embed = discord.Embed(
        title="設定完了",
        description=f"JoinNotice を {'有効' if enabled else '無効'} にしました",
        color=discord.Color.green()
      )
      await ctx.edit_original_response(embed=embed)

    @setting_group.error
    async def setting_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=discord.Embed(
            title="権限エラー",
            description="サーバー管理権限が必要です",
            color=discord.Color.red()
          ),
          ephemeral=True
        )

    self.tree.add_command(setting_group)

  async def add_to_queue(self, content, msg: bool = True):
    if msg:
      message = content
      guild_id = message.guild.id
      speaker = self.server_config.get(guild_id, "Speaker")
      volume = self.server_config.volume_to_vvtts(guild_id)
      text = message.content
      max_char = self.server_config.get(guild_id, "MaxChar")
      if 0 < max_char < len(text):
        text = text[:max_char]
      src = await self.generate(text, guild_id, message.id, speaker, volume=volume)
      if src is not None:
        await self.voice_queues[guild_id].put((guild_id, src))
        if guild_id not in self.playing_tasks or self.playing_tasks[guild_id].done():
          self.playing_tasks[guild_id] = asyncio.create_task(self.play_loop(message.guild))

  # 音声生成
  async def generate(self, msg, guild_id, msg_id, speaker, volume=1.0):
    path = await self.vvtts.generate(msg, guild_id, msg_id, speaker, volume=volume)
    return path

  # 音声再生待機ループ
  async def play_loop(self, guild):
    guild_id = guild.id
    while True:
      try:
        _, src = await asyncio.wait_for(
          self.voice_queues[guild_id].get(),
          timeout=300
        )
        if guild.voice_client is None:
          self.voice_queues[guild_id].task_done()
          continue
        await self.play(guild, src)
        self.voice_queues[guild_id].task_done()
      except asyncio.TimeoutError:
        break
      except Exception as e:
        print(f"再生エラー: {e}")
        self.voice_queues[guild_id].task_done()

  # 音声再生
  async def play(self, guild, src):
    try:
      voice = await discord.FFmpegOpusAudio.from_probe(src)
      if self.skip_flags[guild.id]:
        self.skip_flags[guild.id] = False
        return
      while guild.voice_client.is_playing():
        await asyncio.sleep(0.1)
      if self.skip_flags[guild.id]:
        self.skip_flags[guild.id] = False
        return
      guild.voice_client.play(
        voice,
        after=lambda _: os.remove(src) if os.path.exists(src) else None
      )
    except Exception as e:
      print(f"音声再生エラー: {e}")