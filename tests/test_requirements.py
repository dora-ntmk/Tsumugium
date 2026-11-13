"""requirements.txt の版の決まりのテスト。"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def requirement_lines():
  lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
  return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


class RequirementsTest(unittest.TestCase):
  def test_discord_py_stays_below_2_8(self):
    # davey とセットで上げるため、discord.py にも上限を付けておく
    [line] = [line for line in requirement_lines() if re.match(r"discord\.py\b", line, re.IGNORECASE)]
    self.assertIn("<2.8", line)

  def test_davey_stays_below_0_2(self):
    # davey 0.2 と discord.py 2.7.1 の組み合わせでは音声が聞こえないため、0.1.x に留める
    [line] = [line for line in requirement_lines() if re.match(r"davey\b", line, re.IGNORECASE)]
    self.assertIn("<0.2", line)


if __name__ == "__main__":
  unittest.main()
