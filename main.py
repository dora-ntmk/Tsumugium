import discord
from config import DISCORD_BOT_TOKEN
from vvtts import VvTTS
from play import Play


# 起動設定
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)
vvtts = VvTTS()
play = Play(client, tree, vvtts)


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



# 起動
client.run(DISCORD_BOT_TOKEN)