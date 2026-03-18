import asyncio
import discord
import os
from collections import defaultdict
from messages import build_embed, get_desc


class Play:
  def __init__(self, client, tree, vvtts, server_config, dict_manager=None):
    self.client = client
    self.tree = tree
    self.vvtts = vvtts
    self.server_config = server_config
    self.dict_manager = dict_manager
    self.voice_queues = defaultdict(asyncio.Queue)
    self.playing_tasks = {}
    self.skip_flags = defaultdict(bool)
    self.clearing_flags = defaultdict(bool)
    self.temp_text_targets = {}
    self._register()

  def _register(self):

    # キュークリア
    @self.tree.command(
      name="clear",
      description=get_desc("commands.clear.description")
    )
    async def clear(ctx, instant: bool = True):
      try:
        await ctx.response.defer()
        lang = self.server_config.get(ctx.guild.id, "Language")
        queue = self.voice_queues[ctx.guild.id]
        cleared = queue.qsize()
        pending_files = []
        while not queue.empty():
          try:
            _, src = queue.get_nowait()
            pending_files.append(src)
            queue.task_done()
          except asyncio.QueueEmpty:
            break
        self.skip_flags[ctx.guild.id] = True
        if instant and ctx.guild.voice_client and ctx.guild.voice_client.is_playing():
          ctx.guild.voice_client.stop()
        self.clearing_flags[ctx.guild.id] = True
        await ctx.edit_original_response(embed=build_embed("clear.clearing", lang=lang))
        await asyncio.sleep(1)
        for src in pending_files:
          await self.safe_remove(src)
        self.clearing_flags[ctx.guild.id] = False
        await ctx.edit_original_response(embed=build_embed("clear.success", lang=lang, cleared=cleared))
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        print(f"HTTPException in clear: {e}")
      except Exception as e:
        print(f"Exception in clear: {e}")

    # メッセージ検出
    @self.client.event
    async def on_message(message):
      if message.author.bot:
        return
      if message.guild.voice_client is None:
        return
      text_target = self.temp_text_targets.get(message.guild.id)
      if text_target is None:
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


  async def add_to_queue(self, content, msg: bool = True):
    if msg:
      message = content
      guild_id = message.guild.id
      if self.clearing_flags[guild_id]:
        return
      speaker = self.server_config.get(guild_id, "Speaker")
      volume = self.server_config.volume_to_vvtts(guild_id)
      speed = self.server_config.speed_to_vvtts(guild_id)
      text = message.content
      replaced_ranges = []
      if self.dict_manager is not None:
        text, replaced_ranges = self.dict_manager.preprocess_text(text, guild_id, message.guild, message.attachments, message.mentions)
      max_char = self.server_config.get(guild_id, "MaxChar")
      if 0 < max_char < len(text):
        cut = max_char
        for start, end in replaced_ranges:
          if start < cut < end:
            cut = end
            break
        text = text[:cut] + ",以下省略"
      src = await self.generate(text, guild_id, message.id, speaker, speed=speed, volume=volume)
      if src is not None:
        await self.voice_queues[guild_id].put((guild_id, src))
        if guild_id not in self.playing_tasks or self.playing_tasks[guild_id].done():
          self.playing_tasks[guild_id] = asyncio.create_task(self.play_loop(message.guild))

  # 音声生成
  async def generate(self, msg, guild_id, msg_id, speaker, speed=1.0, volume=1.0):
    path = await self.vvtts.generate(msg, guild_id, msg_id, speaker, speed=speed, volume=volume)
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

  async def safe_remove(self, src, retries=5, delay=0.3):
    for _ in range(retries):
      try:
        if os.path.exists(src):
          os.remove(src)
        return
      except PermissionError:
        await asyncio.sleep(delay)
    print(f"ファイル削除失敗（使用中）: {src}")

  # 音声再生
  async def play(self, guild, src):
    try:
      voice = await discord.FFmpegOpusAudio.from_probe(src)
      if self.skip_flags[guild.id]:
        self.skip_flags[guild.id] = False
        asyncio.create_task(self.safe_remove(src))
        return
      while guild.voice_client.is_playing():
        await asyncio.sleep(0.1)
      if self.skip_flags[guild.id]:
        self.skip_flags[guild.id] = False
        asyncio.create_task(self.safe_remove(src))
        return
      guild.voice_client.play(
        voice,
        after=lambda _: asyncio.run_coroutine_threadsafe(
          self.safe_remove(src), self.client.loop
        )
      )
    except Exception as e:
      print(f"音声再生エラー: {e}")