import asyncio
import discord
import os
from collections import defaultdict


class Play:
  def __init__(self, client, tree, vvtts):
    self.client = client
    self.tree = tree
    self.vvtts = vvtts
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
      # スキップ条件
      if message.author.bot:
        return
      if message.guild.voice_client is None:
        return
      if message.guild.voice_client.channel.id != message.channel.id:
        return
      if message.content.startswith("!s ") or message.flags.silent:
        return
      asyncio.create_task(self.add_to_queue(message))

  async def add_to_queue(self, content, msg: bool = True):
    if msg:
      message = content
      src = await self.generate(message.content, message.guild.id, message.id, 8)
      await self.voice_queues[message.guild.id].put((message, src))
      if message.guild.id not in self.playing_tasks or self.playing_tasks[message.guild.id].done():
        self.playing_tasks[message.guild.id] = asyncio.create_task(self.play_loop(message.guild))

  # 音声生成
  async def generate(self, msg, guild_id, msg_id, speaker):
    path = await self.vvtts.generate(msg, guild_id, msg_id, speaker)
    return path

  # 音声再生待機ループ
  async def play_loop(self, guild):
    guild_id = guild.id
    while True:
      try:
        message, src = await asyncio.wait_for(
          self.voice_queues[guild_id].get(),
          timeout=300
        )
        if guild.voice_client is None:
          continue
        await self.play(message, src)
        self.voice_queues[guild_id].task_done()
      except asyncio.TimeoutError:
        break
      except Exception as e:
        print(f"再生エラー: {e}")
        self.voice_queues[guild_id].task_done()

  # 音声再生
  async def play(self, content, src):
    try:
      voice = await discord.FFmpegOpusAudio.from_probe(src)
      if self.skip_flags[content.guild.id]:
        self.skip_flags[content.guild.id] = False
        return
      while content.guild.voice_client.is_playing():
        await asyncio.sleep(0.1)
      if self.skip_flags[content.guild.id]:
        self.skip_flags[content.guild.id] = False
        return
      content.guild.voice_client.play(
        voice,
        after=lambda _: os.remove(src) if os.path.exists(src) else None
      )
    except Exception as e:
      print(f"音声再生エラー: {e}")