"""Tsumugiumで使用するDiscord Embedの共通ひな形。"""

from enum import Enum

import discord


class EmbedType(Enum):
  INFO = "info"
  SUCCESS = "success"
  WARNING = "warning"
  ERROR = "error"


_COLOR_FACTORIES = {
  EmbedType.INFO: discord.Color.blue,
  EmbedType.SUCCESS: discord.Color.green,
  EmbedType.WARNING: discord.Color.yellow,
  EmbedType.ERROR: discord.Color.red,
}


def make_embed(
    title: str,
    description: str | None = None,
    *,
    embed_type: EmbedType = EmbedType.INFO,
) -> discord.Embed:
  """共通の色設定でEmbedを生成する。本文とフィールドは呼び出し元で定義する。"""
  return discord.Embed(
    title=title,
    description=description or None,
    color=_COLOR_FACTORIES[embed_type](),
  )

