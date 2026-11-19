"""VOICEVOXから少しずつ届く音声を、WAVファイルに書きながら読ませる。

`/streaming_synthesis`は音声を合成しながら少しずつ返すので、全体が届く前に
再生を始められる。届いた分はすぐWAVファイルへ書き、再生する側（FFmpegへ
データを渡すdiscord.pyのスレッド）は`open_reader()`で開いた読み手から、
書かれた分だけを読む。まだ届いていない分は、届くか終わるまで待つ。

書く側はイベントループで動き、読む側は別のスレッドで動くので、
状態は`threading.Condition`で守る。
"""

import asyncio
import os
import threading


class StreamingAudio:
  """書きかけのWAVファイルと、どこまで書いたか・書き終えたかの状態。"""

  def __init__(self, path: str):
    self.path = path
    self._condition = threading.Condition()
    self._size = 0
    self._finished = False
    self._succeeded = False
    self._file = None
    self._done = asyncio.Event()
    # 記録用（/synthesis-log の first）。時刻はイベントループの時計（loop.time()）
    self.received_at: float | None = None  # メッセージを受け取った時刻
    self.wait_seconds = 0.0                # 生成の順番を待った時間
    self.enqueued_at: float | None = None  # 再生のキューに入れた時刻
    # 再生の順番（同じサーバーの前の読み上げが終わるの）を待った時間。再生を始めたら決まる
    self.playback_wait_seconds: float | None = None
    self.first_seconds: float | None = None
    self._record = None

  @classmethod
  def completed(cls, path: str) -> "StreamingAudio":
    """書き終えたファイルから作る（ストリーミングしなかったときなど）。"""
    audio = cls(path)
    audio._size = os.path.getsize(path)
    audio._finished = True
    audio._succeeded = True
    audio._done.set()
    return audio

  @property
  def size(self) -> int:
    with self._condition:
      return self._size

  @property
  def finished(self) -> bool:
    with self._condition:
      return self._finished

  @property
  def succeeded(self) -> bool:
    """最後まで書き終えたか。途中で切れたときと、まだ書いているときはFalse。"""
    with self._condition:
      return self._finished and self._succeeded

  @property
  def total_wait_seconds(self) -> float:
    """待った時間の合計（生成の順番待ち＋再生の順番待ち）。/synthesis-log の wait。"""
    return self.wait_seconds + (self.playback_wait_seconds or 0.0)

  def attach_record(self, record) -> None:
    """生成の記録を結び付ける。すでに分かっている待ち時間とfirstを書き込む。"""
    self._record = record
    self._update_record()

  def _update_record(self) -> None:
    record = self._record
    if record is None:
      return
    record.wait_seconds = self.total_wait_seconds
    record.playback_wait_seconds = self.playback_wait_seconds
    if self.first_seconds is not None:
      record.first_seconds = self.first_seconds

  def mark_enqueued(self, now: float) -> None:
    """再生のキューに入れた時刻を覚える（最初の1回だけ）。"""
    if self.enqueued_at is None:
      self.enqueued_at = now

  def mark_playback_started(self, now: float) -> None:
    """再生を始めた時刻から、再生の順番を待った時間を決める（最初の1回だけ）。"""
    if self.playback_wait_seconds is not None or self.enqueued_at is None:
      return
    self.playback_wait_seconds = max(0.0, now - self.enqueued_at)
    self._update_record()

  def mark_played(self, now: float) -> None:
    """最初の音を再生した時刻から、firstを決める（最初の1回だけ）。

    firstは、メッセージを受け取ってから最初の音を再生するまでの時間から、
    待った時間（生成の順番待ちと、同じサーバーの前の読み上げが終わるのを待った
    再生の順番待ち）を除いたもの。メッセージから作っていない音声
    （入退室通知など）は`received_at`がないので決めない。
    """
    if self.first_seconds is not None or self.received_at is None:
      return
    self.first_seconds = max(0.0, now - self.received_at - self.total_wait_seconds)
    self._update_record()

  def open_for_write(self) -> None:
    directory = os.path.dirname(self.path)
    if directory:
      os.makedirs(directory, exist_ok=True)
    self._file = open(self.path, "wb")

  def write(self, data: bytes) -> None:
    """届いたデータをファイルの末尾へ書き、待っている読み手を起こす。"""
    if not data:
      return
    if self._file is None:
      raise RuntimeError("open_for_write()を先に呼んでください")
    self._file.write(data)
    self._file.flush()
    with self._condition:
      self._size += len(data)
      self._condition.notify_all()

  def finish(self, succeeded: bool = True) -> None:
    """書き終えたことにする。途中で切れたときは`succeeded=False`にする。"""
    if self._file is not None:
      try:
        self._file.close()
      finally:
        self._file = None
    with self._condition:
      if self._finished:
        return
      self._finished = True
      self._succeeded = succeeded
      self._condition.notify_all()
    self._done.set()

  async def wait(self) -> bool:
    """書き終えるまで待ち、最後まで書けたかを返す。"""
    await self._done.wait()
    return self.succeeded

  def open_reader(self) -> "StreamingAudioReader":
    return StreamingAudioReader(self)


class StreamingAudioReader:
  """書きかけのファイルを、書かれた分だけ読む。

  discord.pyはFFmpegへデータを渡すスレッドで`read()`を呼び、空のバイト列が
  返ったら入力の終わりとして扱う。そのため、まだ届いていない分は待ち、
  書き終えたら残りを読み切ってから空を返す。再生を止めたときは`close()`で
  待っている`read()`を起こし、スレッドを終わらせる。
  """

  # 届くのが止まったまま、これ以上待たない（スレッドを残さないため）
  IDLE_TIMEOUT_SECONDS = 30.0

  def __init__(self, audio: StreamingAudio):
    self._audio = audio
    self._file = open(audio.path, "rb")
    self._position = 0
    self._closing = False
    # ファイルを閉じる処理と読む処理が重ならないようにする
    self._io_lock = threading.Lock()

  def readable(self) -> bool:
    return True

  def read(self, size: int = -1) -> bytes:
    audio = self._audio
    with audio._condition:
      while (
          not self._closing
          and self._position >= audio._size
          and not audio._finished
      ):
        if not audio._condition.wait(timeout=self.IDLE_TIMEOUT_SECONDS):
          break
      if self._closing:
        return b""
      available = audio._size - self._position
    if available <= 0:
      return b""
    if size is not None and size >= 0:
      available = min(size, available)
    with self._io_lock:
      if self._file is None:
        return b""
      data = self._file.read(available)
    self._position += len(data)
    return data

  def close(self) -> None:
    with self._audio._condition:
      self._closing = True
      self._audio._condition.notify_all()
    with self._io_lock:
      if self._file is not None:
        self._file.close()
        self._file = None

  @property
  def closed(self) -> bool:
    return self._file is None
