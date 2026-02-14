import discord
from config import DISCORD_BOT_TOKEN
from vvtts import VvTTS

# 起動設定
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)
vvtts = VvTTS()


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
    await ctx.edit_original_response(content="ボイスチャンネルに接続しました")
  else:
    await ctx.edit_original_response(content="ボイスチャンネルに接続できませんでした")

# 退室
@tree.command(
  name="leave",
  description="ボイスチャンネルから切断します。"
)
async def leave(ctx):
  await ctx.response.defer()
  if ctx.user.voice:
    await ctx.guild.voice_client.disconnect()
    await ctx.edit_original_response(content="ボイスチャンネルから切断しました")
  else:
    await ctx.edit_original_response(content="ボイスチャンネルから切断できませんでした")

# 音声生成・再生
@client.event
async def on_message(message):
  if not message.author.bot:
    path = await vvtts.generate(message.content, message.guild.id, message.id, 8)
    print(path)


# 起動
client.run(DISCORD_BOT_TOKEN)