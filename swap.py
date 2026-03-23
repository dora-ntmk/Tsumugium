import os
import re

_CUSTOM_EMOJI_RE = re.compile(r'<a?:[\w/:%#$&?()~.=+\-]+:\d+>')
_STANDARD_EMOJI_RE = re.compile(r'^:([\w/:%#$&?()~.=+\-]+):$')

_SPOILER_RE      = re.compile(r'\|\|[\s\S]+?\|\|')
_STRIKE_RE       = re.compile(r'~~[\s\S]+?~~')
_CODE_BLOCK_RE   = re.compile(r'```[\s\S]+?```')
_INLINE_CODE_RE  = re.compile(r'`[^`]+`')
_TIMESTAMP_RE    = re.compile(r'<t:(\d+):[\s\S]>')
_MENTION_USER_RE = re.compile(r'<@!?(\d+)>')
_MENTION_CH_RE   = re.compile(r'<#(\d+)>')
_MENTION_ROLE_RE = re.compile(r'<@&(\d+)>')
_WWW_RE          = re.compile(r'[wWｗＷ]{2,}')

_URL_PATTERNS = [
  (re.compile(r'https://www.youtube[\w/:%#$&?()~.=+\-!@]+'), 'ユーチューブへのリンク'),
  (re.compile(r'https://youtu[\w/:%#$&?()~.=+\-!@]+'), 'ユーチューブへのリンク'),
  (re.compile(r'https://www.nicovideo[\w/:%#$&?()~.=+\-!@]+'), 'ニコニコへのリンク'),
  (re.compile(r'https://nico[\w/:%#$&?()~.=+\-!@]+'), 'ニコニコへのリンク'),
  (re.compile(r'https://twitter[\w/:%#$&?()~.=+\-!@]+'), 'ツイッターへのリンク'),
  (re.compile(r'https://x[\w/:%#$&?()~.=+\-!@]+'), 'エックスへのリンク'),
  (re.compile(r'https://www.instagram[\w/:%#$&?()~.=+\-!@]+'), 'インスタへのリンク'),
  (re.compile(r'https://www.facebook[\w/:%#$&?()~.=+\-!@]+'), 'フェイスブックへのリンク'),
  (re.compile(r'https://www.tiktok[\w/:%#$&?()~.=+\-!@]+'), 'ティックトックへのリンク'),
  (re.compile(r'https://discord[\w/:%#$&?()~.=+\-!@]+'), 'ディスコードないのリンク'),
  (re.compile(r'https://line[\w/:%#$&?()~.=+\-!@]+'), 'ラインへのリンク'),
  (re.compile(r'https://store.steampowered[\w/:%#$&?()~.=+\-!@]+'), 'スチームへのリンク'),
  (re.compile(r'https://www.ea[\w/:%#$&?()~.=+\-!@]+'), 'イーエーへのリンク'),
  (re.compile(r'https://www.origin[\w/:%#$&?()~.=+\-!@]+'), 'オリジンへのリンク'),
  (re.compile(r'https://store.epicgames[\w/:%#$&?()~.=+\-!@]+'), 'エピックゲームズへのリンク'),
  (re.compile(r'https://www.riotgames[\w/:%#$&?()~.=+\-!@]+'), 'ライアットゲームズへのリンク'),
  (re.compile(r'https://www.xbox[\w/:%#$&?()~.=+\-!@]+'), 'エックスボックスへのリンク'),
  (re.compile(r'https://www.amazon[\w/:%#$&?()~.=+\-!@]+'), 'アマゾンへのリンク'),
  (re.compile(r'https://item.rakuten[\w/:%#$&?()~.=+\-!@]+'), 'らくてんいちばへのリンク'),
  (re.compile(r'https://www.yodobashi[\w/:%#$&?()~.=+\-!@]+'), 'ヨドバシカメラへのリンク'),
  (re.compile(r'https://www.soundhouse[\w/:%#$&?()~.=+\-!@]+'), 'サウンドハウスへのリンク'),
  (re.compile(r'https://www.mapcamera[\w/:%#$&?()~.=+\-!@]+'), 'マップカメラへのリンク'),
  (re.compile(r'https://[\w/:%#$&?()~.=+\-!@]+aliexpress+[\w/:%#$&?()~.=+\-!@]+'), 'アリエクへのリンク'),
  (re.compile(r'http://abehiroshi.la.coocan.jp/+'), 'あべひろしのホームページへのリンク'),
  (re.compile(r'https://[\w/:%#$&?()~.=+\-!@]+'), 'リンク省略'),
  (re.compile(r'http://[\w/:%#$&?()~.=+\-!@]+'), 'リンク省略'),
  (re.compile(r'www\.\S+'), 'リンク省略'),
]

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff'}
_VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.webm', '.mkv', '.flv'}
_AUDIO_EXTS = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac'}


def _apply_dict(segments: list, mapping: dict) -> list:
  """Literal word replacements on non-replaced segments."""
  for word, read in mapping.items():
    if not word:
      continue
    pattern = re.escape(word)
    new_segments = []
    for seg_text, replaced in segments:
      if replaced:
        new_segments.append((seg_text, True))
        continue
      last_end = 0
      for m in re.finditer(pattern, seg_text):
        before = seg_text[last_end:m.start()]
        if before:
          new_segments.append((before, False))
        new_segments.append((read, True))
        last_end = m.end()
      remaining = seg_text[last_end:]
      if remaining:
        new_segments.append((remaining, False))
    segments = new_segments
  return segments


def _apply_regex(segments: list, pattern, repl_fn) -> list:
  """Regex replacements on non-replaced segments."""
  new_segments = []
  for seg_text, replaced in segments:
    if replaced:
      new_segments.append((seg_text, True))
      continue
    last_end = 0
    for m in re.finditer(pattern, seg_text):
      before = seg_text[last_end:m.start()]
      if before:
        new_segments.append((before, False))
      replacement = repl_fn(m) if callable(repl_fn) else repl_fn
      if replacement:
        new_segments.append((replacement, True))
      last_end = m.end()
    remaining = seg_text[last_end:]
    if remaining:
      new_segments.append((remaining, False))
  return new_segments


def preprocess_text(text: str, guild_id: int, conn, emoji_ja: dict, guild, attachments, mentions=None) -> tuple[str, list[tuple[int, int]]]:
  segments = [(text, False)]
  gid = str(guild_id)

  # -1. 優先辞書（URL処理より前に適用）
  cur = conn.execute(
    "SELECT word, reading FROM dict WHERE guild_id = ? AND is_priority = 1 AND reading IS NOT NULL ORDER BY added_at DESC",
    (gid,)
  )
  d = dict(cur.fetchall())
  if d:
    segments = _apply_dict(segments, d)

  # 0. URL処理（辞書より前に実行してURLを保護）
  for url_re, url_read in _URL_PATTERNS:
    segments = _apply_regex(segments, url_re, url_read + ',')

  # 0b. www pattern → N × わら（URL内の www は protected=True でスキップ済み）
  def _www_replace(m):
    return 'わら' * len(m.group(0)) + ','
  segments = _apply_regex(segments, _WWW_RE, _www_replace)

  # 1a. Spoilers
  segments = _apply_regex(segments, _SPOILER_RE, ',ネタバレ,')
  # 1b. Strikethrough
  segments = _apply_regex(segments, _STRIKE_RE, ',取り消し線,')
  # 1c. Code blocks
  segments = _apply_regex(segments, _CODE_BLOCK_RE, ',コードブロック,')
  # 1d. Inline code
  segments = _apply_regex(segments, _INLINE_CODE_RE, ',コードブロック,')
  # 1e. Time stamp
  segments = _apply_regex(segments, _TIMESTAMP_RE, ',タイムスタンプ,')

  # 1f. User mentions
  mention_map = {str(u.id): u.display_name for u in (mentions or [])}

  def _mention_user(m):
    uid = m.group(1)
    if uid in mention_map:
      return f'{mention_map[uid]}へのメンション,'
    if guild:
      member = guild.get_member(int(uid))
      if member:
        return f'{member.display_name}へのメンション,'
    return 'ユーザーへのメンション,'

  segments = _apply_regex(segments, _MENTION_USER_RE, _mention_user)

  # 1g. Channel links
  def _channel_link(m):
    if guild:
      ch = guild.get_channel(int(m.group(1)))
      if ch:
        return f'{ch.name}へのリンク,'
    return 'チャンネルへのリンク,'

  segments = _apply_regex(segments, _MENTION_CH_RE, _channel_link)

  # 1h. Role mentions
  def _mention_role(m):
    if guild:
      role = guild.get_role(int(m.group(1)))
      if role:
        return f'{role.name}へのメンション,'
    return 'ロールへのメンション,'

  segments = _apply_regex(segments, _MENTION_ROLE_RE, _mention_role)

  # 1i. Newlines
  segments = _apply_regex(segments, re.compile(r'\n'), ',')
  # 1j. Spaces (half-width and full-width)
  segments = _apply_regex(segments, re.compile(r'[ \u3000]+'), ',')

  # 2. 優先辞書 → 通常辞書 → 共通辞書
  cur = conn.execute(
    "SELECT word, reading FROM dict WHERE guild_id = ? AND is_priority = 1 AND reading IS NOT NULL ORDER BY added_at DESC",
    (gid,)
  )
  d = dict(cur.fetchall())
  if d:
    segments = _apply_dict(segments, d)

  cur = conn.execute(
    "SELECT word, reading FROM dict WHERE guild_id = ? AND is_priority = 0 AND reading IS NOT NULL ORDER BY added_at DESC",
    (gid,)
  )
  d = dict(cur.fetchall())
  if d:
    segments = _apply_dict(segments, d)

  cur = conn.execute(
    "SELECT word, reading FROM dict WHERE guild_id = '__common__' AND reading IS NOT NULL ORDER BY added_at DESC"
  )
  d = dict(cur.fetchall())
  if d:
    segments = _apply_dict(segments, d)

  # 3. emoji_ja short_name mapping
  if emoji_ja:
    segments = _apply_dict(segments, emoji_ja)

  # 4. Join with replaced range tracking
  result_parts = []
  replaced_ranges = []
  pos = 0
  for seg_text, replaced in segments:
    end = pos + len(seg_text)
    if replaced:
      replaced_ranges.append((pos, end))
    result_parts.append(seg_text)
    pos = end
  result = ''.join(result_parts)

  # 5. Attachments (appended after; ranges not tracked)
  for att in attachments:
    _, ext = os.path.splitext(att.filename.lower())
    if ext in _IMAGE_EXTS:
      result += ',画像ファイル'
    elif ext in _VIDEO_EXTS:
      result += ',動画ファイル'
    elif ext in _AUDIO_EXTS:
      result += ',音声ファイル'
    else:
      result += ',添付ファイル'

  return result, replaced_ranges
