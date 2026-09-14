"""Runtime integration: run explicitly; uses loopback sockets and child processes."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from test_usage import event, tokens

ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def test_messages_api_recovers_after_rewrite_and_keeps_response_contract(self):
        from test_messages import message, native, call, result
        from codex_usage.importer import Importer
        from codex_usage.store import connect
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'codex'; data = Path(temp) / 'data'
            (home / 'sessions').mkdir(parents=True)
            rollout = home / 'sessions/sample.jsonl'
            records = [event('session_meta', dict(id='s', session_id='s')),
                       event('turn_context', dict(turn_id='t', model='test')),
                       message('user', '<environment_context>runtime</environment_context>\nrequest'),
                       call(), native('r1'), result(text='tool result'),
                       message('assistant', 'answer'), native('r2', 2)]
            rollout.write_text(''.join(json.dumps(r) + '\n' for r in records))
            con = connect(data); Importer(con, home).scan(data); con.close()
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
            server = subprocess.Popen([sys.executable, '-B', str(ROOT/'app.py'),
                '--codex-home', str(home), '--data-dir', str(data), 'serve',
                '--port', str(port), '--interval', '60'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(route, method='GET'):
                req = urllib.request.Request('http://127.0.0.1:%s%s' % (port, route),
                    method=method, headers={'X-Token-Lens':'1'})
                with opener.open(req, timeout=3) as response:
                    return response.status, json.load(response)
            try:
                for _ in range(60):
                    try:
                        if request('/api/status')[1].get('scan'): break
                    except OSError: pass
                    time.sleep(.1)
                else: self.fail('Initial scan did not complete')
                # Live file rewrite leaves the stored position invalid until a refresh.
                rollout.write_text(json.dumps(event('ignored', dict(padding='x'*2000))) + '\n' + rollout.read_text())
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request('/api/requests/response%3Ar1/messages')
                self.assertEqual(error.exception.code, 410)
                _, queued = request('/api/sync', 'POST')
                for _ in range(60):
                    if request('/api/status')[1]['scan']['started_at'] >= queued['requested_at']: break
                    time.sleep(.1)
                else: self.fail('Refresh did not complete')
                code, first = request('/api/requests/response%3Ar1/messages')
                self.assertEqual(code, 200)
                for key in ('context','input','output','tool_results'):
                    self.assertIsInstance(first[key], list)
                self.assertEqual(first['input'][0]['text'], 'request')
                self.assertEqual(first['output'][0]['name'], 'read_file')
                self.assertEqual(first['tool_results'][0]['text'], 'tool result')
                second = request('/api/requests/response%3Ar2/messages')[1]
                self.assertEqual(second['context'][0]['text'], 'request')
                self.assertEqual(second['output'][0]['text'], 'answer')
                turn = request('/api/sessions/s/turns/t')[1]
                self.assertEqual(turn['tools']['calls'], 1)
                self.assertEqual(turn['tools']['output_chars'], len('tool result'))
                self.assertEqual(turn['requests'][0]['tools']['tools'][0]['name'], 'read_file')
                self.assertEqual(request('/api/overview')[1]['summary']['total_tokens'], 240)
            finally:
                server.terminate(); server.wait(timeout=5)

    def test_refresh_survives_disconnected_terminal_and_waits_for_new_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=root/'codex'; data=root/'data'
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            server=subprocess.Popen([sys.executable,'-B',str(ROOT/'app.py'),
                '--codex-home',str(home),'--data-dir',str(data),'serve',
                '--port',str(port),'--interval','60'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            base='http://127.0.0.1:'+str(port)
            def request(path,method='GET'):
                req=urllib.request.Request(base+path,method=method,headers={'X-Token-Lens':'1'})
                with urllib.request.urlopen(req,timeout=3) as r:return r.status,json.load(r)
            try:
                for _ in range(40):
                    try:request('/api/status');break
                    except OSError:time.sleep(.1)
                else:self.fail('Server did not start')
                # This reproduced the production failure: GET 200 survived, but
                # logging POST 202 to the dead terminal broke the response.
                server.stdout.close();server.stderr.close()
                (home/'sessions').mkdir(parents=True)
                records=[event('session_meta',dict(id='s',session_id='s',cwd='/test')),
                    event('turn_context',dict(turn_id='t',model='test')),
                    event('token_usage_record',dict(thread_id='s',session_id='s',turn_id='t',
                        root_turn_id='t',response_id='r',usage=tokens(),thread_token_usage=tokens()),1)]
                (home/'sessions'/'sample.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
                code,queued=request('/api/sync','POST')
                self.assertEqual(code,202)
                for _ in range(40):
                    _,status=request('/api/status')
                    if status['scan']['started_at']>=queued['requested_at']:break
                    time.sleep(.1)
                else:self.fail('Manual refresh did not wake the scanner')
                self.assertEqual(request('/api/overview')[1]['summary']['total_tokens'],120)
                with self.assertRaises(urllib.error.HTTPError) as ctx:request('/does-not-exist')
                self.assertEqual(ctx.exception.code,404)
                self.assertIn('POST /api/sync',(data/'server.log').read_text())
            finally:
                server.terminate();server.wait(timeout=5)
                if not server.stdout.closed:server.stdout.close()
                if not server.stderr.closed:server.stderr.close()

    def test_hook_collects_without_server_then_api_drills_down(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); home=root/'codex'; data=root/'data'
            (home/'sessions').mkdir(parents=True)
            rollout=home/'sessions'/'sample.jsonl'
            records=[event('session_meta',dict(id='s',session_id='s',cwd='/test',thread_source='user')),
                     event('turn_context',dict(turn_id='t',model='test-model')),
                     event('token_usage_record',dict(thread_id='s',session_id='s',turn_id='t',root_turn_id='t',response_id='r',usage=tokens(),thread_token_usage=tokens()),1)]
            rollout.write_text(''.join(json.dumps(r)+'\n' for r in records))
            command=[sys.executable,'-B',str(ROOT/'app.py'),'--codex-home',str(home),'--data-dir',str(data)]
            payload={'hook_event_name':'Stop','session_id':'s','turn_id':'t','last_assistant_message':'SECRET-DO-NOT-SAVE'}
            started=time.monotonic()
            result=subprocess.run(command+['hook'],input=json.dumps(payload),text=True,capture_output=True,timeout=3)
            self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout),{})
            self.assertLess(time.monotonic()-started,2)
            for _ in range(60):
                if (data/'usage.sqlite3').exists():
                    import sqlite3
                    c=sqlite3.connect(str(data/'usage.sqlite3'))
                    try:
                        if c.execute('SELECT COUNT(*) FROM usage').fetchone()[0]==1:break
                    except sqlite3.Error:pass
                    finally:c.close()
                time.sleep(.1)
            else:self.fail('Hook did not collect usage independently')
            self.assertNotIn(b'SECRET-DO-NOT-SAVE',(data/'usage.sqlite3').read_bytes())
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            server=subprocess.Popen(command+['serve','--port',str(port),'--interval','0.5'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            base='http://127.0.0.1:'+str(port)
            def request(path,headers=None,method='GET'):
                req=urllib.request.Request(base+path,headers=headers or {},method=method)
                with urllib.request.urlopen(req,timeout=5) as r:return r.status,r.read()
            try:
                for _ in range(40):
                    try:
                        code,body=request('/api/overview');break
                    except (OSError,urllib.error.URLError):time.sleep(.1)
                else:self.fail('Server did not start')
                self.assertEqual(json.loads(body)['summary']['total_tokens'],120)
                self.assertEqual(json.loads(request('/api/sessions/s')[1])['turns'][0]['id'],'t')
                self.assertEqual(json.loads(request('/api/sessions/s/turns/t')[1])['requests'][0]['response_id'],'r')
                messages=json.loads(request('/api/requests/response%3Ar/messages')[1])
                self.assertEqual(messages['request_id'],'response:r')
                self.assertEqual(messages['association'],'transcript_interval')
                self.assertEqual(json.loads(request('/api/status')[1])['hooks_received'],1)
                self.assertIn(b'Token Lens',request('/')[1])
                for path,headers,method,code in [('/api/overview',{'Host':'evil.example'},'GET',403),
                         ('/api/sync',{'Origin':'https://evil.example','X-Token-Lens':'1'},'POST',403),
                         ('/api/sync',{},'POST',403),('/../app.py',{},'GET',404),
                         ('/api/overview?from=not-a-date',{},'GET',400)]:
                    with self.assertRaises(urllib.error.HTTPError) as ctx:request(path,headers,method)
                    self.assertEqual(ctx.exception.code,code)
                self.assertEqual(request('/api/sync',{'X-Token-Lens':'1'},'POST')[0],202)
                records[-1]['payload']['response_id']='r2'
                with rollout.open('a') as f:f.write(json.dumps(records[-1])+'\n')
                for _ in range(40):
                    if json.loads(request('/api/overview')[1])['summary']['requests']==2:break
                    time.sleep(.1)
                else:self.fail('Watcher did not ingest appended usage')
            finally:
                server.terminate();server.communicate(timeout=5)
                # The independent hook worker has two reconciliation passes.
                time.sleep(1.5)


if __name__=='__main__':unittest.main()
