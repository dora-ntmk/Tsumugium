"""辞書のエクスポート・インポート（.dictファイル、CSV形式）のテスト。

単語と読みはテスト用の作り物で、実際の辞書の登録内容は使わない。
"""

import asyncio
import sqlite3
import unittest

import discord

from services import dictionary_transfer_service as dict_transfer
from services.dictionary_transfer_service import (
  ImportFailure,
  ImportRejected,
  decode_import,
  format_export,
  parse_import,
)
from tests.support import (
  DUMMY_GUILD_ID,
  DUMMY_OTHER_GUILD_ID,
  DUMMY_SOUND_ID,
  RecordingNotifier,
  TempDirTestCase,
)
from word_dict import (
  DictManager,
  build_import_failure_report,
  send_import_failures,
)


class ParseImportTest(unittest.TestCase):
  def test_basic_lines(self):
    parsed = parse_import('てすと語, てすとご\nみほん,みほん\n')
    self.assertEqual(
      [(e.line_no, e.word, e.reading) for e in parsed.entries],
      [(1, 'てすと語', 'てすとご'), (2, 'みほん', 'みほん')],
    )
    self.assertEqual(parsed.failures, [])

  def test_splits_at_last_comma(self):
    parsed = parse_import('A,B, えーびー\n')
    self.assertEqual(parsed.entries[0].word, 'A,B')
    self.assertEqual(parsed.entries[0].reading, 'えーびー')

  def test_header_comments_and_blank_lines_are_skipped(self):
    parsed = parse_import(
      f'# exported_{DUMMY_GUILD_ID}.dict\n\n   \n# メモ\nてすと, てすと\n'
    )
    self.assertEqual([e.word for e in parsed.entries], ['てすと'])
    self.assertEqual(parsed.failures, [])

  def test_hash_word_with_comma_is_imported(self):
    parsed = parse_import('#てすと, はっしゅてすと\n')
    self.assertEqual([e.word for e in parsed.entries], ['#てすと'])

  def test_crlf(self):
    parsed = parse_import('てすと, てすと\r\nみほん, みほん\r\n')
    self.assertEqual([e.reading for e in parsed.entries], ['てすと', 'みほん'])

  def test_invalid_lines_are_reported(self):
    long_reading = 'あ' * (dict_transfer.MAX_READING_LENGTH + 1)
    parsed = parse_import(
      'カンマなし\n'
      ', よみだけ\n'
      'ごだけ,\n'
      f'ながい, {long_reading}\n'
      'てすと, てすと\n'
      'てすと, べつのよみ\n'
    )
    self.assertEqual([e.word for e in parsed.entries], ['てすと'])
    self.assertEqual(
      [(f.line_no, f.reason) for f in parsed.failures],
      [
        (1, dict_transfer.REASON_NO_COMMA),
        (2, dict_transfer.REASON_EMPTY_WORD),
        (3, dict_transfer.REASON_EMPTY_READING),
        (4, dict_transfer.REASON_READING_TOO_LONG),
        (6, '同じ単語が5行目にもあります'),
      ],
    )

  def test_reading_at_limit_is_accepted(self):
    reading = 'あ' * dict_transfer.MAX_READING_LENGTH
    self.assertEqual(len(parse_import(f'てすと, {reading}').entries), 1)

  def test_too_many_entries_are_rejected(self):
    count = dict_transfer.MAX_IMPORT_ENTRIES + 1
    text = ''.join(f'ご{i}, よみ\n' for i in range(count))
    with self.assertRaisesRegex(ImportRejected, '多すぎます'):
      parse_import(text)

  def test_entries_at_limit_are_accepted(self):
    count = dict_transfer.MAX_IMPORT_ENTRIES
    text = ''.join(f'ご{i}, よみ\n' for i in range(count))
    self.assertEqual(len(parse_import(text).entries), count)


SAMPLE = 'てすと語, てすとご\r\nA,B, えーびー\r\n一, いち\r\n'
BOM = '\ufeff'


class DecodeImportTest(unittest.TestCase):
  def test_utf8(self):
    self.assertEqual(decode_import(SAMPLE.encode('utf-8')), SAMPLE)

  def test_utf8_with_bom(self):
    self.assertEqual(decode_import((BOM + SAMPLE).encode('utf-8')), SAMPLE)

  def test_utf16_le_with_bom(self):
    # Windowsのツールが「Unicode」として書き出す形。
    self.assertEqual(decode_import(SAMPLE.encode('utf-16')), SAMPLE)
    self.assertEqual(decode_import((BOM + SAMPLE).encode('utf-16-le')), SAMPLE)

  def test_utf16_be_with_bom(self):
    self.assertEqual(decode_import((BOM + SAMPLE).encode('utf-16-be')), SAMPLE)

  def test_utf16_without_bom(self):
    self.assertEqual(decode_import(SAMPLE.encode('utf-16-le')), SAMPLE)
    self.assertEqual(decode_import(SAMPLE.encode('utf-16-be')), SAMPLE)

  def test_shift_jis(self):
    self.assertEqual(decode_import(SAMPLE.encode('cp932')), SAMPLE)

  def test_detected_encodings(self):
    self.assertEqual(dict_transfer.detect_encoding(SAMPLE.encode('utf-8')), 'utf-8')
    self.assertEqual(dict_transfer.detect_encoding(SAMPLE.encode('utf-16-le')), 'utf-16-le')
    self.assertEqual(dict_transfer.detect_encoding(SAMPLE.encode('cp932')), 'cp932')

  def test_unreadable_bytes(self):
    with self.assertRaisesRegex(ImportRejected, '文字コード'):
      decode_import(b'\x81')

  def test_nul_bytes_are_rejected(self):
    with self.assertRaisesRegex(ImportRejected, '文字コード'):
      decode_import(b'abc\x00def')

  def test_too_large(self):
    with self.assertRaisesRegex(ImportRejected, '大きすぎます'):
      decode_import(b'a' * (dict_transfer.MAX_IMPORT_BYTES + 1))


class FormatTest(unittest.TestCase):
  def test_export_format(self):
    self.assertEqual(
      format_export([('てすと', 'てすと'), ('A,B', 'えーびー')]),
      'てすと, てすと\nA,B, えーびー\n',
    )

  def test_reading_with_comma_is_not_exported(self):
    self.assertEqual(format_export([('てすと', 'て,すと')]), '')

  def test_export_filename(self):
    self.assertEqual(
      dict_transfer.export_filename(DUMMY_GUILD_ID),
      f'exported_{DUMMY_GUILD_ID}.dict',
    )

  def test_failure_format(self):
    failures = [
      ImportFailure(3, 'ごだけ,', dict_transfer.REASON_EMPTY_READING),
      ImportFailure(1, 'カンマなし', dict_transfer.REASON_NO_COMMA),
    ]
    self.assertEqual(
      dict_transfer.format_failures(failures),
      '1: カンマなし — カンマがありません\n3: ごだけ, — 読みが空です\n',
    )

  def test_long_line_is_shortened(self):
    line = 'あ' * (dict_transfer.MAX_REPORTED_LINE_LENGTH + 10)
    text = ImportFailure(1, line, dict_transfer.REASON_NO_COMMA).format()
    self.assertIn('あ' * dict_transfer.MAX_REPORTED_LINE_LENGTH + '…', text)
    self.assertNotIn('あ' * (dict_transfer.MAX_REPORTED_LINE_LENGTH + 1), text)


class DictManagerTransferTest(TempDirTestCase):
  def setUp(self):
    super().setUp()
    self.manager = DictManager(self.tmp_path('dict.db'), RecordingNotifier())
    self.addCleanup(self.manager.close)

  def readings(self, guild_id=DUMMY_GUILD_ID):
    return dict(self.manager.get_entries(guild_id))

  def test_round_trip(self):
    self.manager.add(DUMMY_GUILD_ID, 'てすと語', 'てすとご')
    self.manager.add(DUMMY_GUILD_ID, 'A,B', 'えーびー')
    text, count, skipped = self.manager.export_text(DUMMY_GUILD_ID)
    self.assertEqual((count, skipped), (2, 0))
    self.assertEqual(
      sorted(text.splitlines()),
      sorted(['てすと語, てすとご', 'A,B, えーびー']),
    )

    result = self.manager.import_data(DUMMY_OTHER_GUILD_ID, text.encode('utf-8'))
    self.assertEqual(result.added, 2)
    self.assertEqual(result.failures, [])
    self.assertEqual(self.readings(DUMMY_OTHER_GUILD_ID), self.readings())

  def test_export_counts_skipped_readings(self):
    self.manager.add(DUMMY_GUILD_ID, 'てすと', 'て,すと')
    self.manager.add(DUMMY_GUILD_ID, 'みほん', 'みほん')
    self.assertEqual(
      self.manager.export_text(DUMMY_GUILD_ID),
      ('みほん, みほん\n', 1, 1),
    )

  def test_existing_readings_are_not_overwritten(self):
    self.manager.add(DUMMY_GUILD_ID, 'てすと', 'もとのよみ')
    result = self.manager.import_data(
      DUMMY_GUILD_ID,
      'みほん, みほん\nてすと, あたらしいよみ\nカンマなし\n'.encode('utf-8'),
    )
    self.assertEqual(result.added, 1)
    self.assertEqual(
      [(f.line_no, f.reason) for f in result.failures],
      [
        (2, dict_transfer.REASON_ALREADY_EXISTS),
        (3, dict_transfer.REASON_NO_COMMA),
      ],
    )
    self.assertEqual(self.readings(), {'てすと': 'もとのよみ', 'みほん': 'みほん'})

  def test_sound_only_word_gets_reading_and_keeps_sound(self):
    self.manager.add_sound(DUMMY_GUILD_ID, 'てすと', DUMMY_SOUND_ID)
    result = self.manager.import_data(DUMMY_GUILD_ID, 'てすと, てすと'.encode('utf-8'))
    self.assertEqual((result.added, result.failures), (1, []))
    self.assertEqual(self.readings(), {'てすと': 'てすと'})
    sounds = self.manager.get_sound_entries(DUMMY_GUILD_ID)
    self.assertEqual([(s[0], s[1]) for s in sounds], [('てすと', DUMMY_SOUND_ID)])

  def test_priority_is_decided_from_word(self):
    self.manager.import_data(
      DUMMY_GUILD_ID,
      'https://example.com/, れいのさいと\nてすと, てすと\n'.encode('utf-8'),
    )
    rows = dict(self.manager.repository._conn.execute(
      'SELECT word, is_priority FROM dict WHERE guild_id = ?',
      (str(DUMMY_GUILD_ID),),
    ).fetchall())
    self.assertEqual(rows, {'https://example.com/': 1, 'てすと': 0})

  def test_rejected_file_adds_nothing(self):
    with self.assertRaises(ImportRejected):
      self.manager.import_data(DUMMY_GUILD_ID, b'\x81')
    self.assertEqual(self.readings(), {})

  def test_utf16_file_is_imported(self):
    data = '# メモ\r\nてすと, てすと\r\nみほん, みほん\r\n'.encode('utf-16')
    result = self.manager.import_data(DUMMY_GUILD_ID, data)
    self.assertEqual((result.added, result.failures), (2, []))
    self.assertEqual(self.readings(), {'てすと': 'てすと', 'みほん': 'みほん'})

  def test_database_error_rolls_back(self):
    repository = self.manager.repository
    real = repository._conn

    class FailingConnection:
      """3回目のINSERTで失敗させる。"""

      def __init__(self):
        self.inserts = 0

      def execute(self, sql, *args):
        if sql.lstrip().startswith('INSERT'):
          self.inserts += 1
          if self.inserts == 3:
            raise sqlite3.OperationalError('dummy failure')
        return real.execute(sql, *args)

      def __enter__(self):
        return real.__enter__()

      def __exit__(self, *exc):
        return real.__exit__(*exc)

    repository._conn = FailingConnection()
    try:
      with self.assertRaises(sqlite3.OperationalError):
        self.manager.import_data(
          DUMMY_GUILD_ID,
          'いち, いち\nに, に\nさん, さん\n'.encode('utf-8'),
        )
    finally:
      repository._conn = real
    self.assertEqual(self.readings(), {})


class FakeGuild:
  id = DUMMY_GUILD_ID
  name = 'テスト用サーバー'


class FakeUser:
  def __init__(self, error=None):
    self.error = error
    self.sent = []

  async def send(self, **kwargs):
    if self.error is not None:
      raise self.error
    self.sent.append(kwargs)


class FakeResponse:
  status = 403
  reason = 'Forbidden'


class FailureReportTest(unittest.TestCase):
  def failures(self, count):
    return [
      ImportFailure(i, f'ぎょう{i}', dict_transfer.REASON_NO_COMMA)
      for i in range(1, count + 1)
    ]

  def test_short_report_is_inline(self):
    content, report = build_import_failure_report(FakeGuild(), self.failures(2))
    self.assertIsNone(report)
    self.assertIn('テスト用サーバー', content)
    self.assertIn('（2件）', content)
    self.assertIn('1: ぎょう1 — カンマがありません', content)

  def test_long_report_is_attached(self):
    content, report = build_import_failure_report(FakeGuild(), self.failures(200))
    self.assertIsInstance(report, discord.File)
    self.assertEqual(report.filename, f'import_failures_{DUMMY_GUILD_ID}.txt')
    body = report.fp.read().decode('utf-8')
    self.assertIn('200: ぎょう200 — カンマがありません', body)
    self.assertIn('添付', content)

  def test_dm_is_sent(self):
    user = FakeUser()
    delivered = asyncio.run(send_import_failures(user, FakeGuild(), self.failures(1)))
    self.assertTrue(delivered)
    self.assertEqual(len(user.sent), 1)
    self.assertNotIn('file', user.sent[0])

  def test_dm_failure_returns_false(self):
    user = FakeUser(discord.Forbidden(FakeResponse(), 'Cannot send messages to this user'))
    delivered = asyncio.run(send_import_failures(user, FakeGuild(), self.failures(1)))
    self.assertFalse(delivered)


if __name__ == '__main__':
  unittest.main()
