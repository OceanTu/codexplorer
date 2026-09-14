"""Regression cases for rewritten rollouts and recoverable conversation ranges."""
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_usage.importer import Importer
from codex_usage.messages import MessagesUnavailable, RequestMessages
from codex_usage.store import connect
from test_usage import event, tokens
from test_messages import message, native, call, result


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'codex'
        self.data = Path(self.temp.name) / 'data'
        (self.home / 'sessions').mkdir(parents=True)
        self.path = self.home / 'sessions/rollout.jsonl'
        self.con = connect(self.data)
        self.addCleanup(lambda: self.con.close())
        self.head = [event('session_meta', dict(id='s', session_id='s')),
                     event('turn_context', dict(turn_id='t', model='test', effort='high'))]

    def write(self, *items):
        self.path.write_text(''.join(json.dumps(d, ensure_ascii=False) + '\n'
                                     for d in self.head + list(items)))

    def scan(self):
        report = Importer(self.con, self.home).scan(self.data)
        self.assertEqual(report['errors'], [])
        return report

    def record(self, rid='r1'):
        return dict(self.con.execute('SELECT * FROM usage WHERE id=?', ('response:' + rid,)).fetchone())

    def read(self, rid='r1'):
        return RequestMessages(self.con, self.home).read('response:' + rid)

    def test_rewrite_updates_position_without_duplicate_billing(self):
        self.write(message('user', 'before'), native('r1'))
        self.scan()
        before = self.record()
        self.write(message('user', 'new preamble ' * 200), native('r1'))
        self.scan()
        after = self.record()
        self.assertNotEqual(after['byte_offset'], before['byte_offset'])
        self.assertEqual(after['total_tokens'], before['total_tokens'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 1)
        self.assertEqual(self.read()['input'][0]['text'], 'new preamble ' * 200)
        self.assertEqual(self.scan()['bytes_read'], 0)

    def test_middle_rewrite_and_append_with_unchanged_tail_rescans(self):
        # The former 256-byte tail checkpoint cannot detect this rewrite.
        pad = event('ignored', dict(text='z' * 1000))
        self.write(message('user', 'short'), native('r1'), pad)
        self.scan()
        before = self.record()['byte_offset']
        self.write(message('user', 'longer' * 50), native('r1'),
                   event('ignored', dict(text='z' * (1000 - 295))), native('r2', 2))
        self.scan()
        self.assertNotEqual(self.record()['byte_offset'], before)
        self.assertEqual(self.con.execute('SELECT COUNT(*),SUM(total_tokens) FROM usage').fetchone()[:], (2, 240))
        self.assertEqual(self.read()['input'][0]['text'], 'longer' * 50)

    def test_v1_migration_invalidates_cursors_once_and_retains_usage(self):
        self.write(message('user', 'migration'), native('r1'))
        self.scan()
        self.con.execute('UPDATE usage SET byte_offset=1')
        self.con.execute('PRAGMA user_version=1')
        self.con.commit()
        self.con.close()
        self.con = connect(self.data)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM files').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT SUM(total_tokens) FROM usage').fetchone()[0], 120)
        self.assertGreater(self.scan()['bytes_read'], 0)
        self.assertEqual(self.read()['input'][0]['text'], 'migration')
        self.con.close()
        self.con = connect(self.data)
        self.assertEqual(self.scan()['bytes_read'], 0)

    def test_inherited_records_update_location_only(self):
        self.write(message('user', 'original'), native('r1'))
        self.scan()
        before = self.record()
        inherited = native('r1'); inherited['ordinal'] = 3
        inherited['payload']['usage'] = tokens(9999)  # Locator repair cannot alter accounting.
        unknown = native('not-previously-counted'); unknown['ordinal'] = 4
        archived = self.home / 'archived_sessions'
        archived.mkdir()
        target = archived / 'page.jsonl'
        target.write_text(''.join(json.dumps(d) + '\n' for d in [
            event('session_meta', dict(id='fork', session_id='fork', forked_from_ordinal_exclusive=10)),
            inherited, unknown]))
        self.path.unlink()
        self.scan()
        after = self.record()
        self.assertEqual(after['path'], str(target.resolve()))
        self.assertNotEqual(after['byte_offset'], before['byte_offset'])
        self.assertEqual({k:v for k,v in before.items() if k not in ('path','byte_offset')},
                         {k:v for k,v in after.items() if k not in ('path','byte_offset')})
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 1)
        self.assertEqual(self.read()['request_id'], 'response:r1')

    def test_duplicate_in_same_stream_repairs_position_without_pending_usage(self):
        self.write(native('r1'))
        self.scan()
        first = self.record()['byte_offset']
        with self.path.open('a') as f:
            f.write(json.dumps(message('user', 'copied history')) + '\n')
            f.write(json.dumps(native('r1')) + '\n')
        self.scan()
        self.assertGreater(self.record()['byte_offset'], first)
        self.assertEqual(self.con.execute('SELECT COUNT(*),SUM(total_tokens) FROM usage').fetchone()[:], (1,120))

    def test_stale_neighbor_markers_are_skipped_in_both_directions(self):
        self.write(message('assistant', 'outside range'), native('r0', 1),
                   native('bad-before', 2), message('user', 'recover input'), call(), native('r1', 3),
                   native('bad-after', 4), result(text='recover result'), native('r2', 5),
                   result(text='outside result'))
        self.scan()
        # Same length replacements leave the current and outer marker positions valid.
        text = self.path.read_text().replace('"bad-before"', '"old-before"').replace('"bad-after"', '"old-after"')
        self.path.write_text(text)
        data = self.read()
        self.assertEqual([m['text'] for m in data['input']], ['recover input'])
        self.assertEqual([m['name'] for m in data['output']], ['read_file'])
        self.assertEqual([m['text'] for m in data['tool_results']], ['recover result'])
        self.assertTrue(any('跳过' in w for w in data['warnings']))
        self.assertNotIn('outside range', json.dumps(data))
        self.assertNotIn('outside result', json.dumps(data))

    def test_unavailable_errors_distinguish_missing_offset_and_response(self):
        self.write(native('r1'))
        self.scan()
        saved = self.path.read_text()
        self.path.write_text(saved.replace('"r1"', '"rX"'))
        with self.assertRaisesRegex(MessagesUnavailable, 'response_id'):
            self.read()
        self.path.write_text(saved)
        self.con.execute('UPDATE usage SET byte_offset=byte_offset+1')
        with self.assertRaisesRegex(MessagesUnavailable, '偏移'):
            self.read()
        self.path.unlink()
        with self.assertRaisesRegex(MessagesUnavailable, '不存在|不可访问'):
            self.read()

    def test_runtime_blocks_filtered_only_from_user_messages(self):
        injected = ('<recommended_plugins>plugins</recommended_plugins>\n'
                    '<environment_context>env</environment_context>\n'
                    '<in-app-browser-context source="ambient-ui-state">browser</in-app-browser-context>\n'
                    '<user_instructions>instructions</user_instructions>\n'
                    '# AGENTS.md instructions for /workspace\n<INSTRUCTIONS>agents</INSTRUCTIONS>\n')
        self.write(message('user', injected), message('user', injected + '真实请求'),
                   message('user', 'wrapper\n## My request:\n最终请求'),
                   message('assistant', injected), result(text=injected), native('r1'))
        self.scan()
        data = self.read()
        self.assertEqual([m['text'] for m in data['input'] if m['role']=='user'], ['真实请求', '最终请求'])
        self.assertEqual(data['output'][0]['text'], injected)
        self.assertEqual([m['text'] for m in data['input'] if m['role']=='tool'], [injected])

    def test_event_fallback_filters_runtime_blocks(self):
        self.write(event('event_msg', dict(type='user_message', message=
            '<environment_context>runtime</environment_context>\nfallback request')), native('r1'))
        self.scan()
        self.assertEqual(self.read()['input'][0]['text'], 'fallback request')

    def test_range_read_stops_if_rewrite_truncates_file(self):
        class GuardedFile(io.BytesIO):
            reads = 0
            def readline(self, size=-1):
                self.reads += 1
                if self.reads > 10:
                    raise AssertionError('Reader loops past EOF after concurrent truncation')
                return super().readline(size)
        # Simulate a file becoming shorter after its range end was computed.
        for content, limit in ((b'', 4), (b'oversized', 100)):
            with self.subTest(content=content), patch.object(Path, 'open', return_value=GuardedFile(content)), \
                    patch('codex_usage.messages.MAX_RANGE_BYTES', limit), \
                    patch('codex_usage.messages.MAX_LINE_BYTES', 2):
                warnings = set()
                self.assertEqual(RequestMessages(self.con, self.home).region(self.path, 0, 30, warnings), [])
                self.assertTrue(warnings)


if __name__ == '__main__':
    unittest.main()
