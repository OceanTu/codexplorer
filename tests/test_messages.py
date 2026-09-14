import json
import tempfile
import unittest
from pathlib import Path

from codex_usage.importer import Importer
from codex_usage.messages import CHUNK_CHARS, MessagesUnavailable, RequestMessages
from codex_usage.store import connect
from test_usage import event, tokens, plus


def message(role, text, at=0):
    return event('response_item', {'type':'message','role':role,
        'content':[{'type':'output_text' if role=='assistant' else 'input_text','text':text}]}, at)


def native(rid, at=1, turn='t', usage=None):
    return event('token_usage_record', dict(response_id=rid,thread_id='s',session_id='s',
        turn_id=turn,root_turn_id=turn,usage=tokens(),thread_token_usage=usage or tokens()),at)


def call(cid='c1', at=0):
    return event('response_item',dict(type='function_call',name='read_file',call_id=cid,
                                     arguments='{"path":"example.py"}'),at)


def result(cid='c1', text='file content', at=0):
    return event('response_item',dict(type='function_call_output',call_id=cid,output=text),at)


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)/'codex';self.data=Path(self.temp.name)/'data'
        (self.home/'sessions').mkdir(parents=True)
        self.path=self.home/'sessions'/'rollout.jsonl'
        self.con=connect(self.data)
        self.reader=RequestMessages(self.con,self.home)
        self.write(event('session_meta',dict(id='s',session_id='s')),
                   event('turn_context',dict(turn_id='t',model='test')))

    def tearDown(self):
        self.con.close();self.temp.cleanup()

    def write(self,*events):
        with self.path.open('a') as f:
            for d in events:f.write(json.dumps(d,ensure_ascii=False)+'\n')

    def scan(self):
        self.assertEqual(Importer(self.con,self.home).scan(self.data)['errors'],[])

    def test_native_inputs_outputs_tool_results_and_turn_context(self):
        self.write(message('developer','INTERNAL-INSTRUCTION'),message('user','检查 example.py'),
            event('response_item',dict(type='reasoning',raw_content='PRIVATE-REASONING',encrypted_content='OPAQUE')),
            call(),native('r1'),result(text='print(42)',at=2),message('assistant','文件会打印 42。',3),
            native('r2',4,usage=plus(tokens(),tokens())))
        self.scan()
        first=self.reader.read('response:r1')
        self.assertEqual([m['text'] for m in first['input']],['检查 example.py'])
        self.assertEqual(first['output'][0]['name'],'read_file')
        self.assertEqual(first['tool_results'][0]['text'],'print(42)')
        second=self.reader.read('response:r2')
        self.assertEqual(second['input'][0]['text'],'print(42)')
        self.assertEqual(second['output'][0]['text'],'文件会打印 42。')
        self.assertEqual(second['context'][0]['text'],'检查 example.py')
        serialized=json.dumps([first,second])
        for private in ('INTERNAL-INSTRUCTION','PRIVATE-REASONING','OPAQUE'):
            self.assertNotIn(private,serialized)
        # Message bodies are loaded from source, not copied into the SQLite index.
        self.assertNotIn('文件会打印 42。'.encode(),(self.data/'usage.sqlite3').read_bytes())

    def test_legacy_tool_result_is_next_request_input(self):
        def legacy(total,at):
            return event('event_msg',dict(type='token_count',info=dict(total_token_usage=total,last_token_usage=tokens())),at)
        self.write(message('user','读取文件'),call(),result(text='first result'),legacy(tokens(),1),
                   message('assistant','已读取',2),legacy(plus(tokens(),tokens()),3))
        self.scan()
        ids=[r[0] for r in self.con.execute('SELECT id FROM usage ORDER BY byte_offset')]
        first=self.reader.read(ids[0]);second=self.reader.read(ids[1])
        self.assertEqual([m['role'] for m in first['input']],['user'])
        self.assertEqual(first['tool_results'][0]['text'],'first result')
        self.assertEqual(second['input'][0]['text'],'first result')
        self.assertEqual(second['output'][0]['text'],'已读取')
        self.assertTrue(second['warnings'])

    def test_long_messages_load_more_without_losing_text(self):
        body='中文 <img src=x onerror=alert(1)>\n'*(CHUNK_CHARS//10)
        self.write(message('user',body),message('assistant','好'),native('r1'))
        self.scan();data=self.reader.read('response:r1');m=data['input'][0]
        self.assertEqual(len(m['text']),CHUNK_CHARS)
        restored=m['text']
        while m['next_offset'] is not None:
            m=self.reader.read('response:r1',m['id'],m['next_offset']);restored+=m['text']
        self.assertEqual(restored,body)
        with self.assertRaises(KeyError):self.reader.read('response:r1','arbitrary-message-id')
        with self.assertRaises(ValueError):self.reader.read('response:r1',m['id'],-1)

    def test_missing_replaced_and_outside_source_are_unavailable(self):
        self.write(message('user','hello'),native('r1'));self.scan()
        saved=self.path.read_text();self.path.write_text(saved.replace('"r1"','"rX"'))
        with self.assertRaises(MessagesUnavailable):self.reader.read('response:r1')
        self.path.unlink()
        with self.assertRaises(MessagesUnavailable):self.reader.read('response:r1')
        self.con.execute('UPDATE usage SET path=?',(str(Path(self.temp.name)/'auth.json'),));self.con.commit()
        with self.assertRaises(MessagesUnavailable):self.reader.read('response:r1')

    def test_event_fallback_retains_multiple_messages_and_avoids_mirrors(self):
        self.write(event('event_msg',dict(type='user_message',message='first')),
                   event('event_msg',dict(type='user_message',message='second')),
                   message('assistant','visible reply'),event('event_msg',dict(type='agent_message',message='visible reply')),
                   native('r1'))
        self.scan();d=self.reader.read('response:r1')
        self.assertEqual([m['text'] for m in d['input']],['first','second'])
        self.assertEqual(len(d['output']),1)

    def test_media_placeholder_and_unrelated_next_turn_are_not_mixed(self):
        self.write(event('response_item',dict(type='message',role='user',content=[
            {'type':'input_text','text':'看图'},{'type':'input_image','image_url':'data:image/png;base64,SECRETDATA'}])),
            message('assistant','已看到'),native('r1'),event('turn_context',dict(turn_id='t2',model='test')),
            message('user','another turn'),message('assistant','other response'),native('r2',4,'t2',plus(tokens(),tokens())))
        self.scan();d=self.reader.read('response:r1')
        text=json.dumps(d,ensure_ascii=False)
        self.assertIn('附件',text)
        self.assertNotIn('SECRETDATA',text)
        self.assertNotIn('another turn',text)
        self.assertNotIn('other response',text)


if __name__=='__main__':unittest.main()
