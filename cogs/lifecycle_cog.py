"""Bot・ギルド・Soundboardのライフサイクルイベントを登録する。"""

import io
import json

import discord

from backup import start as start_backup
from services.error_notification_service import ensure_error_notifier


class LifecycleCog:
  def __init__(
      self,
      client,
      tree,
      server_config,
      dict_manager,
      sound_boards,
      *,
      backup_databases,
      status_service,
      error_notifier=None,
  ):
    self.client = client
    self.tree = tree
    self.server_config = server_config
    self.dict_manager = dict_manager
    self.sound_boards = sound_boards
    self.backup_databases = backup_databases
    self.status_service = status_service
    self.error_notifier = ensure_error_notifier(error_notifier)
    self.backup_task = None
    self._register()

  def _register(self) -> None:
    @self.client.event
    async def on_ready():
      await self.tree.sync()
      current_guild_ids = {str(guild.id) for guild in self.client.guilds}
      database_guild_ids = self.server_config.get_all_guild_ids()

      for guild_id in current_guild_ids - database_guild_ids:
        self.server_config.init_guild(int(guild_id))
        print(f"on_ready: init_guild {guild_id}")

      for guild_id in database_guild_ids - current_guild_ids:
        self.server_config.remove_guild(int(guild_id))
        self.dict_manager.remove_guild(int(guild_id))
        self.sound_boards.remove_guild(int(guild_id))
        print(f"on_ready: remove_guild {guild_id}")

      for guild_id in current_guild_ids:
        await self.sound_boards.refresh(guild_id)
        print(f"on_ready: refresh {guild_id}")

      self.backup_task = start_backup(
        self.backup_databases,
        self.error_notifier,
      )
      await self.status_service.update()
      print(discord.__version__)

    @self.client.event
    async def on_guild_join(guild):
      self.server_config.init_guild(guild.id)
      self.status_service.schedule_update()

    @self.client.event
    async def on_guild_remove(guild):
      try:
        normal_items, priority_items = self.dict_manager.get_entries(guild.id)
        combined = dict(priority_items + normal_items)
        if combined:
          data = json.dumps(
            combined,
            ensure_ascii=False,
            indent=2,
          ).encode("utf-8")
          file = discord.File(
            io.BytesIO(data),
            filename=f"{guild.id}_dict.json",
          )
          owner = guild.owner
          if owner is None and guild.owner_id:
            try:
              owner = await self.client.fetch_user(guild.owner_id)
            except (discord.NotFound, discord.HTTPException):
              pass
          if owner:
            try:
              dm = await owner.create_dm()
              await dm.send(
                content=f"サーバー「{guild.name}」の辞書データをお送りします。",
                files=[file],
              )
            except (discord.Forbidden, discord.HTTPException):
              pass
      except Exception as e:
        self.error_notifier.report(f"Exception in on_guild_remove (DM): {e}")
      finally:
        self.server_config.remove_guild(guild.id)
        self.dict_manager.remove_guild(guild.id)
        self.sound_boards.remove_guild(guild.id)
        self.status_service.schedule_update()

    @self.client.event
    async def on_socket_raw_receive(message):
      data = json.loads(message)
      if data.get("op") != 0:
        return
      event_type = data.get("t")
      payload = data.get("d")
      if payload is None:
        return
      if event_type in (
          "GUILD_SOUNDBOARD_SOUND_CREATE",
          "GUILD_SOUNDBOARD_SOUND_UPDATE",
      ):
        self.sound_boards.add(
          payload["guild_id"],
          payload["sound_id"],
          payload["name"],
        )
      elif event_type == "GUILD_SOUNDBOARD_SOUND_DELETE":
        self.sound_boards.delete(
          payload["guild_id"],
          payload["sound_id"],
        )
