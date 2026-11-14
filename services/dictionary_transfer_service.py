"""読み辞書を.dictファイル（CSV形式）へ書き出し、読み込む。

形式は1行に1件の`単語, 読み`。SQLiteもDiscordも知らない純粋な処理だけを置く。
"""

import codecs

from dataclasses import dataclass, field

MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_ENTRIES = 5000
MAX_READING_LENGTH = 50
MAX_REPORTED_LINE_LENGTH = 100

REASON_NO_COMMA = 'カンマがありません'
REASON_EMPTY_WORD = '単語が空です'
REASON_EMPTY_READING = '読みが空です'
REASON_READING_TOO_LONG = f'読みが{MAX_READING_LENGTH}文字を超えています'
REASON_ALREADY_EXISTS = 'すでに辞書にあります'


class ImportRejected(Exception):
  """ファイル全体を読み込まないときに投げる。メッセージは利用者へそのまま見せる。"""


@dataclass(frozen=True)
class ImportFailure:
  line_no: int
  line: str
  reason: str

  def format(self) -> str:
    line = self.line
    if len(line) > MAX_REPORTED_LINE_LENGTH:
      line = line[:MAX_REPORTED_LINE_LENGTH] + '…'
    return f'{self.line_no}: {line} — {self.reason}'


@dataclass(frozen=True)
class ImportEntry:
  line_no: int
  line: str
  word: str
  reading: str


@dataclass
class ParsedImport:
  entries: list[ImportEntry] = field(default_factory=list)
  failures: list[ImportFailure] = field(default_factory=list)


@dataclass
class ImportResult:
  added: int
  failures: list[ImportFailure]


def export_filename(guild_id: int) -> str:
  return f'exported_{guild_id}.dict'


def is_exportable(reading: str) -> bool:
  """読みにカンマがあると、読み込むときに単語と読みの境目が分からなくなる。"""
  return ',' not in reading


def format_export(entries: list[tuple[str, str]]) -> str:
  """(単語, 読み)の一覧を.dictファイルの本文にする。書き出せない項目は入れない。"""
  return ''.join(
    f'{word}, {reading}\n'
    for word, reading in entries
    if is_exportable(reading)
  )


def check_import_size(size: int) -> None:
  if size > MAX_IMPORT_BYTES:
    raise ImportRejected(
      f'ファイルが大きすぎます（{MAX_IMPORT_BYTES // 1024}KBまで）'
    )


_REJECT_ENCODING = (
  '文字コードを判別できませんでした。UTF-8・UTF-16・Shift_JISのどれかで保存してください'
)

# BOMと、そのBOMを読み飛ばして読む文字コード。
_BOMS = (
  (codecs.BOM_UTF8, 'utf-8-sig'),
  (codecs.BOM_UTF16_LE, 'utf-16'),
  (codecs.BOM_UTF16_BE, 'utf-16'),
)


def _guess_utf16_without_bom(data: bytes):
  """BOMのないUTF-16を、半角文字（カンマ・空白・改行など）の上位バイトの0で見分ける。

  各行に半角文字があるので、UTF-16なら片側のバイトにだけ0が多く並ぶ。
  """
  if len(data) < 2 or len(data) % 2:
    return None
  even_zeros = data[0::2].count(0)
  odd_zeros = data[1::2].count(0)
  enough = len(data) // 20
  if odd_zeros >= enough and odd_zeros > even_zeros * 4:
    return 'utf-16-le'
  if even_zeros >= enough and even_zeros > odd_zeros * 4:
    return 'utf-16-be'
  return None


def detect_encoding(data: bytes) -> str:
  """ファイルの文字コードを中身から決める。

  BOMがあればそれに従う。なければUTF-16（0の並び）、UTF-8、Shift_JIS（cp932）の順に、
  読めて、かつNUL文字を含まないものを選ぶ。どれでも読めなければ ImportRejected を投げる。
  """
  for bom, encoding in _BOMS:
    if data.startswith(bom):
      return encoding
  candidates = [_guess_utf16_without_bom(data), 'utf-8', 'cp932']
  for encoding in filter(None, candidates):
    try:
      text = data.decode(encoding)
    except UnicodeDecodeError:
      continue
    if '\x00' not in text:
      return encoding
  raise ImportRejected(_REJECT_ENCODING)


def decode_import(data: bytes) -> str:
  check_import_size(len(data))
  try:
    return data.decode(detect_encoding(data))
  except UnicodeDecodeError:
    raise ImportRejected(_REJECT_ENCODING) from None


def parse_import(text: str) -> ParsedImport:
  """.dictファイルの本文を読む。

  空行と、`#`で始まりカンマのない行（`# exported_….dict`の見出しなど）は読み飛ばす。
  単語にカンマが入ってもよいように、最後のカンマで単語と読みに分ける。
  同じ単語が何度も出たら最初の行を使う。
  """
  parsed = ParsedImport()
  first_line_of: dict[str, int] = {}
  for line_no, raw in enumerate(text.splitlines(), start=1):
    line = raw.strip()
    if not line or (line.startswith('#') and ',' not in line):
      continue
    word, comma, reading = line.rpartition(',')
    if not comma:
      parsed.failures.append(ImportFailure(line_no, line, REASON_NO_COMMA))
      continue
    word = word.strip()
    reading = reading.strip()
    reason = None
    if not word:
      reason = REASON_EMPTY_WORD
    elif not reading:
      reason = REASON_EMPTY_READING
    elif len(reading) > MAX_READING_LENGTH:
      reason = REASON_READING_TOO_LONG
    elif word in first_line_of:
      reason = f'同じ単語が{first_line_of[word]}行目にもあります'
    if reason:
      parsed.failures.append(ImportFailure(line_no, line, reason))
      continue
    first_line_of[word] = line_no
    parsed.entries.append(ImportEntry(line_no, line, word, reading))
  if len(parsed.entries) > MAX_IMPORT_ENTRIES:
    raise ImportRejected(
      f'登録する単語が多すぎます（{len(parsed.entries)}件。'
      f'1回に{MAX_IMPORT_ENTRIES}件まで）'
    )
  return parsed


def format_failures(failures: list[ImportFailure]) -> str:
  ordered = sorted(failures, key=lambda failure: failure.line_no)
  return ''.join(f'{failure.format()}\n' for failure in ordered)
