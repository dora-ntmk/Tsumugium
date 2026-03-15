import asyncio
import discord
import os
from collections import defaultdict
from messages import build_embed, get_desc


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
      description=get_desc("commands.clear")
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
      await ctx.edit_original_response(embed=build_embed("clear.success", cleared=cleared))

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