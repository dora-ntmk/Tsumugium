"""再生キューに格納する音声アイテム。"""

from dataclasses import dataclass

from models.streaming_audio import StreamingAudio


@dataclass(frozen=True, slots=True)
class TTSItem:
  path: str
  message_id: int | None = None
  # 書きかけの音声。あれば、書き終える前でも届いた分から再生を始める
  audio: StreamingAudio | None = None


@dataclass(frozen=True, slots=True)
class SoundboardItem:
  sound_id: str


AudioItem = TTSItem | SoundboardItem

