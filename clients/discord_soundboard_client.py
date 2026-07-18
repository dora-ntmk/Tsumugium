"""Discord Soundboard HTTP APIへの非同期アクセスを担当する。"""

from typing import Any


class DiscordSoundboardClient:
  API_BASE = "https://discord.com/api/v10"

  def __init__(self, token: str, *, session: Any = None):
    self._token = token
    self._session = session
    self._owns_session = session is None

  @property
  def _headers(self) -> dict[str, str]:
    return {
      "Authorization": f"Bot {self._token}",
      "Content-Type": "application/json",
    }

  async def _get_session(self):
    if self._session is None or getattr(self._session, "closed", False):
      import aiohttp

      self._session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
      )
      self._owns_session = True
    return self._session

  async def list_sounds(self, guild_id: int | str) -> list[tuple[str, str]]:
    session = await self._get_session()
    async with session.get(
        f"{self.API_BASE}/guilds/{guild_id}/soundboard-sounds",
        headers=self._headers,
    ) as response:
      response.raise_for_status()
      payload = await response.json()
    return [
      (str(sound["sound_id"]), sound["name"])
      for sound in payload["items"]
    ]

  async def play(self, channel_id: int | str, sound_id: int | str) -> None:
    session = await self._get_session()
    async with session.post(
        f"{self.API_BASE}/channels/{channel_id}/send-soundboard-sound",
        headers=self._headers,
        json={"sound_id": str(sound_id)},
    ) as response:
      response.raise_for_status()

  async def close(self) -> None:
    if (
        self._owns_session
        and self._session is not None
        and not getattr(self._session, "closed", False)
    ):
      await self._session.close()

