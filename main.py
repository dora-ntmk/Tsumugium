import asyncio
import discord
from config import DISCORD_BOT_TOKEN, SERVER_CONFIG_PATH
from vvtts import VvTTS
from play import Play
from server_config import ServerConfig
from messages import build_embed, get_desc, handle_os_error, BotTranslator
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
leaving_guilds: set = set()


def get_notify_channel(guild, vc_channel=None):
  text_target = play.temp_text_targets.get(guild.id)
  if text_target is None:
    text_target = server_config.get(guild.id, "TextTarget")
  if text_target:
    return guild.get_channel(text_target)
  return vc_channel


async def enqueue_notice(guild, member, msg_key, lang: str = "ja"):
  notice_text = get_desc(msg_key, lang=lang).format(display_name=member.display_name)
  speaker = server_config.get(guild.id, "Speaker")
  volume = server_config.volume_to_vvtts(guild.id)
  speed = server_config.speed_to_vvtts(guild.id)
  src = await play.generate(notice_text, guild.id, member.id, speaker, speed=speed, volume=volume)
  if src is not None:
    await play.voice_queues[guild.id].put((guild.id, src))
    if guild.id not in play.playing_tasks or play.playing_tasks[guild.id].done():
      play.playing_tasks[guild.id] = asyncio.create_task(play.play_loop(guild))


# 起動時動作
@client.event
async def on_ready():
  await tree.set_translator(BotTranslator())
  await tree.sync()
  stts = "Hello World!"
  await client.change_presence(status=discord.Status.online, activity=discord.Game(name=stts))
  print(discord.__version__)


# サーバー参加時にデフォルト設定を書き込む
@client.event
async def on_guild_join(guild):
  server_config.init_guild(guild.id)


# VC入退室検知（AutoJoin / JoinNotice）
@client.event
async def on_voice_state_update(member, before, after):
  guild = member.guild
  lang = server_config.get(guild.id, "Language")

  # Bot自身の切断検知（強制切断 vs 自発的退出の区別）
  if member == guild.me:
    if before.channel is not None and after.channel is None:
      play.temp_text_targets.pop(guild.id, None)
      if guild.id in leaving_guilds:
        leaving_guilds.discard(guild.id)
      else:
        ch = get_notify_channel(guild, before.channel)
        if ch:
          await ch.send(embed=build_embed("leave.forced", lang=lang))
    return

  user_joined = before.channel is None and after.channel is not None

  # ユーザー退出時: Botがいるチャンネルが空になったら自動退出
  user_left = before.channel is not None
  if user_left and guild.voice_client is not None:
    bot_channel = guild.voice_client.channel
    if before.channel == bot_channel:
      human_members = [m for m in bot_channel.members if not m.bot]
      if len(human_members) == 0:
        ch = get_notify_channel(guild, bot_channel)
        leaving_guilds.add(guild.id)
        await guild.voice_client.disconnect()
        if ch:
          await ch.send(embed=build_embed("leave.auto", lang=lang))
        return
      else:
        # LeaveNotice
        if server_config.get(guild.id, "AccessNotice"):
          await enqueue_notice(guild, member, "leave.notice_text", lang=lang)

  if not user_joined:
    return

  # AutoJoin: VoiceTarget が設定されている場合のみ動作
  voice_target = server_config.get(guild.id, "VoiceTarget")
  if server_config.get(guild.id, "AutoJoin") and guild.voice_client is None and voice_target is not None:
    target_channel = guild.get_channel(voice_target)
    if target_channel is not None:
      await target_channel.connect(timeout=60, self_deaf=True)
      ch = get_notify_channel(guild, target_channel)
      if ch:
        await ch.send(embed=build_embed("join.auto", lang=lang, vc=target_channel.mention, text=ch.mention))

  # JoinNotice
  if server_config.get(guild.id, "AccessNotice") and guild.voice_client is not None:
    await enqueue_notice(guild, member, "join.notice_text", lang=lang)


# 入室
@tree.command(
  name="join",
  description=discord.app_commands.locale_str(get_desc("commands.join.description"), key="commands.join.description")
)
@discord.app_commands.describe(
  change_channel=discord.app_commands.locale_str(
    get_desc("commands.join.args.change_channel"),
    key="commands.join.args.change_channel"
  )
)
async def join(ctx, change_channel: bool = False):
  try:
    await ctx.response.defer()
    lang = server_config.get(ctx.guild.id, "Language")
    if ctx.user.voice:
      await ctx.user.voice.channel.connect(timeout=60, self_deaf=True)
      if change_channel:
        try:
          server_config.set(ctx.guild.id, "TextTarget", ctx.channel.id)
          server_config.set(ctx.guild.id, "VoiceTarget", ctx.user.voice.channel.id)
          await ctx.edit_original_response(
            embed=build_embed(
              "join.success_change_channel",
              lang=lang,
              vc=ctx.user.voice.channel.mention,
              text=ctx.channel.mention,
              voice=ctx.user.voice.channel.mention
            )
          )
        except OSError:
          await ctx.edit_original_response(embed=build_embed("join.failure_change_channel", lang=lang))
      else:
        play.temp_text_targets[ctx.guild.id] = ctx.channel.id
        await ctx.edit_original_response(
          embed=build_embed("join.success_temp", lang=lang, vc=ctx.user.voice.channel.mention, text=ctx.channel.mention)
        )
    else:
      await ctx.edit_original_response(embed=build_embed("join.failure", lang=lang))
  except discord.errors.InteractionResponded:
    return
  except discord.errors.HTTPException as e:
    print(f"HTTPException in join: {e}")
  except OSError as e:
    await handle_os_error(ctx, e, "join", lang=server_config.get(ctx.guild.id, "Language"))
  except Exception as e:
    print(f"Exception in join: {e}")


# 退室
@tree.command(
  name="leave",
  description=discord.app_commands.locale_str(get_desc("commands.leave.description"), key="commands.leave.description")
)
async def leave(ctx):
  try:
    await ctx.response.defer()
    lang = server_config.get(ctx.guild.id, "Language")
    if ctx.user.voice:
      leaving_guilds.add(ctx.guild.id)
      await ctx.guild.voice_client.disconnect()
      await ctx.edit_original_response(embed=build_embed("leave.success", lang=lang))
    else:
      await ctx.edit_original_response(embed=build_embed("leave.failure", lang=lang))
  except discord.errors.InteractionResponded:
    return
  except discord.errors.HTTPException as e:
    print(f"HTTPException in leave: {e}")
  except OSError as e:
    await handle_os_error(ctx, e, "leave", lang=server_config.get(ctx.guild.id, "Language"))
  except Exception as e:
    print(f"Exception in leave: {e}")



# 起動
client.run(DISCORD_BOT_TOKEN)
