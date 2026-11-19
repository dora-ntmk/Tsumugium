"""VOICEVOX HTTP APIへの非同期アクセスを担当する。"""

import asyncio
import json
import os
from typing import Any

from models.generation_record import SKIP_EMPTY
from models.streaming_audio import StreamingAudio
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


class _GenerationTiming:
  """生成1回分の、順番が来た時刻・試した回数・ストリーミングしたか（記録用）。"""

  __slots__ = ("acquired_at", "attempts", "streamed")

  def __init__(self):
    self.acquired_at: float | None = None
    self.attempts = 0
    self.streamed = False


class _SynthesisStart:
  """音声の最初の部分。`response`があれば、残りはそこから続けて受け取る。"""

  __slots__ = ("data", "response")

  def __init__(self, data: bytes, response=None):
    self.data = data
    self.response = response


class VoicevoxClient:
  RETRY_WINDOW_SECONDS = 30.0
  MAX_RETRY_DELAY_SECONDS = 5.0
  # /streaming_synthesisで一度に作る音声の長さ（秒）。短いほど最初の音は早く届くが、
  # 区切りごとに前後の余白も作るので、全体の生成は遅くなる
  STREAM_SEGMENT_SECONDS = 1.0
  # ストリーミングの残りが、この秒数届かなければ打ち切る（全体の時間は縛らない）
  STREAM_READ_TIMEOUT_SECONDS = 15.0

  def __init__(
      self,
      url: str = "http://127.0.0.1:50021",
      *,
      tmp_dir: str = "tmp",
      session: Any = None,
      error_notifier=None,
      generation_log=None,
      streaming_enabled: bool = True,
  ):
    self.url = url.rstrip("/")
    self.tmp_dir = tmp_dir
    self._session = session
    self._owns_session = session is None
    self._generation_lock = asyncio.Lock()
    self.error_notifier = ensure_error_notifier(error_notifier)
    # 生成の記録（GenerationLogService）。Noneなら記録しない
    self.generation_log = generation_log
    # Falseなら/streaming_synthesisを使わない
    self.streaming_enabled = streaming_enabled
    # エンジンが/streaming_synthesisを持たないと分かったらFalseにする
    self._streaming_supported = True
    # ストリーミングに対応していないと分かった話者
    self._non_streaming_speakers: set[int] = set()
    # 裏で受け取っているストリーミングの残り
    self._stream_tasks: set[asyncio.Task] = set()

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

  async def get_speakers(self) -> list[dict]:
    """VOICEVOXの /speakers からキャラクターとスタイルの一覧を取得する。"""
    import aiohttp

    session = await self._get_session()
    async with session.get(
        f"{self.url}/speakers",
        timeout=aiohttp.ClientTimeout(total=10),
    ) as response:
      response.raise_for_status()
      payload = await response.json()
    if not isinstance(payload, list):
      raise RuntimeError("VOICEVOX /speakers returned an unexpected response")
    return payload

  INITIALIZE_TIMEOUT_SECONDS = 120.0

  async def initialize_speaker(self, speaker: int) -> None:
    """話者の声のモデルをVOICEVOXに読み込ませる。読み込み済みならすぐ終わる。"""
    import aiohttp

    session = await self._get_session()
    async with session.post(
        f"{self.url}/initialize_speaker",
        params={"speaker": speaker, "skip_reinit": "true"},
        timeout=aiohttp.ClientTimeout(total=self.INITIALIZE_TIMEOUT_SECONDS),
    ) as response:
      response.raise_for_status()

  async def warm_up_speaker(self, speaker: int) -> float | None:
    """起動直後の最初の読み上げが遅れないよう、話者のモデルを前もって読み込む。

    VOICEVOXは話者を最初に使うときにモデルを読み込むため、何もしないと
    起動後の最初のメッセージがその待ち時間を受ける。合成用のロックは取らず、
    失敗しても表示するだけにする（最初の合成が遅くなるだけで済むため）。
    かかった秒数を返し、失敗したときはNoneを返す。
    """
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    try:
      await self.initialize_speaker(speaker)
    except asyncio.CancelledError:
      raise
    except Exception as error:
      print(
        f"VOICEVOX話者{speaker}の事前読み込みに失敗しました: "
        f"{type(error).__name__}: {error}"
      )
      return None
    elapsed = loop.time() - started_at
    print(f"VOICEVOX話者{speaker}の事前読み込みが完了しました: {elapsed:.1f}秒")
    return elapsed

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
  ) -> StreamingAudio | None:
    """音声を作り、WAVファイルへ書きながら返す。作れなかったときはNoneを返す。

    `/streaming_synthesis`が使えるときは、最初の音が届いた時点で返し、
    残りは裏で受け取ってファイルへ書き足す（`StreamingAudio.wait()`で
    書き終えるのを待てる）。使えないエンジン・話者では`/synthesis`で
    全体を作ってから返す。
    """
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    timing = _GenerationTiming()
    try:
      if msg is None:
        raise ValueError("msg cannot be None")
      if guildid is None:
        raise ValueError("guildid cannot be None")
      if msgid is None:
        raise ValueError("msgid cannot be None")
      if not msg.strip():
        # 空の文章はVOICEVOXへ送らない（無音の音声を作り、生成の順番も待たせるため）
        self._record_skip_empty()
        return None
      path = os.path.join(self.tmp_dir, f"{guildid}-{msgid}.wav")
      await self._acquire_turn(timing)
    except VoicevoxGenerationError as e:
      self._report_failure(e, msg, speaker, started_at, timing, guildid, msgid)
      return None
    except Exception as e:
      self._report_failure(e, msg, speaker, started_at, timing, guildid, msgid)
      return None

    # ここから先は生成の順番を持っている。最後まで受け取ったら手放す
    handed_over = False
    try:
      result = await self._start_with_retry(
        msg,
        speaker,
        speed,
        pitch,
        intonation,
        volume,
        # 期限は順番が来てから数える（ほかの生成を待った時間は数えない）
        deadline=timing.acquired_at + self.RETRY_WINDOW_SECONDS,
        started_at=timing.acquired_at,
        timing=timing,
      )
      audio = StreamingAudio(path)
      audio.wait_seconds = timing.acquired_at - started_at
      try:
        audio.open_for_write()
        audio.write(result.data)
      except BaseException:
        audio.finish(succeeded=False)
        if result.response is not None:
          result.response.release()
        raise
      if result.response is None:
        audio.finish()
        audio.attach_record(self._record(msg, speaker, started_at, timing))
        return audio
      timing.streamed = True
      response = result.response
      task = asyncio.create_task(
        self._receive_rest(
          response, audio, msg, speaker, started_at, timing, guildid, msgid,
        )
      )
      self._stream_tasks.add(task)

      def cleanup_if_never_ran(done_task):
        self._stream_tasks.discard(done_task)
        # 動き出す前に止められたタスクは後始末をしないので、ここで行う
        if not audio.finished:
          response.release()
          audio.finish(succeeded=False)
          self._generation_lock.release()

      task.add_done_callback(cleanup_if_never_ran)
      handed_over = True
      return audio
    except Exception as e:
      self._report_failure(e, msg, speaker, started_at, timing, guildid, msgid)
      return None
    finally:
      if not handed_over:
        self._generation_lock.release()

  async def _receive_rest(
      self, response, audio, msg, speaker, started_at, timing, guildid, msgid
  ) -> None:
    """ストリーミングの残りを受け取ってファイルへ書き足し、生成の順番を手放す。"""
    import aiohttp

    succeeded = False
    try:
      while True:
        chunk = await response.content.readany()
        if not chunk:
          break
        audio.write(chunk)
      succeeded = True
    except asyncio.CancelledError:
      raise
    except Exception as e:
      if isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError)):
        await self._reset_owned_session()
      self._report_failure(
        e, msg, speaker, started_at, timing, guildid, msgid,
        streaming=True, audio=audio,
      )
    finally:
      response.release()
      audio.finish(succeeded=succeeded)
      self._generation_lock.release()
    if succeeded:
      audio.attach_record(self._record(msg, speaker, started_at, timing))

  def _report_failure(
      self, error, msg, speaker, started_at, timing, guildid, msgid,
      *, streaming=False, audio=None,
  ) -> None:
    context = "VOICEVOX streaming" if streaming else "VOICEVOX generation"
    if isinstance(error, VoicevoxGenerationError):
      record = self._record(msg, speaker, started_at, timing, error=error.last_error)
      if audio is not None:
        audio.attach_record(record)
      self.error_notifier.report_exception(
        error,
        context,
        {
          "attempts": error.attempts,
          "elapsed_seconds": f"{error.elapsed:.1f}",
          "guild_id": guildid,
          "last_error": f"{type(error.last_error).__name__}: {error.last_error}",
          "message_id": msgid,
          "speaker": speaker,
        },
      )
      return
    record = self._record(msg, speaker, started_at, timing, error=error)
    if audio is not None:
      audio.attach_record(record)
    self.error_notifier.report_exception(
      error,
      context,
      {
        "guild_id": guildid,
        "message_id": msgid,
        "speaker": speaker,
      },
    )

  def _record_skip_empty(self) -> None:
    if self.generation_log is not None:
      self.generation_log.record_skip(SKIP_EMPTY)

  def _record(self, msg, speaker, started_at, timing, *, error=None):
    """生成の結果を記録し、その記録を返す（記録しないときはNone）。

    待ち時間と生成時間は、順番が来た時刻で分ける。文章とサーバーは記録しない（文字数だけ）。
    """
    if self.generation_log is None:
      return None
    now = asyncio.get_running_loop().time()
    acquired_at = timing.acquired_at if timing.acquired_at is not None else now
    values = {
      "chars": len(msg) if isinstance(msg, str) else 0,
      "speaker": speaker,
      "wait_seconds": acquired_at - started_at,
      "synth_seconds": now - acquired_at,
      "attempts": timing.attempts,
      "streamed": timing.streamed,
    }
    if error is None:
      return self.generation_log.record_success(**values)
    return self.generation_log.record_failure(**values, reason=type(error).__name__)

  async def _acquire_turn(self, timing) -> None:
    """生成の順番（全サーバーで1件ずつ）を待つ。

    待つ時間は期限に数えない。前の生成は、それぞれ順番が来てからの期限と、
    ストリーミングの届かない時間の上限で終わるので、待ちが止まることはない。
    """
    await self._generation_lock.acquire()
    timing.acquired_at = asyncio.get_running_loop().time()

  def _can_stream(self, speaker) -> bool:
    return (
      self.streaming_enabled
      and self._streaming_supported
      and speaker not in self._non_streaming_speakers
    )

  async def _start_with_retry(
      self,
      msg,
      speaker,
      speed,
      pitch,
      intonation,
      volume,
      *,
      deadline,
      started_at,
      timing,
  ) -> _SynthesisStart:
    """音声の最初の部分を受け取るまでを、VOICEVOXの一時的な切断に備えて期限内で再試行する。

    最初の音が届いたあとに切れたときは、もう再生を始めている場合があるので
    やり直さない（`_receive_rest()`が失敗として扱う）。
    """
    import aiohttp

    loop = asyncio.get_running_loop()
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
      timing.attempts = attempts
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
        body = json.dumps(query)

        if self._can_stream(speaker):
          started = await self._try_streaming(session, body, speaker, deadline)
          if started is not None:
            return started

        remaining = deadline - loop.time()
        if remaining <= 0:
          raise asyncio.TimeoutError("VOICEVOX generation deadline exceeded")
        async with session.post(
            f"{self.url}/synthesis",
            headers={"Content-Type": "application/json"},
            params={"speaker": speaker},
            data=body,
            timeout=aiohttp.ClientTimeout(total=remaining),
        ) as response:
          response.raise_for_status()
          return _SynthesisStart(await response.read())
      except (
          asyncio.TimeoutError,
          aiohttp.ServerDisconnectedError,
          aiohttp.ClientConnectionError,
          aiohttp.ClientPayloadError,
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

  async def _try_streaming(
      self, session, body, speaker, deadline
  ) -> _SynthesisStart | None:
    """`/streaming_synthesis`を始め、最初の部分が届いたら返す。

    エンジンが`/streaming_synthesis`を持たないとき（404・405）と、話者が
    ストリーミングに対応していないとき（422）はNoneを返し、以後は
    `/synthesis`を使う。
    """
    import aiohttp

    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
      raise asyncio.TimeoutError("VOICEVOX generation deadline exceeded")
    response = None

    async def start() -> _SynthesisStart | None:
      nonlocal response
      # 期限（順番が来てから30秒）で縛るのは最初の音が届くまで。残りは長い文章ほど
      # 時間がかかるので、全体の時間ではなく、届かない時間だけで打ち切る
      response = await session.post(
        f"{self.url}/streaming_synthesis",
        headers={"Content-Type": "application/json"},
        params={
          "speaker": speaker,
          "segment_length": self.STREAM_SEGMENT_SECONDS,
        },
        data=body,
        timeout=aiohttp.ClientTimeout(
          total=None,
          sock_connect=remaining,
          sock_read=self.STREAM_READ_TIMEOUT_SECONDS,
        ),
      )
      if response.status in (404, 405):
        self._streaming_supported = False
        print(
          "VOICEVOXが/streaming_synthesisに対応していないため、"
          "/synthesisで音声を作ります"
        )
        response.release()
        return None
      if response.status == 422:
        self._non_streaming_speakers.add(speaker)
        print(
          f"VOICEVOX話者{speaker}はストリーミングに対応していないため、"
          "/synthesisで音声を作ります"
        )
        response.release()
        return None
      response.raise_for_status()
      first = await response.content.readany()
      if response.content.at_eof():
        response.release()
        return _SynthesisStart(first)
      return _SynthesisStart(first, response)

    try:
      return await asyncio.wait_for(start(), timeout=remaining)
    except BaseException:
      if response is not None:
        response.release()
      raise

  async def _reset_owned_session(self) -> None:
    """一時切断後に、このClientが所有するHTTPセッションを作り直す。"""
    if not self._owns_session or self._session is None:
      return
    session = self._session
    self._session = None
    if not getattr(session, "closed", False):
      await session.close()

  async def close(self) -> None:
    for task in list(self._stream_tasks):
      task.cancel()
    if self._stream_tasks:
      await asyncio.gather(*self._stream_tasks, return_exceptions=True)
    if (
        self._owns_session
        and self._session is not None
        and not getattr(self._session, "closed", False)
    ):
      await self._session.close()
