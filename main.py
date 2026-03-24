"""
ファイル名：main.py
作者：どら
説明：Tsumugium - Discord 読み上げボット エントリーポイント。
      ボットの初期化・起動、スラッシュコマンド (/join, /leave) の定義、
      VC入退室イベントに基づく AutoJoin・AccessNotice（入退室通知）処理、
      およびギルド退出時の辞書データ保全を担当する。
      VvTTS / ServerConfig / DictManager / SoundDict などのモジュールを統括する。
依存関係：discord.py
"""
import asyncio
import io
import json
import discord
from config import STATUS_MESSAGE, DISCORD_BOT_TOKEN, SERVER_CONFIG_DB, DICT_DB, SOUND_BOARDS_DB, VOICEVOX_URL
from backup import start as start_backup
from vvtts import VvTTS
from play import Play
from server_config import ServerConfig
from messages import build_embed, get_desc, handle_os_error, BotTranslator
from setting import Setting
from word_dict import DictManager, WordDict
from sound_dict import SoundDict, SoundDictView, UpdateSoundBoards


# 起動設定
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents, enable_debug_events=True)
tree = discord.app_commands.CommandTree(client)
vvtts = VvTTS(VOICEVOX_URL)
server_config = ServerConfig(SERVER_CONFIG_DB)
dict_manager = DictManager(DICT_DB)
sound_dict = SoundDict(dict_manager)
sound_boards = UpdateSoundBoards(SOUND_BOARDS_DB, dict_manager)
leaving_guilds: set = set()
play = Play(client, tree, vvtts, server_config, dict_manager, leaving_guilds)
setting = Setting(client, tree, server_config)
word_dict = WordDict(client, tree, dict_manager, server_config)
sound_dict_view = SoundDictView(client, tree, sound_dict, dict_manager, server_config, sound_boards)
_backup_task = None


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

  # 起動時にサーバー設定・辞書データを同期（オフライン中の参加・追放に対応）
  current_guild_ids = {str(g.id) for g in client.guilds}
  db_guild_ids = server_config.get_all_guild_ids()

  for gid_str in current_guild_ids - db_guild_ids:
    server_config.init_guild(int(gid_str))
    print(f'on_ready: init_guild {gid_str}')

  for gid_str in db_guild_ids - current_guild_ids:
    server_config.remove_guild(int(gid_str))
    dict_manager.remove_guild(int(gid_str))
    sound_boards.remove_guild(int(gid_str))
    print(f'on_ready: remove_guild {gid_str}')

  for gid_str in current_guild_ids:
    sound_boards.refresh(gid_str, DISCORD_BOT_TOKEN)
    print(f'on_ready: refresh {gid_str}')

  global _backup_task
  _backup_task = start_backup([SERVER_CONFIG_DB, DICT_DB])

  await client.change_presence(status=discord.Status.online, activity=discord.Game(name=STATUS_MESSAGE))
  print(discord.__version__)


# サーバー参加時にデフォルト設定を書き込む
@client.event
async def on_guild_join(guild):
  server_config.init_guild(guild.id)


# サーバー退出時に辞書データをオーナーへ送信して削除
@client.event
async def on_guild_remove(guild):
  try:
    normal_items, priority_items = dict_manager.get_entries(guild.id)
    combined = dict(priority_items + normal_items)  # 優先辞書が上、通常辞書が下
    if combined:
      data = json.dumps(combined, ensure_ascii=False, indent=2).encode('utf-8')
      file = discord.File(io.BytesIO(data), filename=f'{guild.id}_dict.json')
      owner = guild.owner
      if owner is None and guild.owner_id:
        try:
          owner = await client.fetch_user(guild.owner_id)
        except (discord.NotFound, discord.HTTPException):
          pass
      if owner:
        try:
          dm = await owner.create_dm()
          await dm.send(
            content=f'サーバー「{guild.name}」の辞書データをお送りします。',
            files=[file]
          )
        except (discord.Forbidden, discord.HTTPException):
          pass
  except Exception as e:
    print(f'Exception in on_guild_remove (DM): {e}')
  finally:
    server_config.remove_guild(guild.id)
    dict_manager.remove_guild(guild.id)
    sound_boards.remove_guild(guild.id)


# サウンドボード更新トリガー
@client.event
async def on_socket_raw_receive(msg):
  data = json.loads(msg)
  if data.get("op") != 0:
    return
  t = data.get("t")
  d = data.get("d")
  if d is None:
    return
  if t == "GUILD_SOUNDBOARD_SOUND_CREATE":
    sound_boards.add(d["guild_id"], d["sound_id"], d["name"])
  elif t == "GUILD_SOUNDBOARD_SOUND_UPDATE":
    sound_boards.add(d["guild_id"], d["sound_id"], d["name"])
  elif t == "GUILD_SOUNDBOARD_SOUND_DELETE":
    sound_boards.delete(d["guild_id"], d["sound_id"])
  else:
    return


# VC入退室検知（AutoJoin / AccessNotice）
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
        await asyncio.sleep(0.5)
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
      ch = get_notify_channel(guild, target_channel)
      bot_member = guild.me
      vc_perms = target_channel.permissions_for(bot_member)
      vc_ok = vc_perms.connect and vc_perms.speak
      text_ok = True
      if ch is not None and ch != target_channel:
        text_perms = ch.permissions_for(bot_member)
        text_ok = text_perms.view_channel and text_perms.send_messages
      issues = []
      if not vc_ok:
        issues.append(get_desc("join.no_permission_vc", lang=lang).format(channel=target_channel.mention))
      if not text_ok:
        issues.append(get_desc("join.no_permission_text", lang=lang).format(channel=ch.mention))
      if issues:
        if not text_ok:
          await member.send(get_desc("join.no_permission_text_dm", lang=lang))
        elif ch:
          await ch.send(embed=build_embed("join.no_permission", lang=lang, issues="\n".join(issues)))
        return
      await asyncio.sleep(1)
      await target_channel.connect(timeout=60)
      if ch:
        await ch.send(embed=build_embed("join.auto", lang=lang, vc=target_channel.mention, text=ch.mention))
      return  # 最初の入室者の入室通知をスキップ

  # AccessNotice
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
      voice_channel = ctx.user.voice.channel
      text_channel  = ctx.channel
      bot_member    = ctx.guild.me
      vc_perms      = voice_channel.permissions_for(bot_member)
      text_perms    = text_channel.permissions_for(bot_member)
      issues = []
      if not (vc_perms.connect and vc_perms.speak):
        issues.append(get_desc("join.no_permission_vc", lang=lang).format(channel=voice_channel.mention))
      if not (text_perms.view_channel and text_perms.send_messages):
        issues.append(get_desc("join.no_permission_text", lang=lang).format(channel=text_channel.mention))
      if issues:
        await ctx.edit_original_response(
          embed=build_embed("join.no_permission", lang=lang, issues="\n".join(issues))
        )
        return
      await voice_channel.connect(timeout=60)
      if change_channel:
        if not ctx.user.guild_permissions.manage_guild:
          await ctx.edit_original_response(
            embed=build_embed("join.no_permission_change_channel", lang=lang)
          )
        else:
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