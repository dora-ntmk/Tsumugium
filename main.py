import discord
from config import DISCORD_BOT_TOKEN, SERVER_CONFIG_PATH
from vvtts import VvTTS
from play import Play
from server_config import ServerConfig
from messages import build_embed, get_desc
from setting import Setting


# 起動設定
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)
vvtts = VvTTS()
server_config = ServerConfig(SERVER_CONFIG_PATH)
play = Play(client, tree, vvtts, server_config)
setting = Setting(client, tree, server_config)


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
  description=get_desc("commands.join")
)
async def join(ctx):
  await ctx.response.defer()
  if ctx.user.voice:
    await ctx.user.voice.channel.connect(timeout=60, self_deaf=True)
    await ctx.edit_original_response(embed=build_embed("join.success"))
  else:
    await ctx.edit_original_response(embed=build_embed("join.failure"))


# 退室
@tree.command(
  name="leave",
  description=get_desc("commands.leave")
)
async def leave(ctx):
  await ctx.response.defer()
  if ctx.user.voice:
    await ctx.guild.voice_client.disconnect()
    await ctx.edit_original_response(embed=build_embed("leave.success"))
  else:
    await ctx.edit_original_response(embed=build_embed("leave.failure"))



# 起動
client.run(DISCORD_BOT_TOKEN)