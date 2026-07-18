"""旧importパスを維持するためのサーバー設定Repository互換モジュール。"""

from repositories.guild_config_repository import (
  DEFAULTS,
  GuildConfigRepository,
)


class ServerConfig(GuildConfigRepository):
  """v3.4以前のクラス名を維持する互換エイリアス。"""


__all__ = ["DEFAULTS", "GuildConfigRepository", "ServerConfig"]
