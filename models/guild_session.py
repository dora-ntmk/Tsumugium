"""ギルド単位で保持する一時的な実行状態。"""

import asyncio
from dataclasses import dataclass, field

from models.audio_item import AudioItem


@dataclass(slots=True)
class GuildSession:
  queue: asyncio.Queue[AudioItem] = field(default_factory=asyncio.Queue)
  player_task: asyncio.Task | None = None
  keepalive_task: asyncio.Task | None = None
  temporary_text_channel_id: int | None = None
  pending_text_channel_id: int | None = None
  skipping: bool = False
  clearing: bool = False
