"""Hidden rollout snapshots must not masquerade as missing chat messages."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_usage.importer import Importer
from codex_usage.messages import RequestMessages
from codex_usage.store import connect
from test_usage import event
from test_messages import message, native, call, result


class MessageLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)/'codex'
        self.data = Path(self.temp.name)/'data'
        (self.home/'sessions').mkdir(parents=True)
        self.path = self.home/'sessions/rollout.jsonl'
        self.con = connect(self.data)
        self.addCleanup(self.con.close)
        self.reader = RequestMessages(self.con, self.home)

    def write(self, *events):
        head = [event('session_meta', dict(id='s', session_id='s')),
                event('turn_context', dict(turn_id='t', model='test'))]
        self.path.write_text(''.join(json.dumps(d)+'\n' for d in head+list(events)))
        self.assertEqual(Importer(self.con, self.home).scan(self.data)['errors'], [])

    def test_large_compacted_snapshot_does_not_repeat_warning_via_turn_context(self):
        self.write(event('compacted', dict(replacement_history='data:image/png;base64,'+'a'*6000)),
                   message('user', 'real request'), message('assistant', 'first answer'), native('r1'),
                   message('assistant', 'second answer'), native('r2', 2))
        with patch('codex_usage.messages.MAX_LINE_BYTES', 4096):
            first = self.reader.read('response:r1'); second = self.reader.read('response:r2')
        self.assertEqual(first['warnings'], [])
        self.assertEqual(second['warnings'], [])
        self.assertEqual(first['input'][0]['text'], 'real request')
        self.assertEqual(second['context'][0]['text'], 'real request')
        self.assertEqual(second['output'][0]['text'], 'second answer')

    def test_large_internal_records_are_silent(self):
        records = [event('world_state', dict(text='w'*6000)),
                   event('response_item', dict(type='reasoning', encrypted_content='x'*6000)),
                   message('developer', 'd'*6000)]
        self.write(*records, message('user', 'visible'), native('r1'))
        with patch('codex_usage.messages.MAX_LINE_BYTES', 4096):
            d = self.reader.read('response:r1')
        self.assertEqual(d['warnings'], [])
        self.assertEqual(d['input'][0]['text'], 'visible')

    def test_large_visible_messages_still_warn_and_next_messages_survive(self):
        for large in (message('user', '{"type":"compacted"} '+'x'*6000),
                      result(text='large tool result '+'x'*6000)):
            with self.subTest(kind=large['payload']['type']):
                self.write(large, message('user', 'kept'), message('assistant', 'answer'), native('r1'))
                with patch('codex_usage.messages.MAX_LINE_BYTES', 4096):
                    d = self.reader.read('response:r1')
                self.assertEqual(len(d['warnings']), 1)
                self.assertIn('原始', d['warnings'][0])
                self.assertEqual([m['text'] for m in d['input']], ['kept'])
                self.assertEqual(d['output'][0]['text'], 'answer')

    def test_unrecognized_envelope_is_not_silently_classified_from_nested_type(self):
        # The top-level type is deliberately after a large payload. A regex for
        # the first type field would confuse the tool body with the envelope.
        self.write({'payload':{'type':'function_call_output', 'call_id':'c',
                               'output':{'type':'compacted','text':'x'*6000}},
                    'type':'response_item'}, native('r1'))
        with patch('codex_usage.messages.MAX_LINE_BYTES', 4096):
            d = self.reader.read('response:r1')
        self.assertEqual(len(d['warnings']), 1)

    def test_normal_compaction_marker_remains_identifiable(self):
        self.write(message('user', 'compact'), native('r1'),
                   event('compacted', dict(compaction_response_id='r1', message='summary')))
        d = self.reader.read('response:r1')
        self.assertTrue(d['is_compaction'])
        self.assertEqual(len(d['warnings']), 1)


if __name__ == '__main__': unittest.main()
