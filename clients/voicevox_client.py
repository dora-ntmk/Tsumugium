"""VOICEVOX HTTP APIへの非同期アクセスを担当する。"""

import asyncio
import json
import os
from typing import Any

from services.error_notification_service import ensure_error_notifier


class VoicevoxGenerationError(RuntimeError):
  def __init__(self, attempts: int, elapsed: float, last_error: BaseException):
    self.attempts = attempts
    self.elapsed = elapsed
    self.last_error = last_error
    super().__init__(
      "VOICEVOXの一時障害が解消しませんでした: "
      f"attempts={attempts}, elapsed={elapsed:.1f}s, "
      f"last_error={type(last_error).__name__}: {last_error}"
    )


class VoicevoxClient:
  RETRY_WINDOW_SECONDS = 30.0
  MAX_RETRY_DELAY_SECONDS = 5.0

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
    self._generation_lock = asyncio.Lock()
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

  async def check_health(self) -> str:
    """VOICEVOXへ疎通できることを確認し、エンジンのバージョンを返す。"""
    import aiohttp

    session = await self._get_session()
    async with session.get(
        f"{self.url}/version",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as response:
      response.raise_for_status()
      version = (await response.text()).strip()
    if not version:
      raise RuntimeError("VOICEVOX /version returned an empty response")
    return version

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
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    try:
      if msg is None:
        raise ValueError("msg cannot be None")
      if guildid is None:
        raise ValueError("guildid cannot be None")
      if msgid is None:
        raise ValueError("msgid cannot be None")

      wav = await self._generate_serially(
        msg,
        speaker,
        speed,
        pitch,
        intonation,
        volume,
        started_at,
      )

      os.makedirs(self.tmp_dir, exist_ok=True)
      path = os.path.join(self.tmp_dir, f"{guildid}-{msgid}.wav")
      with open(path, mode="wb") as file:
        file.write(wav)
      return path
    except VoicevoxGenerationError as e:
      self.error_notifier.report_exception(
        e,
        "VOICEVOX generation",
        {
          "attempts": e.attempts,
          "elapsed_seconds": f"{e.elapsed:.1f}",
          "guild_id": guildid,
          "last_error": f"{type(e.last_error).__name__}: {e.last_error}",
          "message_id": msgid,
          "speaker": speaker,
        },
      )
      return None
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

  async def _generate_serially(
      self,
      msg,
      speaker,
      speed,
      pitch,
      intonation,
      volume,
      started_at,
  ) -> bytes:
    loop = asyncio.get_running_loop()
    deadline = started_at + self.RETRY_WINDOW_SECONDS
    remaining = deadline - loop.time()
    if remaining <= 0:
      timeout = asyncio.TimeoutError("VOICEVOX generation lock deadline exceeded")
      raise VoicevoxGenerationError(0, loop.time() - started_at, timeout)

    acquired = False
    try:
      try:
        await asyncio.wait_for(self._generation_lock.acquire(), timeout=remaining)
        acquired = True
      except asyncio.TimeoutError as error:
        raise VoicevoxGenerationError(
          0, loop.time() - started_at, error
        ) from None
      return await self._generate_with_retry(
        msg,
        speaker,
        speed,
        pitch,
        intonation,
        volume,
        deadline=deadline,
        started_at=started_at,
      )
    finally:
      if acquired:
        self._generation_lock.release()

  async def _generate_with_retry(
      self,
      msg,
      speaker,
      speed,
      pitch,
      intonation,
      volume,
      *,
      deadline=None,
      started_at=None,
  ) -> bytes:
    """VOICEVOXの一時的な切断を期限内で再試行する。"""
    import aiohttp

    loop = asyncio.get_running_loop()
    if started_at is None:
      started_at = loop.time()
    if deadline is None:
      deadline = started_at + self.RETRY_WINDOW_SECONDS
    attempts = 0
    retry_delay = 0.5
    last_error: BaseException = asyncio.TimeoutError(
      "VOICEVOX generation deadline exceeded"
    )
    while True:
      remaining = deadline - loop.time()
      if remaining <= 0:
        raise VoicevoxGenerationError(
          attempts, loop.time() - started_at, last_error
        ) from None
      attempts += 1
      try:
        session = await self._get_session()
        async with session.post(
            f"{self.url}/audio_query",
            params={"text": msg, "speaker": speaker},
            timeout=aiohttp.ClientTimeout(total=remaining),
        ) as response:
          response.raise_for_status()
          query = await response.json()

        query["speedScale"] = speed
        query["pitchScale"] = pitch
        query["intonationScale"] = intonation
        query["volumeScale"] = volume

        remaining = deadline - loop.time()
        if remaining <= 0:
          raise asyncio.TimeoutError("VOICEVOX generation deadline exceeded")
        async with session.post(
            f"{self.url}/synthesis",
            headers={"Content-Type": "application/json"},
            params={"speaker": speaker},
            data=json.dumps(query),
            timeout=aiohttp.ClientTimeout(total=remaining),
        ) as response:
          response.raise_for_status()
          return await response.read()
      except (
          asyncio.TimeoutError,
          aiohttp.ServerDisconnectedError,
          aiohttp.ClientConnectionError,
      ) as error:
        last_error = error
        await self._reset_owned_session()
        remaining = deadline - loop.time()
        if remaining <= 0:
          raise VoicevoxGenerationError(
            attempts, loop.time() - started_at, error
          ) from None
        delay = min(
          retry_delay,
          self.MAX_RETRY_DELAY_SECONDS,
          remaining,
        )
        await asyncio.sleep(delay)
        retry_delay = min(
          retry_delay * 2,
          self.MAX_RETRY_DELAY_SECONDS,
        )

  async def _reset_owned_session(self) -> None:
    """一時切断後に、このClientが所有するHTTPセッションを作り直す。"""
    if not self._owns_session or self._session is None:
      return
    session = self._session
    self._session = None
    if not getattr(session, "closed", False):
      await session.close()

  async def close(self) -> None:
    if (
        self._owns_session
        and self._session is not None
        and not getattr(self._session, "closed", False)
    ):
      await self._session.close()

