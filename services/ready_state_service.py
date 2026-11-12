"""起動の準備が終わったことを、インストーラーへ ready.json で知らせる。"""

import datetime
import json
import os
import tempfile


READY_FILE_NAME = "ready.json"


class ReadyStateService:
  """UPDATE_STATE_DIR が設定されているときだけ、ready.json を書き・消す。

  インストーラーは、起動の前にこのファイルを消し、Botが書き直すのを待つ。
  書けなかったときも、Botは止めない。
  """

  def __init__(self, state_dir, version, *, pid=None, now=None):
    self.state_dir = state_dir or ""
    self.version = version
    self._pid = pid
    self._now = now or (lambda: datetime.datetime.now(datetime.timezone.utc))

  @property
  def enabled(self) -> bool:
    return bool(self.state_dir)

  @property
  def path(self):
    return os.path.join(self.state_dir, READY_FILE_NAME) if self.enabled else None

  def mark_ready(self) -> bool:
    """ready.json を書く。書いたときは True を返す。"""
    if not self.enabled:
      return False
    data = {
      "version": self.version,
      "pid": self._pid if self._pid is not None else os.getpid(),
      "ready_at": self._now().isoformat(timespec="seconds"),
    }
    temp = None
    try:
      os.makedirs(self.state_dir, exist_ok=True)
      fd, temp = tempfile.mkstemp(dir=self.state_dir, prefix=".tmp-ready-", suffix=".json")
      with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)
        file.write("\n")
      os.chmod(temp, 0o644)
      os.replace(temp, self.path)
      temp = None
    except OSError as error:
      print(f"ready.json を書けませんでした: {error}")
      return False
    finally:
      if temp is not None:
        try:
          os.remove(temp)
        except OSError:
          pass
    return True

  def clear(self) -> None:
    if not self.enabled:
      return
    try:
      os.remove(self.path)
    except FileNotFoundError:
      pass
    except OSError as error:
      print(f"ready.json を消せませんでした: {error}")

  def close(self) -> None:
    """Bot終了時に呼ばれる。"""
    self.clear()
