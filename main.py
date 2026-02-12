import discord
from config import DISCORD_BOT_TOKEN

# 起動設定

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

# 起動時動作

@client.event
async def on_ready():
  await tree.sync()
  await client.change_presence(status=discord.Status.online, activity=discord.Game(name="Hello World!"))
  print(discord.__version__)

@client.event
async def on_message(message):
  if not message.author.bot:
    print(message.content)

# 起動

client.run(DISCORD_BOT_TOKEN)