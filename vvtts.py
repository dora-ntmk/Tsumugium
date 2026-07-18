"""既存のVvTTS APIを保つVOICEVOXクライアント互換モジュール。"""

import json

from clients.voicevox_client import VoicevoxClient
from config import TMP_DIR


def edit_query(
    res_json,
    speed: float,
    pitch: float,
    intonation: float,
    volume: float,
):
  """v3.4以前から公開しているクエリ編集ヘルパー。"""
  try:
    if res_json is None:
      raise ValueError("res_json cannot be None")
    if speed is None:
      raise ValueError("speed cannot be None")
    if pitch is None:
      raise ValueError("pitch cannot be None")
    if intonation is None:
      raise ValueError("intonation cannot be None")
    if volume is None:
      raise ValueError("volume cannot be None")
    res_json["speedScale"] = speed
    res_json["pitchScale"] = pitch
    res_json["intonationScale"] = intonation
    res_json["volumeScale"] = volume
    return json.dumps(res_json)
  except ValueError as e:
    print(e)
    return None


class VvTTS(VoicevoxClient):
  def __init__(self, url: str = "http://127.0.0.1:50021", **kwargs):
    kwargs.setdefault("tmp_dir", TMP_DIR)
    super().__init__(url, **kwargs)
