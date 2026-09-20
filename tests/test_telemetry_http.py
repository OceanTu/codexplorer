import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from test_telemetry import payload

class IntakeHTTPTests(unittest.TestCase):
    def test_authenticated_ingestion_replay_and_bad_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'codex';home.mkdir();data=root/'data';data.mkdir()
            (data/'telemetry-token').write_text('test-secret')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            server=subprocess.Popen([sys.executable,'-B','app.py','--codex-home',str(home),'--data-dir',str(data),'serve','--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            base='http://127.0.0.1:'+str(port)
            def post(body,token='test-secret',origin=None):
                headers={'Content-Type':'application/json','X-Token-Lens-Key':token}
                if origin:headers['Origin']=origin
                req=urllib.request.Request(base+'/v1/logs',data=json.dumps(body).encode(),headers=headers)
                with urllib.request.urlopen(req,timeout=3) as r:return r.status,json.load(r)
            try:
                for _ in range(50):
                    try:
                        urllib.request.urlopen(base+'/api/status',timeout=1).close();break
                    except OSError:time.sleep(.1)
                self.assertEqual(post(payload())[0],200)
                self.assertEqual(post(payload())[0],200)
                with urllib.request.urlopen(base+'/api/status') as r:status=json.load(r)
                self.assertEqual(status['supplemental']['telemetry_events'],1)
                for body,token,origin,code in [(payload(),'bad',None,403),(payload(),'test-secret','https://evil.test',403),({},'test-secret',None,400),({'resourceLogs':[None]},'test-secret',None,400)]:
                    with self.assertRaises(urllib.error.HTTPError) as e:post(body,token,origin)
                    self.assertEqual(e.exception.code,code)
            finally:
                server.terminate();server.wait(timeout=5)
