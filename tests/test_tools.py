import json
import tempfile
import unittest
from pathlib import Path

from codex_usage.api import API
from codex_usage.importer import Importer
from codex_usage.store import connect
from test_usage import event, tokens, plus
from test_messages import message, native, call, result


class ToolSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'codex'
        self.data = Path(self.temp.name) / 'data'
        (self.home / 'sessions').mkdir(parents=True)
        self.path = self.home / 'sessions/test.jsonl'
        self.con = connect(self.data)
        self.addCleanup(lambda: self.con.close())
        self.head = [event('session_meta', dict(id='s', session_id='s')),
                     event('turn_context', dict(turn_id='t', model='test'))]

    def write(self, *events):
        self.path.write_text(''.join(json.dumps(d) + '\n' for d in self.head + list(events)))

    def scan(self):
        self.assertEqual(Importer(self.con, self.home).scan(self.data)['errors'], [])

    def turn(self):
        return API(self.con).turn('s', 't', {})

    def test_calls_output_sizes_and_session_summary_without_message_bodies(self):
        self.write(message('user', 'SECRET-USER'), call(), native('r1'),
                   result(text='中文abc'), message('assistant', 'SECRET-ASSISTANT'), native('r2', 2))
        self.scan()
        # Summary uses the index, without rereading the complete transcript.
        self.path.unlink()
        turn = self.turn()
        first, second = turn['requests']
        self.assertEqual(first['tools']['calls'], 1)
        self.assertEqual(first['tools']['output_chars'], 5)
        self.assertEqual(first['tools']['output_bytes'], 9)
        self.assertEqual(first['tools']['tools'][0]['name'], 'read_file')
        self.assertEqual(second['tools']['calls'], 0)
        self.assertEqual(turn['tools']['calls'], 1)
        self.assertEqual(API(self.con).session('s', {})['turns'][0]['tools']['output_chars'], 5)
        self.con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        data = (self.data/'usage.sqlite3').read_bytes()
        # The original short user label is existing behavior; tool bodies stay out.
        self.assertNotIn('中文abc'.encode(), data)
        self.assertNotIn(b'SECRET-ASSISTANT', data)

    def test_missing_and_empty_results_are_distinct_and_late_result_updates(self):
        self.write(call('missing'), call('empty'), native('r1'), result('empty', ''))
        self.scan()
        summary = self.turn()['tools']
        self.assertEqual((summary['calls'], summary['missing_results'], summary['output_chars']), (2, 1, 0))
        with self.path.open('a') as f:
            f.write(json.dumps(result('missing', 'late')) + '\n')
        self.scan()
        self.assertEqual(self.turn()['tools']['missing_results'], 0)
        self.assertEqual(self.turn()['tools']['output_chars'], 4)

    def test_rewrite_and_duplicate_copy_do_not_double_count_tools(self):
        self.write(call(), native('r1'), result(text='old content'))
        self.scan()
        self.write(message('user', 'inserted'), call(), native('r1'), result(text='new'))
        self.scan()
        copy = self.home/'sessions/z-copy.jsonl'; copy.write_bytes(self.path.read_bytes())
        self.scan()
        summary = self.turn()['tools']
        self.assertEqual((summary['calls'], summary['output_chars']), (1, 3))
        self.assertEqual(self.turn()['summary']['total_tokens'], 120)

    def test_legacy_late_marker_and_inherited_history(self):
        legacy = event('event_msg', dict(type='token_count', info=dict(total_token_usage=tokens(), last_token_usage=tokens())))
        self.write(call(), result(text='legacy'), legacy)
        self.scan()
        self.assertEqual(self.turn()['tools']['output_chars'], 6)
        self.write(call(), native('r1'), result(text='inherited'))
        self.scan()
        records = [event('session_meta', dict(id='fork', forked_from_ordinal_exclusive=100)),
                   dict(call(), ordinal=1), dict(native('r1'), ordinal=2), dict(result(text='inherited'), ordinal=3)]
        self.path.write_text(''.join(json.dumps(d)+'\n' for d in records))
        self.scan()
        r = next(r for r in self.turn()['requests'] if r['id']=='response:r1')
        self.assertEqual((r['tools']['calls'], r['tools']['output_chars']), (1, 9))

    def test_unindexed_position_is_unknown_and_migration_rebuilds_summaries(self):
        self.write(call(), native('r1'), result(text='hello'))
        self.scan()
        self.con.execute('UPDATE usage SET byte_offset=1')
        self.assertEqual(self.turn()['tools']['unavailable_requests'], 1)
        self.con.execute('PRAGMA user_version=2'); self.con.commit(); self.con.close()
        self.con = connect(self.data)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM files').fetchone()[0], 0)
        self.scan()
        self.assertEqual(self.turn()['tools']['calls'], 1)


if __name__ == '__main__': unittest.main()
