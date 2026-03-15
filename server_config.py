import json
import os

DEFAULTS = {
    "TextTarget": None,
    "VoiceTarget": None,
    "Speaker": 8,
    "Volume": 100,
    "MaxChar": 0,
    "AutoJoin": False,
    "JoinNotice": False,
}

_TYPE_VALIDATORS = {
    "TextTarget":  (lambda v: v is None or (isinstance(v, int) and v > 0)),
    "VoiceTarget": (lambda v: v is None or (isinstance(v, int) and v > 0)),
    "Speaker":     (lambda v: isinstance(v, int) and v >= 0),
    "Volume":      (lambda v: isinstance(v, int) and 0 <= v <= 100),
    "MaxChar":     (lambda v: isinstance(v, int) and v >= 0),
    "AutoJoin":    (lambda v: isinstance(v, bool)),
    "JoinNotice":  (lambda v: isinstance(v, bool)),
}


class ServerConfig:
    def __init__(self, path: str = "config.json"):
        self.path = path
        self._data: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"config.json の読み込みに失敗しました（空で初期化します）: {e}")
                self._data = {}

    def _save(self):
        with open(self.path, mode="w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _guild_key(self, guild_id: int) -> str:
        return str(guild_id)

    def init_guild(self, guild_id: int):
        key = self._guild_key(guild_id)
        if key not in self._data:
            self._data[key] = dict(DEFAULTS)
            self._save()

    def get(self, guild_id: int, key: str):
        if key not in DEFAULTS:
            raise KeyError(f"不明な設定キー: {key}")
        return self._data.get(self._guild_key(guild_id), {}).get(key, DEFAULTS[key])

    def set(self, guild_id: int, key: str, value):
        if key not in DEFAULTS:
            raise KeyError(f"不明な設定キー: {key}")
        if not _TYPE_VALIDATORS[key](value):
            raise ValueError(f"{key} に無効な値です: {value!r}")
        key_str = self._guild_key(guild_id)
        if key_str not in self._data:
            self._data[key_str] = {}
        self._data[key_str][key] = value
        self._save()

    def get_all(self, guild_id: int) -> dict:
        return {**DEFAULTS, **self._data.get(self._guild_key(guild_id), {})}

    def reset(self, guild_id: int, key: str):
        if key not in DEFAULTS:
            raise KeyError(f"不明な設定キー: {key}")
        key_str = self._guild_key(guild_id)
        if key_str in self._data and key in self._data[key_str]:
            del self._data[key_str][key]
            self._save()

    def volume_to_vvtts(self, guild_id: int) -> float:
        return self.get(guild_id, "Volume") / 100.0
