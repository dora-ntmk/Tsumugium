import requests
import json
import os


async def edit_query(
    res_json,
    speed: float,
    pitch: float,
    intonation: float,
    volume: float
):
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


class VvTTS:
  def __init__(self, url: str = "http://localhost:50021"):
    self.url = url

  async def generate(
      self,
      msg: str,
      guildid: int,
      msgid: int,
      speaker: int = 0,
      speed: float = 1.0,
      pitch: float = 0.0,
      intonation: float = 1.0,
      volume: float = 1.0
  ):
    try:
      if msg is None:
        raise ValueError("msg cannot be None")
      elif guildid is None:
        raise ValueError("guildid cannot be None")
      elif msgid is None:
        raise ValueError("msgid cannot be None")
      res1 = requests.post(f"{self.url}/audio_query", params={"text": msg, "speaker": speaker})
      res2 = await edit_query(res1.json(), speed, pitch, intonation, volume)
      res3 = requests.post(
        f"{self.url}/synthesis",
        headers={"content-type": "application/json"},
        params={"speaker": speaker},
        data=res2
      )
      os.makedirs("tmp", exist_ok=True)
      path = f"./tmp/{guildid}-{msgid}.wav"
      with open(path, mode="wb") as f:
        f.write(res3.content)
        f.close()
      return path
    except Exception as e:
      print(e)