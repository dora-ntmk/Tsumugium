import discord
from config import DISCORD_BOT_TOKEN, SERVER_CONFIG_PATH
from vvtts import VvTTS
from play import Play
from server_config import ServerConfig
from messages import build_embed, get_desc, handle_os_error
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
  description=get_desc("commands.join.description")
)
@discord.app_commands.describe(
  change_channel=get_desc("commands.join.args.change_channel")
)
async def join(ctx, change_channel: bool = False):
  try:
    await ctx.response.defer()
    if ctx.user.voice:
      await ctx.user.voice.channel.connect(timeout=60, self_deaf=True)
      if change_channel:
        try:
          server_config.set(ctx.guild.id, "TextTarget", ctx.channel.id)
          server_config.set(ctx.guild.id, "VoiceTarget", ctx.user.voice.channel.id)
          await ctx.edit_original_response(
            embed=build_embed(
              "join.success_change_channel",
              text=ctx.channel.mention,
              voice=ctx.user.voice.channel.mention
            )
          )
        except OSError:
          await ctx.edit_original_response(embed=build_embed("join.failure_change_channel"))
      else:
        await ctx.edit_original_response(embed=build_embed("join.success"))
    else:
      await ctx.edit_original_response(embed=build_embed("join.failure"))
  except discord.errors.InteractionResponded:
    return
  except discord.errors.HTTPException as e:
    print(f"HTTPException in join: {e}")
  except OSError as e:
    await handle_os_error(ctx, e, "join")
  except Exception as e:
    print(f"Exception in join: {e}")


# 退室
@tree.command(
  name="leave",
  description=get_desc("commands.leave.description")
)
async def leave(ctx):
  try:
    await ctx.response.defer()
    if ctx.user.voice:
      await ctx.guild.voice_client.disconnect()
      await ctx.edit_original_response(embed=build_embed("leave.success"))
    else:
      await ctx.edit_original_response(embed=build_embed("leave.failure"))
  except discord.errors.InteractionResponded:
    return
  except discord.errors.HTTPException as e:
    print(f"HTTPException in leave: {e}")
  except OSError as e:
    await handle_os_error(ctx, e, "leave")
  except Exception as e:
    print(f"Exception in leave: {e}")



# 起動
client.run(DISCORD_BOT_TOKEN)