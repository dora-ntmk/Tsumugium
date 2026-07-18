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

from config import DISCORD_BOT_TOKEN
from models.audio_item import AudioItem, SoundboardItem, TTSItem
from models.guild_session import GuildSession
from presentation.embeds import EmbedType, make_embed


class Play:
  def __init__(self, client, tree, vvtts, server_config, dict_manager=None, leaving_guilds=None):
    self.client = client
    self.tree = tree
    self.vvtts = vvtts
    self.server_config = server_config
    self.dict_manager = dict_manager
    self.leaving_guilds = leaving_guilds if leaving_guilds is not None else set()
    self.sessions: dict[int, GuildSession] = {}
    self._register()

  def get_session(self, guild_id: int) -> GuildSession:
    """ギルドの実行状態を取得し、存在しなければ初期化する。"""
    session = self.sessions.get(guild_id)
    if session is None:
      session = GuildSession()
      self.sessions[guild_id] = session
    return session

  async def enqueue(self, guild, item: AudioItem) -> None:
    """音声アイテムをキューへ追加し、必要なら再生ループを開始する。"""
    session = self.get_session(guild.id)
    await session.queue.put(item)
    if session.player_task is None or session.player_task.done():
      session.player_task = asyncio.create_task(self.play_loop(guild))

  def _register(self):

    # キュークリア
    @self.tree.command(
      name="clear",
      description="読み上げキューをすべてクリアします。"
    )
    async def clear(ctx, instant: bool = True):
      try:
        await ctx.response.defer()
        session = self.get_session(ctx.guild.id)
        queue = session.queue
        cleared = queue.qsize()
        pending_files = []
        while not queue.empty():
          try:
            item = queue.get_nowait()
            if isinstance(item, TTSItem):
              pending_files.append(item.path)
            queue.task_done()
          except asyncio.QueueEmpty:
            break
        session.skipping = True
        if instant and ctx.guild.voice_client and ctx.guild.voice_client.is_playing():
          ctx.guild.voice_client.stop()
        session.clearing = True
        await ctx.edit_original_response(
          embed=make_embed("削除中", "キューを削除しています　しばらくお待ちください")
        )
        await asyncio.sleep(1)
        for src in pending_files:
          await self.safe_remove(src)
        session.clearing = False
        await ctx.edit_original_response(
          embed=make_embed(
            "キュークリア完了",
            "すべての読み上げをキャンセルしました",
            embed_type=EmbedType.SUCCESS,
          )
        )
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
        session = self.get_session(message.guild.id)
        text_target = session.temporary_text_channel_id
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
          try:
            if message.guild.voice_client is not None:
              if message.author.voice:
                self.leaving_guilds.add(message.guild.id)
                await message.guild.voice_client.disconnect()
                await message.channel.send(
                  embed=make_embed(
                    "切断完了",
                    "ボイスチャンネルから切断しました",
                    embed_type=EmbedType.SUCCESS,
                  )
                )
              else:
                await message.channel.send(
                  embed=make_embed(
                    "切断失敗",
                    "ボイスチャンネルから切断できませんでした",
                    embed_type=EmbedType.ERROR,
                  )
                )
            else:
              if message.author.voice:
                voice_channel = message.author.voice.channel
                bot_member = message.guild.me
                vc_perms = voice_channel.permissions_for(bot_member)
                text_perms = message.channel.permissions_for(bot_member)
                issues = []
                if not (vc_perms.connect and vc_perms.speak):
                  issues.append(f"{voice_channel.mention} への接続権限がありません")
                if not (text_perms.view_channel and text_perms.send_messages):
                  issues.append(f"{message.channel.mention} の表示権限がありません")
                if issues:
                  await message.channel.send(
                    embed=make_embed(
                      "権限エラー",
                      "\n".join(issues),
                      embed_type=EmbedType.ERROR,
                    )
                  )
                  return
                await voice_channel.connect(timeout=60)
                self.get_session(message.guild.id).temporary_text_channel_id = message.channel.id
                embed = make_embed(
                  "接続完了",
                  f"ボイスチャンネルに接続しました。\n今回の通話に限り {message.channel.mention} のメッセージも読み上げます。",
                  embed_type=EmbedType.SUCCESS,
                )
                embed.add_field(
                  name="接続情報",
                  value=f"接続チャンネル：{voice_channel.mention}　読み上げチャンネル：{message.channel.mention}",
                  inline=False,
                )
                await message.channel.send(embed=embed)
              else:
                await message.channel.send(
                  embed=make_embed(
                    "接続失敗",
                    "ボイスチャンネルに接続できませんでした",
                    embed_type=EmbedType.ERROR,
                  )
                )
          except Exception as e:
            print(f"Exception in mention join/leave: {e}")
          return

      if message.guild.voice_client is None:
        return
      session = self.get_session(message.guild.id)
      text_target = session.temporary_text_channel_id
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
      session = self.get_session(guild_id)
      if session.clearing:
        return
      speaker = self.server_config.get(guild_id, "Speaker")
      volume = self.server_config.volume_to_vvtts(guild_id)
      speed = self.server_config.speed_to_vvtts(guild_id)
      text = message.content
      replaced_ranges = []
      if self.dict_manager is not None:
        text, replaced_ranges, sound_id = self.dict_manager.preprocess_text(text, guild_id, message.guild, message.attachments, message.mentions, author_id=message.author.id)
        if sound_id is not None:
          await self.enqueue(message.guild, SoundboardItem(sound_id))
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
        await self.enqueue(message.guild, TTSItem(src))

  # 音声生成
  async def generate(self, msg, guild_id, msg_id, speaker, speed=1.0, volume=1.0):
    path = await self.vvtts.generate(msg, guild_id, msg_id, speaker, speed=speed, volume=volume)
    return path

  # 音声再生待機ループ
  async def play_loop(self, guild):
    guild_id = guild.id
    session = self.get_session(guild_id)
    while True:
      item = None
      try:
        item = await asyncio.wait_for(
          session.queue.get(),
          timeout=300
        )
        if guild.voice_client is None:
          session.queue.task_done()
          item = None
          continue
        if isinstance(item, SoundboardItem):
          while guild.voice_client is not None and guild.voice_client.is_playing():
            await asyncio.sleep(0.1)
          if guild.voice_client is not None:
            await self._play_soundboard(guild, item.sound_id)
        elif isinstance(item, TTSItem):
          await self.play(guild, item.path)
        else:
          raise TypeError(f"未対応の音声アイテムです: {type(item).__name__}")
        session.queue.task_done()
        item = None
      except asyncio.TimeoutError:
        break
      except asyncio.CancelledError:
        if isinstance(item, TTSItem):
          asyncio.create_task(self.safe_remove(item.path))
        raise
      except Exception as e:
        print(f"再生エラー: {e}")
        if item is not None:
          session.queue.task_done()

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
    session = self.get_session(guild.id)
    session.keepalive_task = asyncio.create_task(self._keepalive_loop(guild))

  def stop_keepalive(self, guild_id):
    session = self.get_session(guild_id)
    task = session.keepalive_task
    session.keepalive_task = None
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
      session = self.get_session(guild.id)
      voice = await discord.FFmpegOpusAudio.from_probe(src)
      if session.skipping:
        session.skipping = False
        asyncio.create_task(self.safe_remove(src))
        return
      while guild.voice_client is not None and guild.voice_client.is_playing():
        await asyncio.sleep(0.1)
      if session.skipping:
        session.skipping = False
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
