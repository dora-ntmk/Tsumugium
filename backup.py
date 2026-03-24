"""
ファイル名：backup.py
作者：どら
説明：SQLite データベース自動バックアップモジュール。
      設定した時刻・間隔で DB ファイルを非同期にバックアップし、
      古いバックアップを自動削除してディスク使用量を管理する。
依存関係：なし
"""
import asyncio
import os
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path

from config import BACKUP_DIR, BACKUP_TIMES, BACKUP_INTERVAL_DAYS, BACKUP_KEEP

# バックアップ判定の基準日（固定）
_EPOCH = date(2000, 1, 1)


def parse_backup_times(times_str: str) -> list[tuple[int, int]]:
  """
  "03:00,15:00" -> [(3, 0), (15, 0)]
  空文字列 -> [] (バックアップ無効)
  """
  result = []
  for part in times_str.split(","):
    part = part.strip()
    if not part:
      continue
    try:
      h, m = part.split(":")
      result.append((int(h), int(m)))
    except ValueError:
      print(f"[backup] 無効な時刻フォーマットをスキップ: '{part}' (HH:MM 形式で指定してください)")
  return result


def next_run_time(times: list[tuple[int, int]]) -> datetime:
  """現在時刻から最も近い次の実行時刻を返す。"""
  now = datetime.now()
  candidates = []
  for h, m in times:
    today_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if today_run > now:
      candidates.append(today_run)
    # 翌日の同時刻も候補に追加
    candidates.append(today_run + timedelta(days=1))
  return min(candidates)


def is_backup_day(interval_days: int) -> bool:
  """今日がバックアップ実行日かどうかを返す。"""
  if interval_days <= 1:
    return True
  elapsed = (date.today() - _EPOCH).days
  return elapsed % interval_days == 0


def backup_db(db_path: str, backup_dir: str) -> str | None:
  """
  SQLite データベースをバックアップし、保存先パスを返す。
  失敗時は None を返す。
  """
  src = Path(db_path)
  if not src.exists():
    print(f"[backup] バックアップ対象が見つかりません: {db_path}")
    return None

  dest_dir = Path(backup_dir)
  dest_dir.mkdir(parents=True, exist_ok=True)

  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  dest_path = dest_dir / f"backup_{src.stem}_{timestamp}.db"

  try:
    src_conn = sqlite3.connect(str(src), timeout=30)
    dest_conn = sqlite3.connect(str(dest_path), timeout=30)
    with dest_conn:
      src_conn.backup(dest_conn)
    src_conn.close()
    dest_conn.close()
    return str(dest_path)
  except Exception as e:
    print(f"[backup] バックアップ失敗 ({db_path}): {e}")
    # 不完全なファイルを削除
    if dest_path.exists():
      dest_path.unlink()
    return None


def rotate_backups(db_name: str, backup_dir: str, keep: int) -> None:
  """古いバックアップファイルを削除して keep 件に維持する。"""
  dest_dir = Path(backup_dir)
  pattern = f"backup_{db_name}_*.db"
  files = sorted(dest_dir.glob(pattern))  # ファイル名のタイムスタンプ順
  excess = files[:max(0, len(files) - keep)]
  for f in excess:
    try:
      f.unlink()
      print(f"[backup] 古いバックアップを削除: {f.name}")
    except Exception as e:
      print(f"[backup] 削除失敗 ({f.name}): {e}")


def run_backup(db_paths: list[str], backup_dir: str, keep: int) -> None:
  """複数の DB をバックアップし、ローテーションを行う。"""
  print(f"[backup] バックアップ開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
  for db_path in db_paths:
    result = backup_db(db_path, backup_dir)
    if result:
      print(f"[backup] 完了: {result}")
      db_name = Path(db_path).stem
      rotate_backups(db_name, backup_dir, keep)
  print("[backup] バックアップ終了")


async def backup_scheduler(db_paths: list[str]) -> None:
  """バックアップスケジューラー本体。on_ready から asyncio.create_task で起動する。"""
  times = parse_backup_times(BACKUP_TIMES)
  if not times:
    print("[backup] BACKUP_TIMES が未設定のためバックアップは無効です")
    return

  try:
    interval_days = int(BACKUP_INTERVAL_DAYS)
  except ValueError:
    print(f"[backup] BACKUP_INTERVAL_DAYS の値が不正です: '{BACKUP_INTERVAL_DAYS}' (整数で指定してください)")
    interval_days = 1

  try:
    keep = int(BACKUP_KEEP)
  except ValueError:
    print(f"[backup] BACKUP_KEEP の値が不正です: '{BACKUP_KEEP}' (整数で指定してください)")
    keep = 7

  print(f"[backup] スケジューラー起動 (実行時刻: {BACKUP_TIMES}, {interval_days}日おき, {keep}世代保持)")

  while True:
    next_time = next_run_time(times)
    wait_sec = (next_time - datetime.now()).total_seconds()
    print(f"[backup] 次回実行: {next_time.strftime('%Y-%m-%d %H:%M:%S')} ({wait_sec:.0f}秒後)")
    await asyncio.sleep(max(0, wait_sec))

    if is_backup_day(interval_days):
      run_backup(db_paths, BACKUP_DIR, keep)
    else:
      print(f"[backup] 本日はバックアップ対象日ではありません (BACKUP_INTERVAL_DAYS={interval_days})")


def start(db_paths: list[str]) -> asyncio.Task:
  """バックアップスケジューラーを非同期タスクとして起動する。"""
  return asyncio.create_task(backup_scheduler(db_paths))
