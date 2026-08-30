"""VOICEVOX HTTP APIへの非同期アクセスを担当する。"""

import asyncio
import json
import os
from typing import Any

from services.error_notification_service import ensure_error_notifier


class VoicevoxClient:
  def __init__(
      self,
      url: str = "http://127.0.0.1:50021",
      *,
      tmp_dir: str = "tmp",
      session: Any = None,
      error_notifier=None,
  ):
    self.url = url.rstrip("/")
    self.tmp_dir = tmp_dir
    self._session = session
    self._owns_session = session is None
    self.error_notifier = ensure_error_notifier(error_notifier)

  def cleanup_tmp_wav_files(self) -> int:
    """起動前に前回実行で残ったTMP_DIR直下のWAVを削除する。"""
    if not os.path.isdir(self.tmp_dir):
      return 0
    removed = 0
    try:
      entries = list(os.scandir(self.tmp_dir))
    except OSError as error:
      self.error_notifier.report_exception(
        error,
        "startup temporary audio scan",
        {"tmp_dir": os.path.abspath(self.tmp_dir)},
      )
      return 0
    for entry in entries:
      if not entry.is_file(follow_symlinks=False):
        continue
      if not entry.name.lower().endswith(".wav"):
        continue
      try:
        os.remove(entry.path)
        removed += 1
      except OSError as error:
        self.error_notifier.report_exception(
          error,
          "startup temporary audio cleanup",
          {
            "tmp_dir": os.path.abspath(self.tmp_dir),
            "file_name": entry.name,
          },
        )
    return removed

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

      wav = await self._generate_with_retry(
        msg, speaker, speed, pitch, intonation, volume
      )

      os.makedirs(self.tmp_dir, exist_ok=True)
      path = os.path.join(self.tmp_dir, f"{guildid}-{msgid}.wav")
      with open(path, mode="wb") as file:
        file.write(wav)
      return path
    except Exception as e:
      self.error_notifier.report_exception(
        e,
        "VOICEVOX generation",
        {
          "guild_id": guildid,
          "message_id": msgid,
          "speaker": speaker,
        },
      )
      return None

  async def _generate_with_retry(
      self, msg, speaker, speed, pitch, intonation, volume
  ) -> bytes:
    """VOICEVOXの一時的な切断だけを短い間隔で再試行する。"""
    import aiohttp

    attempts = 3
    for attempt in range(attempts):
      try:
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
          return await response.read()
      except (
          asyncio.TimeoutError,
          aiohttp.ServerDisconnectedError,
          aiohttp.ClientConnectionError,
      ):
        if attempt == attempts - 1:
          raise
        await asyncio.sleep(0.5 * (2 ** attempt))

    raise RuntimeError("VOICEVOX retry loop ended unexpectedly")

  async def close(self) -> None:
    if (
        self._owns_session
        and self._session is not None
        and not getattr(self._session, "closed", False)
    ):
      await self._session.close()

