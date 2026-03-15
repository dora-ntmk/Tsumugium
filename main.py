import asyncio
import discord
from config import DISCORD_BOT_TOKEN
from vvtts import VvTTS
from collections import defaultdict


# 起動設定
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)
vvtts = VvTTS()


# ギルドごとの音声キューと再生タスク
voice_queues = defaultdict(asyncio.Queue)
playing_tasks = {}
skip_flags = defaultdict(bool)


# 起動時動作
@client.event
async def on_ready():
  await tree.sync()
  stts = "Hello World!"
  await client.change_presence(status=discord.Status.online, activity=discord.Game(name=stts))
  print(discord.__version__)


# 入室
@tree.command(
  name="join",
  description="ボイスチャンネルに接続します。"
)
async def join(ctx):
  await ctx.response.defer()
  if ctx.user.voice:
    await ctx.user.voice.channel.connect(timeout=60, self_deaf=True)
    embed = discord.Embed(
      title="接続完了",
      description="ボイスチャンネルに接続しました",
      color=discord.Color.green()
    )
    await ctx.edit_original_response(embed=embed)
  else:
    embed = discord.Embed(
      title="接続失敗",
      description="ボイスチャンネルに接続できませんでした",
      color=discord.Color.red()
    )
    await ctx.edit_original_response(embed=embed)


# 退室
@tree.command(
  name="leave",
  description="ボイスチャンネルから切断します。"
)
async def leave(ctx):
  await ctx.response.defer()
  if ctx.user.voice:
    await ctx.guild.voice_client.disconnect()
    embed = discord.Embed(
      title="切断完了",
      description="ボイスチャンネルから切断しました",
      color=discord.Color.green()
    )
    await ctx.edit_original_response(embed=embed)
  else:
    embed = discord.Embed(
      title="切断失敗",
      description="ボイスチャンネルから切断できませんでした",
      color=discord.Color.red()
    )
    await ctx.edit_original_response(embed=embed)


# キュークリア
@tree.command(
  name="clear",
  description="読み上げキューをすべてクリアします。"
)
async def clear(ctx, instant: bool = True):
  await ctx.response.defer()
  queue = voice_queues[ctx.guild.id]
  cleared = queue.qsize()
  print(cleared)
  while not queue.empty():
    print(f"現在の数：{queue.qsize()}")
    try:
      message, src = queue.get_nowait()
      queue.task_done()
    except asyncio.QueueEmpty:
      break
  if instant:
    skip_flags[ctx.guild.id] = True
    if ctx.guild.voice_client and ctx.guild.voice_client.is_playing():
      ctx.guild.voice_client.stop()
  embed = discord.Embed(
    title="キュークリア完了",
    description=f"{cleared}件の読み上げをキャンセルしました",
    color=discord.Color.green()
  )
  await ctx.edit_original_response(embed=embed)


# メッセージ検出
@client.event
async def on_message(message):
  if message.author.bot:
    return
  if message.guild.voice_client is None:
    return
  if message.guild.voice_client.channel.id != message.channel.id:
    return
  if message.content.startswith("!s ") or message.flags.silent:
    return
  asyncio.create_task(add_to_queue(message))

async def add_to_queue(content, msg: bool = True):
  if msg:
    message = content
    src = await generate(message.content, message.guild.id, message.id, 8)
    await voice_queues[message.guild.id].put((message, src))
    if message.guild.id not in playing_tasks or playing_tasks[message.guild.id].done():
      playing_tasks[message.guild.id] = asyncio.create_task(play_loop(message.guild))


# 音声生成
async def generate(msg, guild_id, msg_id, speaker):
  path = await vvtts.generate(msg, guild_id, msg_id, speaker)
  return path


# 音声再生待機ループ
async def play_loop(guild):
  guild_id = guild.id
  while True:
    try:
      message, src = await asyncio.wait_for(
        voice_queues[guild_id].get(),
        timeout=300
      )
      if guild.voice_client is None:
        continue
      await play(message, src)
      voice_queues[guild_id].task_done()
    except asyncio.TimeoutError:
      break
    except Exception as e:
      print(f"再生エラー: {e}")
      voice_queues[guild_id].task_done()


# 音声再生
async def play(content, src):
  try:
    voice = await discord.FFmpegOpusAudio.from_probe(src)
    if skip_flags[content.guild.id]:
      skip_flags[content.guild.id] = False
      return
    while content.guild.voice_client.is_playing():
      await asyncio.sleep(0.1)
    if skip_flags[content.guild.id]:
      skip_flags[content.guild.id] = False
      return
    content.guild.voice_client.play(voice)
  except Exception as e:
    print(f"音声再生エラー: {e}")


# 起動
client.run(DISCORD_BOT_TOKEN)