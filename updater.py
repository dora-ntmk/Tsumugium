"""
ファイル名：updater.py
作者：どらじゃない人
説明：GitHub Releases API を利用した自動アップデート確認モジュール。
      定期的に最新バージョンを確認し、新バージョンがあれば該当サーバーに通知する。
依存関係：aiohttp, asyncio
"""

import asyncio
import re
import aiohttp
from config import VERSION, GITHUB_REPO, AUTO_UPDATE_CHECK_ENABLED, UPDATE_CHECK_INTERVAL
from messages import build_embed


async def fetch_latest_version() -> str | None:
  """GitHub Releases API から最新バージョンタグを取得する。"""
  url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
  try:
    async with aiohttp.ClientSession() as session:
      async with session.get(url, timeout=10) as resp:
        if resp.status != 200:
          print(f"[updater] GitHub API エラー: HTTP {resp.status}")
          return None
        data = await resp.json()
        tag = data.get("tag_name", "")
        # "v3.0.8" → "3.0.8"
        return tag.lstrip("v")
  except asyncio.TimeoutError:
    print("[updater] GitHub API タイムアウト")
    return None
  except Exception as e:
    print(f"[updater] GitHub API エラー: {e}")
    return None


def is_newer_version(latest: str, current: str) -> bool:
  """セマンティックバージョン比較。latest > current なら True。"""

  def parse(v: str):
    parts = re.split(r"[._\-]", v)
    return [int(x) if x.isdigit() else x for x in parts]

  return parse(latest) > parse(current)


async def update_checker(client, server_config) -> None:
  """定期的にバージョン確認を行い、新バージョンがあれば該当サーバーに通知する。"""
  if not AUTO_UPDATE_CHECK_ENABLED:
    print("[updater] AUTO_UPDATE_CHECK_ENABLED=false のため無効")
    return

  print(f"[updater] 更新確認を開始 (リポジトリ: {GITHUB_REPO}, 間隔: {UPDATE_CHECK_INTERVAL}秒)")

  while True:
    latest = await fetch_latest_version()
    if latest and is_newer_version(latest, VERSION):
      print(f"[updater] 新バージョン検出: v{VERSION} → v{latest}")
      await notify_guilds(client, server_config, latest)
    else:
      print(f"[updater] 最新バージョンです (v{VERSION})" if latest else "[updater] バージョン確認不可")

    await asyncio.sleep(UPDATE_CHECK_INTERVAL)


async def notify_guilds(client, server_config, latest: str) -> None:
  """AutoUpdateCheck と AutoUpdate が有効な全サーバーに通知する。"""
  for guild in client.guilds:
    try:
      if not server_config.get(guild.id, "AutoUpdateCheck"):
        continue
      if not server_config.get(guild.id, "AutoUpdate"):
        continue

      text_target_id = server_config.get(guild.id, "TextTarget")
      if text_target_id is None:
        continue
      channel = guild.get_channel(text_target_id)
      if channel is None:
        continue
      lang = server_config.get(guild.id, "Language")
      await channel.send(embed=build_embed("updater.new_version", lang=lang, current=VERSION, latest=latest))
    except Exception as e:
      print(f"[updater] 通知失敗 (guild={guild.id}): {e}")


def start(client, server_config) -> asyncio.Task | None:
  if not AUTO_UPDATE_CHECK_ENABLED:
    return None
  return asyncio.create_task(update_checker(client, server_config))
