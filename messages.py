"""
ファイル名：messages.py
作者：どら
説明：多言語メッセージ管理モジュール。
      JSON ファイル (conf/<lang>.json) からメッセージを読み込み、
      ドット区切りキーによる階層参照・Discord Embed 生成・スラッシュコマンド翻訳を提供する。
依存関係：discord.py
"""
import json
import os
import discord
from config import MESSAGES_DIR

_LANGS = ("ja", "en", "zh-CN", "zh-TW", "ko", "hg")

def _load_messages(lang: str) -> dict:
  with open(os.path.join(MESSAGES_DIR, f"{lang}.json"), encoding="utf-8") as f:
    return json.load(f)

_MESSAGES_BY_LANG = {lang: _load_messages(lang) for lang in _LANGS}

_COLOR_MAP = {
  "green": discord.Color.green,
  "red":   discord.Color.red,
  "blue":  discord.Color.blue,
}

_DISCORD_LOCALE_TO_LANG = {
  discord.Locale.japanese:        "ja",
  discord.Locale.american_english: "en",
  discord.Locale.british_english:  "en",
  discord.Locale.chinese:          "zh-CN",
  discord.Locale.taiwan_chinese:   "zh-TW",
  discord.Locale.korean:           "ko",
}


def get_desc(key: str, lang: str = "ja") -> str:
  """ドット区切りのキー（例: "commands.join"）でコマンドdescriptionを取得する。"""
  messages = _MESSAGES_BY_LANG.get(lang, _MESSAGES_BY_LANG["ja"])
  node = messages
  for part in key.split("."):
    node = node.get(part) if isinstance(node, dict) else None
    if node is None:
      print(f"[messages] 翻訳キーが見つかりません: '{key}' (lang={lang})")
      return key
  return node


async def handle_os_error(ctx, e: OSError, cmd_name: str, lang: str = "ja") -> None:
  print(f"OSError in {cmd_name}: {e}")
  try:
    await ctx.edit_original_response(embed=build_embed("error.os_error", lang=lang))
  except Exception as inner:
    print(f"Failed to send OSError embed in {cmd_name}: {inner}")


def build_embed(key: str, lang: str = "ja", **kwargs) -> discord.Embed:
  """
  ドット区切りのキー（例: "join.success"）でEmbedを生成する。
  kwargsはdescriptionのstr.format()に渡される。
  """
  messages = _MESSAGES_BY_LANG.get(lang, _MESSAGES_BY_LANG["ja"])
  node = messages
  for part in key.split("."):
    node = node.get(part) if isinstance(node, dict) else None
    if node is None:
      print(f"[messages] Embedキーが見つかりません: '{key}' (lang={lang})")
      return discord.Embed(title=key, color=discord.Color.red())
  title = node["title"]
  description = node.get("description", "").format(**kwargs)
  color = _COLOR_MAP[node["color"]]()
  embed = discord.Embed(title=title, description=description or None, color=color)
  for field in node.get("fields", []):
    embed.add_field(
      name=field["name"].format(**kwargs),
      value=field["value"].format(**kwargs),
      inline=field.get("inline", True)
    )
  footer = messages.get("_footer", {}).get("auto_translation", "")
  if footer:
    embed.set_footer(text=footer)
  return embed


class BotTranslator(discord.app_commands.Translator):
  async def translate(
      self,
      string: discord.app_commands.locale_str,
      locale: discord.Locale,
      context: discord.app_commands.TranslationContext,
  ) -> str | None:
    key = string.extras.get("key")
    if not key:
      return None
    lang = _DISCORD_LOCALE_TO_LANG.get(locale)
    if not lang:
      return None
    try:
      return get_desc(key, lang=lang)
    except (KeyError, TypeError):
      return None