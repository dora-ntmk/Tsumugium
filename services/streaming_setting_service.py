"""VOICEVOXの/streaming_synthesisの設定（モード・基準の文字数・一度に作る長さ）を扱う。

設定は運営者が /synthesis set で決め、config.db の bot_settings 表に保存する（再起動しても残る）。
前の版で .env に書いていた値（VOICEVOX_STREAMING など）は、起動時に一度だけ取り込み、
以後 .env の値は使わない。保存していない項目は初期値（モードは hybrid、基準と長さは自動調整）を使う。
"""

from dataclasses import dataclass

from config import (
  DEFAULT_VOICEVOX_STREAMING,
  parse_stream_segment_seconds,
  parse_stream_threshold_chars,
  parse_streaming_mode,
)


MODE_CHOICES = ("off", "on", "hybrid")

KEY_MODE = "voicevox_streaming"
KEY_THRESHOLD = "voicevox_stream_threshold"
KEY_SEGMENT = "voicevox_stream_segment"
SETTING_KEYS = (KEY_MODE, KEY_THRESHOLD, KEY_SEGMENT)
# 前の版の .env の値を取り込み済みかの印
KEY_ENV_IMPORTED = "voicevox_streaming_env_imported"

ENV_MODE = "VOICEVOX_STREAMING"
ENV_THRESHOLD = "VOICEVOX_STREAM_THRESHOLD"
ENV_SEGMENT = "VOICEVOX_STREAM_SEGMENT"


@dataclass(frozen=True)
class StreamingSetting:
  """今の設定。"""

  mode: str
  threshold_chars: int
  segment_seconds: float
  effective_segment_seconds: float
  engine_supported: bool
  non_streaming_speakers: tuple[int, ...]


class StreamingSettingService:
  def __init__(self, repository, voicevox_client, legacy_env: dict | None = None):
    self.repository = repository
    self.voicevox_client = voicevox_client
    self.legacy_env = dict(legacy_env or {})
    self.mode = parse_streaming_mode(DEFAULT_VOICEVOX_STREAMING)
    self.threshold_chars = 0
    self.segment_seconds = 0.0

  # ---- 起動 ----

  def load(self) -> list[str]:
    """保存した設定を読み、VoicevoxClientへ適用する。取り込んだ .env の項目名を返す。"""
    imported = self._import_legacy_env()
    stored = self.repository.get_many(SETTING_KEYS)
    self.mode = parse_streaming_mode(stored.get(KEY_MODE), name="saved streaming mode")
    self.threshold_chars = parse_stream_threshold_chars(
      stored.get(KEY_THRESHOLD), name="saved stream threshold"
    )
    self.segment_seconds = parse_stream_segment_seconds(
      stored.get(KEY_SEGMENT), name="saved stream segment"
    )
    self._apply()
    return imported

  def _import_legacy_env(self) -> list[str]:
    """前の版の .env の値を、まだなら一度だけ保存する。

    モードの auto・空欄と、基準・長さの 0（自動調整）は保存せず、初期値を使う。
    """
    if self.repository.get(KEY_ENV_IMPORTED) is not None:
      return []
    values: dict[str, str] = {}
    imported: list[str] = []
    mode_text = (self.legacy_env.get(ENV_MODE) or "").strip()
    if mode_text and mode_text.lower() != "auto":
      values[KEY_MODE] = parse_streaming_mode(mode_text)
      imported.append(ENV_MODE)
    threshold = parse_stream_threshold_chars(self.legacy_env.get(ENV_THRESHOLD))
    if threshold > 0:
      values[KEY_THRESHOLD] = str(threshold)
      imported.append(ENV_THRESHOLD)
    segment = parse_stream_segment_seconds(self.legacy_env.get(ENV_SEGMENT))
    if segment > 0:
      values[KEY_SEGMENT] = repr(segment)
      imported.append(ENV_SEGMENT)
    values[KEY_ENV_IMPORTED] = "1"
    self.repository.set_many(values)
    if imported:
      print(
        f".env の {', '.join(imported)} を /synthesis の設定（config.db）へ移しました。"
        "以後 .env のこれらの値は使いません"
      )
    return imported

  # ---- 参照・変更 ----

  def current(self) -> StreamingSetting:
    client = self.voicevox_client
    return StreamingSetting(
      mode=self.mode,
      threshold_chars=self.threshold_chars,
      segment_seconds=self.segment_seconds,
      effective_segment_seconds=client.segment_seconds(),
      engine_supported=client.streaming_supported,
      non_streaming_speakers=tuple(sorted(client.non_streaming_speakers)),
    )

  def update(
      self,
      *,
      mode: str | None = None,
      threshold_chars: int | None = None,
      segment_seconds: float | None = None,
  ) -> StreamingSetting:
    """指定した項目だけを変え、保存して適用する。基準・長さの0は保存を消して自動調整に戻す。"""
    if mode is not None and mode not in MODE_CHOICES:
      raise ValueError(f"unknown streaming mode: {mode!r}")
    if threshold_chars is not None and threshold_chars < 0:
      raise ValueError("threshold must not be negative")
    if segment_seconds is not None and not segment_seconds >= 0:
      raise ValueError("segment must not be negative")

    store: dict[str, str] = {}
    remove: list[str] = []
    if mode is not None:
      store[KEY_MODE] = mode
    if threshold_chars is not None:
      if threshold_chars == 0:
        remove.append(KEY_THRESHOLD)
      else:
        store[KEY_THRESHOLD] = str(threshold_chars)
    if segment_seconds is not None:
      if segment_seconds == 0:
        remove.append(KEY_SEGMENT)
      else:
        store[KEY_SEGMENT] = repr(float(segment_seconds))
    if store:
      self.repository.set_many(store)
    if remove:
      self.repository.delete(remove)

    if mode is not None:
      self.mode = mode
    if threshold_chars is not None:
      self.threshold_chars = threshold_chars
    if segment_seconds is not None:
      self.segment_seconds = float(segment_seconds)
    self._apply()
    return self.current()

  def reset(self) -> StreamingSetting:
    """保存した設定を消し、初期値（hybrid・自動調整）に戻す。"""
    self.repository.delete(SETTING_KEYS)
    self.mode = parse_streaming_mode(DEFAULT_VOICEVOX_STREAMING)
    self.threshold_chars = 0
    self.segment_seconds = 0.0
    self._apply()
    return self.current()

  def _apply(self) -> None:
    self.voicevox_client.configure_streaming(
      self.mode, self.threshold_chars, self.segment_seconds
    )
