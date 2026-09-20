"""Diagnostic recovery must reveal missing work without inventing usage."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from codex_usage.api import API
from codex_usage.importer import Importer
from codex_usage.store import connect

THREAD='11111111-1111-4111-8111-111111111111'
TURN='22222222-2222-4222-8222-222222222222'
BASE=1789905200


def log_db(home):
    db=sqlite3.connect(home/'logs_2.sqlite')
    db.execute('CREATE TABLE logs(id INTEGER PRIMARY KEY,ts INTEGER,ts_nanos INTEGER,target TEXT,thread_id TEXT,feedback_log_body TEXT)')
    return db


def prompt(text='OCR status?', turn=TURN):
    return ('session_loop{thread_id='+THREAD+'}: Submission sub=Submission { id: "'+turn+'", op: TurnInput { request: TurnInputRequest { input: UserInput { content: [Text { text: '+json.dumps(text,ensure_ascii=False)+', text_elements: [] }], client_id: None }, thread_settings: ThreadSettingsOverrides { cwd: AbsolutePathBuf("/project"), model: Some("gpt-6-astra") } } } }')


def sample(total=100, turn=TURN):
    return ('session_loop{thread_id='+THREAD+'}:turn{turn.id='+turn+' model=gpt-6-astra}: post sampling token usage turn_id='+turn+' total_usage_tokens='+str(total)+' auto_compact_scope_tokens='+str(total)+' model_needs_follow_up=false')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.home=self.root/'codex';self.home.mkdir();self.data=self.root/'data'
        self.con=connect(self.data);self.logs=log_db(self.home)
    def tearDown(self):
        self.logs.close();self.con.close();self.temp.cleanup()
    def add(self,body,at=BASE,target='codex_core::session::handlers'):
        self.logs.execute('INSERT INTO logs(ts,ts_nanos,target,thread_id,feedback_log_body) VALUES (?,?,?,?,?)',(at,0,target,THREAD,body));self.logs.commit()
    def scan(self):
        return Importer(self.con,self.home).scan(self.data)
    def recovered(self,params=None):
        return API(self.con).overview(params or {}).get('supplemental',[])

    def test_missing_rollout_is_visible_without_fabricating_tokens(self):
        self.add(prompt());self.add(sample(68533),BASE+5,'codex_core::session::turn');self.scan()
        rows=self.recovered();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['preview'],'OCR status?')
        self.assertEqual(rows[0]['context_samples'][0]['tokens'],68533)
        self.assertIsNone(rows[0]['total_tokens'])
        self.assertIsNone(rows[0]['costs']['credits'])
        self.assertEqual(API(self.con).overview({})['summary']['requests'],0)

    def test_repeat_scan_and_runtime_blocks_and_filters(self):
        self.add(prompt('<environment_context>hidden</environment_context>\n## My request:\nOCR status?'))
        self.scan();self.scan()
        self.assertEqual(len(self.recovered({'q':'OCR'})),1)
        self.assertEqual(self.recovered()[0]['preview'],'OCR status?')
        self.assertEqual(self.recovered({'q':'hidden'}),[])
        self.assertEqual(self.recovered({'from':str(BASE+10)}),[])

    def test_recreated_database_reuses_ids_without_skipping_new_prompt(self):
        self.add(prompt());self.scan();self.logs.close();(self.home/'logs_2.sqlite').unlink()
        self.logs=log_db(self.home)
        self.add(prompt('new chat',turn='33333333-3333-4333-8333-333333333333'),BASE+20)
        self.scan();self.assertEqual(len(self.recovered()),2)

    def test_native_turn_does_not_appear_as_missing(self):
        self.add(prompt());self.scan()
        self.assertEqual(len(self.recovered()),1)
        from test_usage import event,tokens
        folder=self.home/'sessions';folder.mkdir()
        records=[event('session_meta',dict(id=THREAD,session_id=THREAD)),event('turn_context',dict(turn_id=TURN)),event('token_usage_record',dict(thread_id=THREAD,session_id=THREAD,turn_id=TURN,root_turn_id=TURN,response_id='r',usage=tokens()))]
        (folder/'a.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records));self.scan()
        self.assertEqual(self.recovered(),[])
        self.assertEqual(API(self.con).overview({})['summary']['total_tokens'],120)

    def test_malformed_prompt_does_not_stop_later_records(self):
        self.add(prompt().replace('"OCR status?"','"broken\\q"'))
        self.add(prompt('healthy'),BASE+1);self.scan()
        self.assertEqual(len(self.recovered()),1)
        self.assertEqual(self.recovered()[0]['preview'],'healthy')

    def test_options_include_recovered_models_projects_and_parent_sessions(self):
        self.add(prompt());self.scan()
        self.con.execute("INSERT INTO threads(id,session_id,title) VALUES ('parent','parent','Parent task')")
        options=API(self.con).options()
        self.assertIn('gpt-6-astra',options['models'])
        self.assertIn('/project',options['projects'])
        self.assertIn({'id':'parent','title':'Parent task'},options['sessions'])

    def test_rust_unicode_escapes_preserve_literal_backslash(self):
        from codex_usage.supplemental import decode_string
        self.assertEqual(decode_string(r'"hello \u{4e2d}"'),'hello 中')
        self.assertEqual(decode_string(r'"literal \\u{4e2d}"'),r'literal \u{4e2d}')
        self.assertEqual(decode_string(r'"quote \u{22}"'),'quote "')
