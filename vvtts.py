import requests
import json
import os


class VvTTS:
  def __init__(self, port: int = 50021):
    self.port = port

  async def generate(self, msg: str, guildid: int, msgid: int, speaker: int = 0):
    if msg is None:
      raise ValueError("msg cannot be None")
    elif guildid is None:
      raise ValueError("guildid cannot be None")
    elif msgid is None:
      raise ValueError("msgid cannot be None")
    try:
      res1 = requests.post(f"http://localhost:{self.port}/audio_query", params={"text": msg, "speaker": speaker})
      headers = {"content-type": "application/json"}
      res2 = requests.post(
        f"http://localhost:{self.port}/synthesis",
        headers=headers,
        params={"speaker": speaker},
        data=json.dumps(res1.json())
      )
      os.makedirs("tmp", exist_ok=True)
      path = f"./tmp/{guildid}-{msgid}.wav"
      with open(path, mode="wb") as f:
        f.write(res2.content)
        f.close()
      return path
    except Exception as e:
      print(e)