import requests
import json
import os


class VvTTS:
  def __init__(self, msg: str, guildid: int, msgid: int, speaker: int = 0):
    if msg is None:
      raise ValueError("msg cannot be None")
    elif guildid is None:
      raise ValueError("guildid cannot be None")
    elif msgid is None:
      raise ValueError("msgid cannot be None")
    self.msg = msg
    self.GuildId = guildid
    self.MsgId = msgid
    self.speaker = speaker

  def generate(self):
    try:
      res1 = requests.post("http://localhost:50021/audio_query", params={"text": self.msg, "speaker": self.speaker})
      headers = {"content-type": "application/json"}
      res2 = requests.post(
        "http://localhost:50021/synthesis",
        headers=headers,
        params={"speaker": self.speaker},
        data=json.dumps(res1.json())
      )
      os.makedirs("tmp", exist_ok=True)
      path = f"./tmp/{self.GuildId}-{self.MsgId}.wav"
      with open(path, mode="wb") as f:
        f.write(res2.content)
        f.close()
      return path
    except Exception as e:
      print(e)