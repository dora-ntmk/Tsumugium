"""
ファイル名：play.py
作者：どら
説明：音声再生キュー管理モジュール。
      ギルドごとに asyncio.Queue を持ち、TTS 音声の生成・再生・スキップを管理する Play クラスを提供する。
      サウンドボード ID が一致する場合は Discord API で直接再生する。
依存関係：discord.py, aiohttp
"""
import asyncio
import aiohttp
import discord
import os
from collections import defaultdict

from config import DISCORD_BOT_TOKEN
from messages import build_embed, get_desc


class Play:
  def __init__(self, client, tree, vvtts, server_config, dict_manager=None, leaving_guilds=None):
    self.client = client
    self.tree = tree
    self.vvtts = vvtts
    self.server_config = server_config
    self.dict_manager = dict_manager
    self.leaving_guilds = leaving_guilds if leaving_guilds is not None else set()
    self.voice_queues = defaultdict(asyncio.Queue)
    self.playing_tasks = {}
    self.skip_flags = defaultdict(bool)
    self.clearing_flags = defaultdict(bool)
    self.temp_text_targets = {}
    self.pending_temp_targets: dict = {}
    self.keepalive_tasks: dict = {}
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
            if isinstance(src, str):
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
        # Botメッセージ: sounddict一致時のみ再生。TTS・メンション処理は行わない
        if message.guild is None or message.guild.voice_client is None:
          return
        text_target = self.temp_text_targets.get(message.guild.id)
        if text_target is None:
          text_target = self.server_config.get(message.guild.id, "TextTarget")
        if text_target is not None:
          if message.channel.id != text_target and message.guild.voice_client.channel.id != message.channel.id:
            return
        else:
          if message.guild.voice_client.channel.id != message.channel.id:
            return
        asyncio.create_task(self.add_to_queue(message, sounddict_only=True))
        return

      # ボットへのメンション（単体）で入退室トグル
      # client.user.id を使用するためIDが変わっても動作する
      if message.guild is not None:
        bot_id = self.client.user.id
        if message.content.strip() in (f'<@{bot_id}>', f'<@!{bot_id}>'):
          lang = self.server_config.get(message.guild.id, "Language")
          try:
            if message.guild.voice_client is not None:
              if message.author.voice:
                self.leaving_guilds.add(message.guild.id)
                await message.guild.voice_client.disconnect()
                await message.channel.send(embed=build_embed("leave.success", lang=lang))
              else:
                await message.channel.send(embed=build_embed("leave.failure", lang=lang))
            else:
              if message.author.voice:
                voice_channel = message.author.voice.channel
                bot_member = message.guild.me
                vc_perms = voice_channel.permissions_for(bot_member)
                text_perms = message.channel.permissions_for(bot_member)
                issues = []
                if not (vc_perms.connect and vc_perms.speak):
                  issues.append(get_desc("join.no_permission_vc", lang=lang).format(channel=voice_channel.mention))
                if not (text_perms.view_channel and text_perms.send_messages):
                  issues.append(get_desc("join.no_permission_text", lang=lang).format(channel=message.channel.mention))
                if issues:
                  await message.channel.send(embed=build_embed("join.no_permission", lang=lang, issues="\n".join(issues)))
                  return
                await voice_channel.connect(timeout=60)
                self.temp_text_targets[message.guild.id] = message.channel.id
                await message.channel.send(
                  embed=build_embed("join.success_temp", lang=lang, vc=voice_channel.mention, text=message.channel.mention)
                )
              else:
                await message.channel.send(embed=build_embed("join.failure", lang=lang))
          except Exception as e:
            print(f"Exception in mention join/leave: {e}")
          return

      if message.guild.voice_client is None:
        return
      text_target = self.temp_text_targets.get(message.guild.id)
      if text_target is None:
        text_target = self.server_config.get(message.guild.id, "TextTarget")
      if text_target is not None:
        if message.channel.id != text_target and message.guild.voice_client.channel.id != message.channel.id:
          return
      else:
        if message.guild.voice_client.channel.id != message.channel.id:
          return
      if message.content.startswith("!s ") or message.flags.silent:
        return
      if message.content.strip() == "s":
        if message.guild.voice_client.is_playing():
          message.guild.voice_client.stop()
        return
      asyncio.create_task(self.add_to_queue(message))


  async def add_to_queue(self, content, msg: bool = True, sounddict_only: bool = False):
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
        text, replaced_ranges, sound_id = self.dict_manager.preprocess_text(text, guild_id, message.guild, message.attachments, message.mentions, author_id=message.author.id)
        if sound_id is not None:
          await self.voice_queues[guild_id].put((guild_id, ("soundboard", sound_id)))
          if guild_id not in self.playing_tasks or self.playing_tasks[guild_id].done():
            self.playing_tasks[guild_id] = asyncio.create_task(self.play_loop(message.guild))
          return
      if sounddict_only:
        return
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
      src = None
      try:
        _, src = await asyncio.wait_for(
          self.voice_queues[guild_id].get(),
          timeout=300
        )
        if guild.voice_client is None:
          self.voice_queues[guild_id].task_done()
          src = None
          continue
        if isinstance(src, tuple) and src[0] == "soundboard":
          _, sound_id = src
          while guild.voice_client is not None and guild.voice_client.is_playing():
            await asyncio.sleep(0.1)
          if guild.voice_client is not None:
            await self._play_soundboard(guild, sound_id)
          src = None
          self.voice_queues[guild_id].task_done()
        else:
          await self.play(guild, src)
          src = None
          self.voice_queues[guild_id].task_done()
      except asyncio.TimeoutError:
        break
      except asyncio.CancelledError:
        if src is not None and isinstance(src, str):
          asyncio.create_task(self.safe_remove(src))
        raise
      except Exception as e:
        print(f"再生エラー: {e}")
        if src is not None:
          self.voice_queues[guild_id].task_done()

  async def _play_soundboard(self, guild, sound_id: str):
    try:
      async with aiohttp.ClientSession() as session:
        async with session.post(
          f'https://discord.com/api/v10/channels/{guild.voice_client.channel.id}/send-soundboard-sound',
          headers={
            'Authorization': f'Bot {DISCORD_BOT_TOKEN}',
            'Content-Type': 'application/json',
          },
          json={'sound_id': f'{sound_id}'},
        ):
          pass
    except Exception as e:
      print(f'サウンドボード再生エラー：{e}')

  # キープアライブ
  async def _keepalive_loop(self, guild):
    SILENCE = b'\xF8\xFF\xFE'
    while True:
      await asyncio.sleep(270)
      vc = guild.voice_client
      if vc is None or not vc.is_connected():
        break
      if not vc.is_playing():
        try:
          vc.send_audio_packet(SILENCE, encode=False)
        except Exception as e:
          print(f"keepalive error: {e}")

  def start_keepalive(self, guild):
    self.stop_keepalive(guild.id)
    self.keepalive_tasks[guild.id] = asyncio.create_task(self._keepalive_loop(guild))

  def stop_keepalive(self, guild_id):
    task = self.keepalive_tasks.pop(guild_id, None)
    if task and not task.done():
      task.cancel()

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
      while guild.voice_client is not None and guild.voice_client.is_playing():
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