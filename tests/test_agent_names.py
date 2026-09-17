import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from codex_usage.store import connect
from codex_usage.importer import Importer
from codex_usage.api import API
from test_usage import event, tokens


class AgentNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'codex'
        self.data = Path(self.temp.name) / 'data'
        (self.home / 'sessions').mkdir(parents=True)
        self.con = connect(self.data)
        self.addCleanup(lambda: self.con.close())

    def write(self, tid, **meta):
        path = self.home / 'sessions' / (tid + '.jsonl')
        path.write_text('\n'.join(json.dumps(e) for e in [
            event('session_meta', dict(id=tid, session_id='s', parent_thread_id='s', thread_source='subagent', **meta)),
            event('turn_context', dict(turn_id=tid, root_turn_id='root', model='test')),
            event('token_usage_record', dict(response_id=tid, thread_id=tid, session_id='s', turn_id=tid, root_turn_id='root', usage=tokens()))])+'\n')

    def scan(self):
        return Importer(self.con, self.home).scan(self.data)

    def rows(self):
        return API(self.con).turn('s', 'root', {})

    def test_rollout_nested_names_and_api_execution_labels(self):
        self.write('a', source={'subagent':{'thread_spawn':{'agent_path':'/root/feature_tracker','agent_nickname':'Leibniz','agent_role':'reviewer'}}})
        self.scan()
        row = self.rows()['requests'][0]
        self.assertEqual(row['agent_label'], 'feature_tracker · Leibniz · reviewer')
        self.assertEqual(self.rows()['executions'][0]['agent_label'], row['agent_label'])
        self.assertEqual(row['agent_path'], '/root/feature_tracker')

    def test_state_enrichment_and_old_optional_schema(self):
        self.write('a', agent_nickname='Original', agent_path='/root/task')
        self.scan()
        db = sqlite3.connect(self.home / 'state_5.sqlite')
        db.execute('CREATE TABLE threads(id,title,archived,name,agent_nickname,agent_role,agent_path)')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?)', ('a','inherited prompt',0,'检查渲染','Turing','reviewer',None))
        db.commit();db.close()
        self.scan()
        self.assertEqual(self.rows()['requests'][0]['agent_label'],'检查渲染 · Turing · reviewer')
        db = sqlite3.connect(self.home / 'state_5.sqlite')
        db.execute('DROP TABLE threads');db.execute('CREATE TABLE threads(id,title,archived)')
        db.execute('INSERT INTO threads VALUES (?,?,?)',('a','inherited prompt',0));db.commit();db.close()
        self.scan()
        self.assertEqual(self.rows()['requests'][0]['agent_label'],'检查渲染 · Turing · reviewer')

    def test_migration_backfills_headers_without_usage_replay(self):
        self.write('a', agent_path='/root/old_task', agent_nickname='Ada')
        self.scan()
        before=self.con.execute('SELECT id,total_tokens,byte_offset FROM usage').fetchall()
        # Build a genuine v3 threads table, keeping usage and scanning cursors.
        self.con.execute('ALTER TABLE threads RENAME TO old_threads')
        self.con.execute("CREATE TABLE threads AS SELECT id,session_id,parent_id,title,cwd,source,model,created_at,archived FROM old_threads")
        self.con.execute('CREATE UNIQUE INDEX thread_pk ON threads(id)')
        self.con.execute('DROP TABLE old_threads')
        self.con.execute("DELETE FROM settings WHERE key='agent_metadata_v1'")
        self.con.execute('PRAGMA user_version=3');self.con.commit();self.con.close()
        self.con=connect(self.data)
        self.assertEqual(self.scan()['bytes_read'],0,'Backfill only headers, never replay accounting')
        self.assertEqual(self.rows()['requests'][0]['agent_label'],'old_task · Ada')
        self.assertEqual(self.con.execute('SELECT id,total_tokens,byte_offset FROM usage').fetchall(),before)

    def test_guardian_fallback_and_duplicate_names_are_distinct(self):
        for tid in ['a','b']:self.write(tid, agent_nickname='Same')
        self.write('c');self.write('d');self.scan()
        self.con.execute("UPDATE threads SET source='guardian_review',title='very long inherited user request' WHERE id IN ('c','d')")
        rows=self.rows()['requests'];names={r['thread_id']:r['agent_label'] for r in rows}
        self.assertNotEqual(names['a'],names['b'])
        self.assertEqual(names['c'],'自动审核 1');self.assertEqual(names['d'],'自动审核 2')
        self.assertEqual(API(self.con).turn('s','root',{'execution':'d'})['requests'][0]['agent_label'],names['d'])
