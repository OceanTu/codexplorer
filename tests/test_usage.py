import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from codex_usage.api import API
from codex_usage.importer import Importer, FIELDS
from codex_usage.store import connect
from app import install_hooks, hook_definition


def tokens(i=100, c=60, o=20, r=5):
    return dict(input_tokens=i, cached_input_tokens=c, cache_write_input_tokens=0,
                output_tokens=o, reasoning_output_tokens=r, total_tokens=i + o)


def plus(a, b):
    return {k: a[k] + b[k] for k in FIELDS}


def event(t, p, at=0):
    return dict(type=t, timestamp='2026-09-11T08:00:%02dZ' % at, payload=p)


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'codex'
        (self.home / 'sessions').mkdir(parents=True)
        self.data = self.root / 'data'
        self.con = connect(self.data)
        self.importer = Importer(self.con, self.home)
        self.path = self.home / 'sessions' / 'rollout.jsonl'
        self.start()

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def write(self, *items, path=None):
        with (path or self.path).open('a') as f:
            for x in items:
                f.write(json.dumps(x) + '\n')

    def start(self, thread='s1', session='s1', turn='t1', root=None, model='gpt-test'):
        self.write(event('session_meta', dict(id=thread, session_id=session, cwd='/project', thread_source='user' if thread==session else 'subagent')),
                   event('event_msg', dict(type='task_started', turn_id=turn)),
                   event('turn_context', dict(turn_id=turn, root_turn_id=root, model=model, effort='high')))

    def exact(self, response='r1', usage=None, total=None, at=1, thread='s1', session='s1', turn='t1', root='t1'):
        usage = usage or tokens()
        return event('token_usage_record', dict(response_id=response, usage=usage, thread_token_usage=total or usage,
                    thread_id=thread, session_id=session, turn_id=turn, root_turn_id=root), at)

    def legacy(self, total=None, last=None, at=2):
        return event('event_msg', dict(type='token_count', info=dict(total_token_usage=total or tokens(), last_token_usage=last or tokens())), at)

    def scan(self):
        r = self.importer.scan(self.data)
        self.assertEqual(r['errors'], [])
        return r

    def summary(self):
        return API(self.con).overview({})['summary']

    def test_native_mirror_duplicates_and_resume_counter_bases(self):
        inherited = tokens(10000, 5000, 1000, 300)
        first = self.exact(total=plus(inherited,tokens()))
        self.write(first, self.legacy(), self.legacy())
        self.scan()
        self.assertEqual(self.summary()['total_tokens'], 120)
        self.assertEqual(self.summary()['requests'], 1)
        self.write(self.exact('r2', total=plus(plus(inherited,tokens()),tokens()),at=3),
                   self.legacy(total=plus(tokens(),tokens()),at=4))
        self.scan()
        self.assertEqual(self.summary()['total_tokens'], 240)
        self.assertEqual(self.summary()['quality_warnings'], 0)
        archive = self.home / 'archived_sessions'
        archive.mkdir()
        shutil.copyfile(self.path, archive / 'copy.jsonl')
        self.scan()
        self.assertEqual(self.summary()['total_tokens'], 240)

    def test_legacy_differences_duplicates_and_reset(self):
        self.write(self.legacy(), self.legacy(), self.legacy(total=tokens(250,160,60,15),last=tokens(150,100,40,10),at=3),
                   self.legacy(total=tokens(30,0,10,2),last=tokens(30,0,10,2),at=4))
        self.scan()
        s=self.summary()
        self.assertEqual(s['total_tokens'], 350)
        self.assertEqual(s['requests'], 3)
        self.assertEqual(s['cached_input_tokens'], 160)
        self.assertEqual(s['reasoning_output_tokens'], 17)
        self.assertEqual(s['quality_warnings'], 1)
        self.assertEqual(self.scan()['bytes_read'], 0)

    def test_partial_and_malformed_line_and_restart(self):
        line=json.dumps(self.exact()).encode()
        with self.path.open('ab') as f:f.write(b'not json\n'+line[:50])
        self.scan()
        self.assertEqual(self.summary()['requests'],0)
        self.assertEqual(API(self.con).status()['files']['pending'],1)
        self.assertEqual(API(self.con).status()['files']['malformed'],1)
        with self.path.open('ab') as f:f.write(line[50:]+b'\n')
        self.importer=Importer(self.con,self.home)
        self.scan()
        self.assertEqual(self.summary()['total_tokens'],120)
        self.assertEqual(API(self.con).status()['files']['pending'],0)

    def test_child_rollup_model_filter_and_request_drilldown(self):
        self.write(self.exact())
        self.path=self.home/'sessions'/'child.jsonl'
        self.start(thread='child',turn='child-turn',root='t1',model='gpt-child')
        self.write(self.exact('r-child',thread='child',turn='child-turn',root='t1',at=3))
        self.scan();api=API(self.con)
        self.assertEqual(self.summary()['sessions'],1)
        self.assertEqual(self.summary()['turns'],1)
        self.assertEqual(self.summary()['agent_tokens'],120)
        self.assertEqual(api.session('s1',{})['turns'][0]['total_tokens'],240)
        self.assertEqual(len(api.turn('s1','t1',{})['requests']),2)
        self.assertEqual(len(api.turn('s1','t1',{})['executions']),2)
        self.assertEqual(api.turn('s1','t1',{'execution':'child-turn'})['summary']['total_tokens'],120)
        self.assertEqual(api.overview({'model':'gpt-child'})['summary']['total_tokens'],120)
        self.assertEqual(api.overview({'scope':'main'})['summary']['total_tokens'],120)
        self.assertEqual(api.overview({'q':"' OR 1=1 --"})['summary']['total_tokens'],0)
        self.assertEqual(api.overview({'from':'1789113602'})['summary']['total_tokens'],120)

    def test_mixed_legacy_then_native_and_multiple_native_per_snapshot(self):
        self.write(self.legacy(), self.exact('r2',total=plus(tokens(),tokens()),at=3),
                   self.exact('r3',total=tokens(300,180,60,15),at=4),
                   self.legacy(total=tokens(300,180,60,15),at=5))
        self.scan()
        self.assertEqual(self.summary()['total_tokens'],360)
        self.assertEqual(self.summary()['requests'],3)

    def test_snapshot_before_native_and_no_double_count(self):
        self.write(self.legacy(at=1),self.exact(at=2), self.legacy(at=3))
        self.scan()
        self.assertEqual(self.summary()['total_tokens'],120)
        self.assertEqual(self.summary()['exact_requests'],1)

    def test_duplicate_native_does_not_hide_later_legacy_usage(self):
        self.write(self.exact(),self.exact(),self.legacy(),
                   self.legacy(total=plus(tokens(),tokens()),at=4))
        self.scan()
        self.assertEqual(self.summary()['total_tokens'],240)
        self.assertEqual(self.summary()['requests'],2)

    def test_turn_numbers_stable_when_filtering(self):
        self.write(self.exact())
        self.write(event('turn_context',dict(turn_id='t2',model='other'),3),
                   self.exact('r2',turn='t2',root='t2',at=4))
        self.scan()
        api=API(self.con)
        self.assertEqual(api.session('s1',{'model':'other'})['turns'][0]['number'],2)

    def test_truncation_reimports_without_losing_or_duplicating_history(self):
        self.write(self.exact());self.scan()
        self.path.write_text('');self.start();self.write(self.exact('r2',at=5));self.scan()
        self.assertEqual(self.summary()['requests'],2)

    def test_first_legacy_inherited_baseline_only_counts_last(self):
        self.write(self.legacy(total=tokens(10000,9000,1000,300)))
        self.scan()
        self.assertEqual(self.summary()['total_tokens'],120)
        self.assertEqual(self.summary()['quality_warnings'],1)

    def test_hook_install_preserves_existing_and_uninstall(self):
        args=SimpleNamespace(codex_home=self.home,data_dir=self.data)
        old={'hooks':{'Stop':[{'hooks':[{'type':'command','command':'existing notifier'}]}]},'description':'keep me'}
        target=self.home/'hooks.json';target.write_text(json.dumps(old))
        install_hooks(args)
        installed=json.loads(target.read_text())
        self.assertEqual(installed['description'],'keep me')
        self.assertEqual(installed['hooks']['Stop'][0],old['hooks']['Stop'][0])
        install_hooks(args)
        self.assertEqual(json.loads(target.read_text()),installed)
        install_hooks(args,remove=True)
        self.assertEqual(json.loads(target.read_text()),old)


if __name__=='__main__':unittest.main()
