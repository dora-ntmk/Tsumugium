"""
ファイル名：setting.py
作者：どら
説明：サーバー設定コマンドモジュール。
      スラッシュコマンドグループ /setting を実装する。
      テキスト/ボイスチャンネルの設定、話者・音量・速度・最大文字数、
      AutoJoin・AccessNoticeをサーバー管理者向けに提供する。
依存関係：discord.py
"""
import json

import discord

from config import SPEAKERS_JSON
from presentation.embeds import EmbedType, make_embed
from presentation.error_handler import handle_internal_error, handle_os_error
from services.error_notification_service import ensure_error_notifier


with open(SPEAKERS_JSON, encoding="utf-8") as _f:
  VOICEVOX_SPEAKERS = [(s["id"], s["name"]) for s in json.load(_f)]

BOT_DEFAULT_LABEL = "Botのデフォルト"


class Setting:
  def __init__(self, client, tree, server_config, error_notifier=None):
    self.client = client
    self.tree = tree
    self.server_config = server_config
    self.error_notifier = ensure_error_notifier(error_notifier)
    self._register()

  def _register(self):
    setting_group = discord.app_commands.Group(
      name="setting",
      description="サーバー設定を管理します",
      default_permissions=discord.Permissions(manage_guild=True),
    )

    @setting_group.command(name="view", description="現在の設定を表示します")
    async def setting_view(ctx):
      try:
        await ctx.response.defer()
        cfg = self.server_config.get_all(ctx.guild.id)
        embed = make_embed("サーバー設定")
        text_ch = ctx.guild.get_channel(cfg["TextTarget"])
        voice_ch = ctx.guild.get_channel(cfg["VoiceTarget"])
        embed.add_field(
          name="読み上げ対象チャンネル",
          value="未設定" if text_ch is None else text_ch.mention,
          inline=False,
        )
        embed.add_field(
          name="接続チャンネル",
          value="未設定" if voice_ch is None else voice_ch.mention,
          inline=False,
        )
        raw_speaker = self.server_config.get_raw_speaker(ctx.guild.id)
        if raw_speaker is None:
          speaker_display = BOT_DEFAULT_LABEL
        else:
          speaker_display = next(
            (name for sid, name in VOICEVOX_SPEAKERS if sid == raw_speaker),
            str(raw_speaker),
          )
        embed.add_field(name="話者", value=speaker_display, inline=True)
        embed.add_field(name="音量", value=str(cfg["Volume"]), inline=True)
        embed.add_field(name="速さ", value=str(cfg["Speed"]), inline=True)
        embed.add_field(name="最大読み上げ文字数", value=str(cfg["MaxChar"]), inline=True)
        embed.add_field(name="自動入退室", value=str(cfg["AutoJoin"]), inline=True)
        embed.add_field(name="入退室通知", value=str(cfg["AccessNotice"]), inline=True)
        await ctx.edit_original_response(embed=embed)
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_view: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_view", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_view", self.error_notifier)

    @setting_group.command(
      name="text-target",
      description="読み上げ対象のテキストチャンネルを設定します（省略で現在のチャンネル）",
    )
    @discord.app_commands.describe(
      channel="設定するテキストチャンネル（省略で現在のチャンネル）"
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target(ctx, channel: discord.TextChannel = None):
      try:
        await ctx.response.defer()
        target = channel or ctx.channel
        perms = target.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages):
          await ctx.edit_original_response(
            embed=make_embed(
              "設定失敗",
              f"{target.mention} の表示・送信権限がありません",
              embed_type=EmbedType.ERROR,
            )
          )
          return
        self.server_config.set(ctx.guild.id, "TextTarget", target.id)
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"TextTarget を {target.mention} に設定しました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_text_target: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_text_target", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_text_target", self.error_notifier)

    @setting_group.command(
      name="text-target-reset",
      description="TextTarget を未設定に戻します",
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_text_target_reset(ctx):
      try:
        await ctx.response.defer()
        self.server_config.reset(ctx.guild.id, "TextTarget")
        await ctx.edit_original_response(
          embed=make_embed(
            "設定リセット",
            "TextTarget をリセットしました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_text_target_reset: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_text_target_reset", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_text_target_reset", self.error_notifier)

    @setting_group.command(
      name="voice-target",
      description="自動接続先VCを設定します（省略で現在入室中のVC）",
    )
    @discord.app_commands.describe(
      channel="設定するボイスチャンネル（省略で現在入室中のVC）"
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target(ctx, channel: discord.VoiceChannel = None):
      try:
        await ctx.response.defer()
        if channel is None:
          if ctx.user.voice is None:
            await ctx.edit_original_response(
              embed=make_embed(
                "設定失敗",
                "VCに入室していません。チャンネルを指定するか、VCに入室してから実行してください",
                embed_type=EmbedType.ERROR,
              )
            )
            return
          channel = ctx.user.voice.channel
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.connect and perms.speak):
          await ctx.edit_original_response(
            embed=make_embed(
              "設定失敗",
              f"{channel.mention} への接続権限がありません",
              embed_type=EmbedType.ERROR,
            )
          )
          return
        self.server_config.set(ctx.guild.id, "VoiceTarget", channel.id)
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"VoiceTarget を {channel.mention} に設定しました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_voice_target: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_voice_target", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_voice_target", self.error_notifier)

    @setting_group.command(
      name="voice-target-reset",
      description="VoiceTarget を未設定に戻します",
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_voice_target_reset(ctx):
      try:
        await ctx.response.defer()
        self.server_config.reset(ctx.guild.id, "VoiceTarget")
        await ctx.edit_original_response(
          embed=make_embed(
            "設定リセット",
            "VoiceTarget をリセットしました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_voice_target_reset: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_voice_target_reset", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_voice_target_reset", self.error_notifier)

    @setting_group.command(name="speaker", description="VOICEVOXの話者を設定します")
    @discord.app_commands.describe(speaker="使用するVOICEVOXの話者名")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speaker(ctx, speaker: str):
      try:
        await ctx.response.defer()
        if speaker == BOT_DEFAULT_LABEL:
          self.server_config.set(ctx.guild.id, "Speaker", None)
          await ctx.edit_original_response(
            embed=make_embed(
              "設定完了",
              "Speaker を Botのデフォルトに設定しました",
              embed_type=EmbedType.SUCCESS,
            )
          )
          return
        speaker_id = next(
          (sid for sid, name in VOICEVOX_SPEAKERS if name == speaker), None
        )
        if speaker_id is None:
          await ctx.edit_original_response(
            embed=make_embed(
              "設定失敗",
              "話者が見つかりません。一覧から選択してください",
              embed_type=EmbedType.ERROR,
            )
          )
          return
        self.server_config.set(ctx.guild.id, "Speaker", speaker_id)
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"Speaker を {speaker}（ID: {speaker_id}）に設定しました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_speaker: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_speaker", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_speaker", self.error_notifier)

    @setting_speaker.autocomplete("speaker")
    async def speaker_autocomplete(ctx, current: str):
      choices = []
      if not current or current in BOT_DEFAULT_LABEL:
        choices.append(
          discord.app_commands.Choice(name=BOT_DEFAULT_LABEL, value=BOT_DEFAULT_LABEL)
        )
      filtered = [
        discord.app_commands.Choice(name=name, value=name)
        for _, name in VOICEVOX_SPEAKERS
        if current in name
      ]
      return (choices + filtered)[:25]

    @setting_group.command(name="volume", description="音量を設定します（0〜100）")
    @discord.app_commands.describe(volume="音量（0〜100）")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_volume(ctx, volume: int):
      try:
        await ctx.response.defer()
        try:
          self.server_config.set(ctx.guild.id, "Volume", volume)
        except ValueError:
          await ctx.edit_original_response(
            embed=make_embed(
              "設定失敗",
              "音量は 0〜100 の整数で指定してください",
              embed_type=EmbedType.ERROR,
            )
          )
          return
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"Volume を {volume} に設定しました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_volume: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_volume", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_volume", self.error_notifier)

    @setting_group.command(
      name="speed",
      description="読み上げ速度を設定します（50〜200）",
    )
    @discord.app_commands.describe(speed="速度（50〜200、100が等速）")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_speed(ctx, speed: int):
      try:
        await ctx.response.defer()
        try:
          self.server_config.set(ctx.guild.id, "Speed", speed)
        except ValueError:
          await ctx.edit_original_response(
            embed=make_embed(
              "設定失敗",
              "速度は 50〜200 の整数で指定してください",
              embed_type=EmbedType.ERROR,
            )
          )
          return
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"Speed を {speed} に設定しました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_speed: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_speed", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_speed", self.error_notifier)

    @setting_group.command(
      name="max-char",
      description="読み上げ最大文字数を設定します（30〜200、0でデフォルトの50に戻す）",
    )
    @discord.app_commands.describe(
      chars="読み上げ最大文字数（30〜200、0でデフォルトの50に戻す）"
    )
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_max_char(ctx, chars: int):
      try:
        await ctx.response.defer()
        if chars == 0:
          chars = 50
        try:
          self.server_config.set(ctx.guild.id, "MaxChar", chars)
        except ValueError:
          await ctx.edit_original_response(
            embed=make_embed(
              "設定失敗",
              "最大文字数は 30〜200 の整数で指定してください（0でデフォルトの50に戻す）",
              embed_type=EmbedType.ERROR,
            )
          )
          return
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"MaxChar を {chars} に設定しました（{chars}文字）",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_max_char: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_max_char", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_max_char", self.error_notifier)

    @setting_group.command(
      name="auto-join",
      description="VCに人が入ったとき自動で参加するかを設定します",
    )
    @discord.app_commands.describe(enabled="有効にするかどうか")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_auto_join(ctx, enabled: bool):
      try:
        await ctx.response.defer()
        self.server_config.set(ctx.guild.id, "AutoJoin", enabled)
        state = "有効" if enabled else "無効"
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"AutoJoin を {state} にしました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_auto_join: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_auto_join", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_auto_join", self.error_notifier)

    @setting_group.command(
      name="access-notice",
      description="VCへの入退室をTTSで通知するかを設定します",
    )
    @discord.app_commands.describe(enabled="有効にするかどうか")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def setting_access_notice(ctx, enabled: bool):
      try:
        await ctx.response.defer()
        self.server_config.set(ctx.guild.id, "AccessNotice", enabled)
        state = "有効" if enabled else "無効"
        await ctx.edit_original_response(
          embed=make_embed(
            "設定完了",
            f"AccessNotice を {state} にしました",
            embed_type=EmbedType.SUCCESS,
          )
        )
      except discord.errors.InteractionResponded:
        return
      except discord.errors.HTTPException as e:
        self.error_notifier.report(f"HTTPException in setting_access_notice: {e}")
      except OSError as e:
        await handle_os_error(ctx, e, "setting_access_notice", self.error_notifier)
      except Exception as e:
        await handle_internal_error(ctx, e, "setting_access_notice", self.error_notifier)

    @setting_group.error
    async def setting_error(ctx, error):
      if isinstance(error, discord.app_commands.MissingPermissions):
        await ctx.response.send_message(
          embed=make_embed(
            "権限エラー",
            "サーバー管理権限が必要です",
            embed_type=EmbedType.ERROR,
          ),
          ephemeral=True,
        )

    self.tree.add_command(setting_group)
