"""テスト共通の部品。

決まりごと（AGENTS.md「開発用テストの運用」）:
- 実在するDiscordのID・トークン・サーバー名・ユーザー名と、実際の辞書の登録内容を使わない。
- DBはテストごとに一時ディレクトリへ作り、本番DBのコピーは使わない。
- ネットワークに接続しない。
"""

import os
import tempfile
import unittest


# 明らかなダミーと分かるID。実在するIDは使わない。
DUMMY_GUILD_ID = 111111111111111111
DUMMY_OTHER_GUILD_ID = 111111111111111112
DUMMY_USER_ID = 222222222222222222
DUMMY_OTHER_USER_ID = 222222222222222223
DUMMY_BOT_USER_ID = 222222222222222299
DUMMY_TEXT_CHANNEL_ID = 333333333333333333
DUMMY_VOICE_CHANNEL_ID = 444444444444444444
DUMMY_ROLE_ID = 555555555555555555
DUMMY_SOUND_ID = "666666666666666666"

DUMMY_TOKEN = "dummy-token-for-tests"


class TempDirTestCase(unittest.TestCase):
  """テストごとに新しい一時ディレクトリを用意する。"""

  def setUp(self) -> None:
    super().setUp()
    self._tmp = tempfile.TemporaryDirectory(prefix="tsumugium-test-")
    self.addCleanup(self._tmp.cleanup)
    self.tmp_dir = self._tmp.name

  def tmp_path(self, *parts: str) -> str:
    """一時ディレクトリの中のパスを返す。途中のディレクトリは作る。"""
    path = os.path.join(self.tmp_dir, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path
