"""ユーザー設定を使って読み上げ用の名前を解決する。"""


class UserReadingService:
  def __init__(self, user_config):
    self.user_config = user_config

  def get_reading(self, user) -> str:
    """設定済みの読み方を返し、未設定ならDiscordの表示名を返す。"""
    reading = self.user_config.get_user_reading(user.id)
    if reading is not None:
      return reading
    return user.display_name
