"""Exercise the shipped HTTP path, including persistence and write protection."""
import json
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


class PricingRuntimeTests(unittest.TestCase):
    def test_http_save_reprices_history_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            home, data = Path(temp)/'codex', Path(temp)/'data'
            (home/'sessions').mkdir(parents=True)
            records = [event('session_meta', dict(id='s', session_id='s')),
                       event('turn_context', dict(turn_id='t', model='gpt-6-astra')),
                       event('token_usage_record', dict(response_id='r', session_id='s', thread_id='s',
                             turn_id='t', root_turn_id='t', usage=tokens(100000,90000,10000,8000)), 1)]
            (home/'sessions/test.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def request(path, body=None, headers=None):
                req = urllib.request.Request('http://127.0.0.1:%d%s' % (port,path),
                    data=json.dumps(body).encode() if body is not None else None,
                    headers=headers if headers is not None else {'X-Token-Lens':'1','Content-Type':'application/json'})
                with opener.open(req, timeout=4) as response:
                    return json.load(response)

            def start():
                process = subprocess.Popen([sys.executable,'-B',str(ROOT/'app.py'),
                    '--data-dir',str(data),'--codex-home',str(home),'serve','--port',str(port)],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                self.addCleanup(lambda: process.poll() is None and (process.terminate(), process.wait(timeout=5)))
                for _ in range(60):
                    try:
                        if request('/api/overview')['summary']['requests']: return process
                    except OSError: pass
                    time.sleep(.1)
                self.fail('Server failed to start')

            server = start()
            before = request('/api/overview')['summary']
            self.assertAlmostEqual(before['costs']['usd']['total'], .69)
            config = request('/api/pricing')
            self.assertEqual(config['credit_reference']['credits'],50400)
            config['credit_reference'] = dict(name='测试套餐', credits=12600)
            astra = next(m for m in config['models'] if m['model']=='gpt-6-astra')
            astra['usd']['input_per_million'] = 20
            astra['credits']['cached_multiplier'] = .2
            for headers in ({'Content-Type':'application/json'},
                            {'Content-Type':'application/json','X-Token-Lens':'1','Origin':'https://evil.example'}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request('/api/pricing',config,headers)
                self.assertEqual(error.exception.code,403)
            saved = request('/api/pricing',config)
            self.assertNotEqual(saved['revision'],config['revision'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/pricing',config)
            self.assertEqual(error.exception.code,400)
            invalid = json.loads(json.dumps(saved)); invalid['models'][0]['usd']['input_per_million'] = -1
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/pricing',invalid)
            self.assertEqual(error.exception.code,400)
            self.assertEqual(request('/api/pricing')['revision'],saved['revision'])
            server.terminate(); server.wait(timeout=5)
            start()
            self.assertEqual(request('/api/pricing')['credit_reference'],config['credit_reference'])
            self.assertEqual(request('/api/overview')['credit_reference'],config['credit_reference'])
            after = request('/api/overview')['summary']
            self.assertEqual(before['total_tokens'],after['total_tokens'])
            self.assertEqual(before['requests'],after['requests'])
            self.assertAlmostEqual(after['costs']['usd']['total'],1.38)
            self.assertAlmostEqual(after['costs']['credits']['total'],19.5)
            for path in ('/api/sessions/s','/api/sessions/s/turns/t'):
                self.assertEqual(request(path)['credit_reference'],config['credit_reference'])
                self.assertEqual(request(path)['summary']['costs'],after['costs'])


if __name__ == '__main__':
    unittest.main()
