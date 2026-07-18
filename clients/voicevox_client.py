"""VOICEVOX HTTP APIへの非同期アクセスを担当する。"""

import json
import os
from typing import Any


class VoicevoxClient:
  def __init__(
      self,
      url: str = "http://127.0.0.1:50021",
      *,
      tmp_dir: str = "tmp",
      session: Any = None,
  ):
    self.url = url.rstrip("/")
    self.tmp_dir = tmp_dir
    self._session = session
    self._owns_session = session is None

  async def _get_session(self):
    if self._session is None or getattr(self._session, "closed", False):
      import aiohttp

      self._session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=60),
      )
      self._owns_session = True
    return self._session

  async def generate(
      self,
      msg: str,
      guildid: int,
      msgid: int,
      speaker: int = 0,
      speed: float = 1.0,
      pitch: float = 0.0,
      intonation: float = 1.0,
      volume: float = 1.0,
  ):
    try:
      if msg is None:
        raise ValueError("msg cannot be None")
      if guildid is None:
        raise ValueError("guildid cannot be None")
      if msgid is None:
        raise ValueError("msgid cannot be None")

      session = await self._get_session()
      async with session.post(
          f"{self.url}/audio_query",
          params={"text": msg, "speaker": speaker},
      ) as response:
        response.raise_for_status()
        query = await response.json()

      query["speedScale"] = speed
      query["pitchScale"] = pitch
      query["intonationScale"] = intonation
      query["volumeScale"] = volume

      async with session.post(
          f"{self.url}/synthesis",
          headers={"Content-Type": "application/json"},
          params={"speaker": speaker},
          data=json.dumps(query),
      ) as response:
        response.raise_for_status()
        wav = await response.read()

      os.makedirs(self.tmp_dir, exist_ok=True)
      path = os.path.join(self.tmp_dir, f"{guildid}-{msgid}.wav")
      with open(path, mode="wb") as file:
        file.write(wav)
      return path
    except Exception as e:
      print(e)
      return None

  async def close(self) -> None:
    if (
        self._owns_session
        and self._session is not None
        and not getattr(self._session, "closed", False)
    ):
      await self._session.close()

