import requests
import json
import os

class VvTTS:
  def __init__(self, msg: str, guildid: int, msgid: int, speaker: int):
    self.msg = msg
    self.GuildId = guildid
    self.MsgId = msgid
    self.speaker = speaker

  @staticmethod
  def generate(msg: str = "", guildid: int = 0, msgid: int = 0, speaker: int = 0):
    if msg == "":
      raise ValueError("msg is empty")
    elif guildid == 0:
      raise ValueError("Guild id is empty")
    elif msgid == 0:
      raise ValueError("MsgId is empty")

    res1 = requests.post("http://localhost:50021/audio_query", params={"text": msg, "speaker": speaker})
    headers = {"content-type": "application/json"}
    res2 = requests.post("http://localhost:50021/synthesis", headers=headers, params={"speaker": speaker},
                         data=json.dumps(res1.json()))
    os.mkdir("tmp", exist_ok=True)
    with open(f"tmp/{guildid}-{msgid}.wav", mode="wb") as f:
      f.write(res2.content)
      f.close()