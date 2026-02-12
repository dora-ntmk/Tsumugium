import discord
import requests
import json
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
  stts = "Hello World!"
  await client.change_presence(status=discord.Status.online, activity=discord.Game(name=stts))
  print(discord.__version__)

# 音声生成
@client.event
async def on_message(message):
  if not message.author.bot:
    res1 = requests.post("http://localhost:50021/audio_query", params={"text": message.content, "speaker": 8})
    headers = {"content-type": "application/json"}
    res2 = requests.post("http://localhost:50021/synthesis", headers=headers,params={"speaker": 8},
                         data=json.dumps(res1.json()))
    with open(f"tmp/{message.guild.id}-{message.id}.wav", mode="wb") as f:
      f.write(res2.content)
      f.close()

# 起動
client.run(DISCORD_BOT_TOKEN)