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


# 音声生成
@client.event
async def on_message(message):
  if not message.author.bot:
    path = await vvtts.generate(message.content, message.guild.id, message.id, 8)
    print(path)


# 起動
client.run(DISCORD_BOT_TOKEN)