#!/usr/bin/env python3
"""Codex Token Lens: python3 app.py serve | sync | install-hooks."""
import argparse
import fcntl
import json
import logging
import mimetypes
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from codex_usage.api import API
from codex_usage.importer import Importer
from codex_usage.messages import MessagesUnavailable, RequestMessages
from codex_usage.store import connect

ROOT = Path(__file__).resolve().parent
HOOK_EVENTS = ('Stop', 'SubagentStop', 'Interrupt', 'SessionEnd')


def sync(args, quiet=False, progress=None):
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.data_dir / 'sync.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'busy': True}
        con = connect(args.data_dir)
        try:
            if getattr(args, 'rebuild', False):
                with con:
                    for table in ('usage', 'turns', 'threads', 'files', 'tool_events', 'usage_markers'):
                        con.execute('DELETE FROM ' + table)
            result = Importer(con, args.codex_home).scan(args.data_dir, progress=progress)
        finally:
            con.close()
    if not quiet:
        print(json.dumps(result, ensure_ascii=False))
    return result


def hook(args):
    # Do not retain messages, tool arguments, environment or arbitrary hook fields.
    try:
        payload = json.loads(sys.stdin.buffer.read(1024 * 1024))
        pending = args.data_dir / 'pending'
        pending.mkdir(parents=True, exist_ok=True, mode=0o700)
        event = dict(id=uuid.uuid4().hex, occurred_at=time.time(),
                     event=str(payload.get('hook_event_name', 'unknown'))[:80],
                     session_id=str(payload.get('session_id', ''))[:128],
                     turn_id=str(payload.get('turn_id', ''))[:128])
        temp = pending / (event['id'] + '.tmp')
        temp.write_text(json.dumps(event), encoding='utf-8')
        temp.replace(temp.with_suffix('.json'))
        command = [sys.executable, '-B', str(ROOT / 'app.py'), '--data-dir', str(args.data_dir),
                   '--codex-home', str(args.codex_home), 'hook-worker']
        log_dir = getattr(args, 'log_dir', None) or args.data_dir
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (log_dir / 'collector.log').open('ab') as log:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True)
    except Exception as exc:
        # Hook errors must never block, steer or add model context to a user's turn.
        print('Token Lens hook: ' + str(exc), file=sys.stderr)
    print('{}')


def hook_definition(args):
    command = getattr(args, 'hook_command', None) or shlex.join(
        [sys.executable, str(ROOT / 'app.py'), '--data-dir', str(args.data_dir),
         '--codex-home', str(args.codex_home), 'hook'])
    return {'hooks': {event: [{'hooks': [{'type': 'command', 'command': command, 'timeout': 3}]}]
                      for event in HOOK_EVENTS}}


def install_hooks(args, remove=False):
    path = args.codex_home / 'hooks.json'
    if remove and not path.exists():
        return
    current = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(current, dict) or not isinstance(current.get('hooks', {}), dict):
        raise ValueError('Existing hooks.json is not a valid hook object; left unchanged')
    hooks = current.setdefault('hooks', {})
    definitions = hook_definition(args)['hooks']
    commands = {v[0]['hooks'][0]['command'] for v in definitions.values()}
    commands.update(getattr(args, 'old_hook_commands', []))
    for event in HOOK_EVENTS:
        groups = []
        for group in hooks.get(event, []):
            kept = [h for h in group.get('hooks', []) if h.get('command') not in commands]
            if kept:
                groups.append({**group, 'hooks': kept})
        hooks[event] = groups + ([] if remove else definitions[event])
        if not hooks[event]:
            del hooks[event]
    encoded = json.dumps(current, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and json.loads(path.read_text()) == current:
        print('Hooks already match: ' + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name('hooks.json.token-lens-backup-' + str(time.time_ns()))
    if path.exists():
        shutil.copy2(path, backup)
    temp = path.with_name('hooks.json.token-lens-' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(encoded, encoding='utf-8')
        temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    print(('Removed' if remove else 'Installed') + ': ' + str(path))
    if not remove:
        print('Run codex in a terminal, then use /hooks inside Codex CLI to review and trust the definitions; existing notify/config.toml were preserved.')


def serve(args):
    # Create the schema before listening, but import history in the background.
    connect(args.data_dir).close()
    wake = threading.Event()
    progress = dict(running=True, completed=0, total=0)
    log_dir = getattr(args, 'log_dir', None) or args.data_dir
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    service_lock = (args.data_dir / 'service.lock').open('a')
    fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    service_id = getattr(args, 'service_id', '')
    version = (ROOT / 'VERSION').read_text().strip() if (ROOT / 'VERSION').exists() else 'dev'
    logger = logging.getLogger('token_lens.server')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_handler = RotatingFileHandler(log_dir / 'server.log', maxBytes=2*1024*1024,
                                     backupCount=2, encoding='utf-8')
    log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(log_handler)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *values):
            if values and str(values[1] if len(values) > 1 else '') not in ('200', '304'):
                # The terminal that launched this service may have disconnected.
                # HTTP responses must not depend on its stdout/stderr pipes.
                logger.info('%s %s', self.address_string(), fmt % values)

        def send(self, code, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def allowed(self):
            # Loopback binding plus Host validation prevents DNS-rebinding reads.
            host = self.headers.get('Host', '')
            return host in ('127.0.0.1:' + str(args.port), 'localhost:' + str(args.port))

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {'error': 'Local access only'})
            request = urlsplit(self.path)
            params = {k: v[-1] for k, v in parse_qs(request.query).items()}
            path = unquote(request.path)
            if path == '/api/health':
                return self.send(200, dict(app='token-lens', service_id=service_id,
                                          pid=os.getpid(), version=version, progress=progress.copy()))
            if path.startswith('/api/'):
                con = connect(args.data_dir)
                try:
                    api = API(con)
                    if path == '/api/overview':
                        result = api.overview(params)
                    elif path == '/api/options':
                        result = api.options()
                    elif path == '/api/status':
                        result = api.status()
                    elif path.startswith('/api/requests/') and path.endswith('/messages'):
                        bits = path.split('/')
                        if len(bits) != 5:
                            return self.send(404, {'error': 'Not found'})
                        result = RequestMessages(con, args.codex_home).read(bits[3], params.get('message'),
                                                                          int(params.get('offset', '0')))
                    elif path.startswith('/api/sessions/'):
                        bits = path.split('/')
                        if len(bits) == 4:
                            result = api.session(bits[3], params)
                        elif len(bits) == 6 and bits[4] == 'turns':
                            result = api.turn(bits[3], bits[5], params)
                        else:
                            return self.send(404, {'error': 'Not found'})
                    else:
                        return self.send(404, {'error': 'Not found'})
                    if path in ('/api/status', '/api/overview'):
                        status = result if path == '/api/status' else result['status']
                        status.update(progress=progress.copy(), version=version, data_dir=str(args.data_dir))
                    return self.send(200, result)
                except KeyError as e:
                    return self.send(404, {'error': str(e.args[0])})
                except MessagesUnavailable as e:
                    return self.send(410, {'error': str(e), 'code': e.code})
                except (ValueError, OverflowError):
                    return self.send(400, {'error': 'Invalid filter'})
                except Exception:
                    logger.exception('API request failed: %s', path)
                    return self.send(500, {'error': '读取失败，请查看本地服务日志'})
                finally:
                    con.close()
            assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/favicon.svg': 'favicon.svg'}
            if path not in assets:
                return self.send(404, {'error': 'Not found'})
            asset = ROOT / 'static' / assets[path]
            return self.send(200, asset.read_bytes(), (mimetypes.guess_type(str(asset))[0] or 'application/octet-stream') + '; charset=utf-8')

        def do_POST(self):
            if not self.allowed() or self.headers.get('Origin') not in (None, 'http://' + self.headers.get('Host', '')) or self.headers.get('X-Token-Lens') != '1':
                return self.send(403, {'error': 'Local access only'})
            if self.path != '/api/sync':
                return self.send(404, {'error': 'Not found'})
            requested_at = time.time()
            wake.set()
            return self.send(202, {'queued': True, 'requested_at': requested_at})

    def watch():
        while True:
            wake.wait(args.interval)
            wake.clear()
            try:
                progress.pop('error', None)
                result = sync(args, quiet=True, progress=progress.update)
                if result.get('busy'):
                    progress.update(running=False, waiting=True)
                else:
                    progress.pop('waiting', None)
            except Exception:
                progress.update(running=False, error='采集失败，请查看服务日志')
                logger.exception('Incremental scan failed')

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            logger.exception('HTTP handler failed for %s', client_address)

    server = Server(('127.0.0.1', args.port), Handler)
    if service_id:
        (args.data_dir / 'service.json').write_text(json.dumps(dict(pid=os.getpid(), service_id=service_id)))
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    wake.set()
    threading.Thread(target=watch, name='rollout-watcher', daemon=True).start()
    logger.info('Listening on http://127.0.0.1:%s', args.port)
    try:
        print('Token Lens → http://127.0.0.1:' + str(args.port), flush=True)
    except (OSError, ValueError):
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service_lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=lambda s: Path(s).expanduser().resolve(), default=ROOT / '.data')
    parser.add_argument('--codex-home', type=lambda s: Path(s).expanduser().resolve(), default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    parser.add_argument('--log-dir', type=lambda s: Path(s).expanduser().resolve())
    sub = parser.add_subparsers(dest='command', required=True)
    server = sub.add_parser('serve')
    server.add_argument('--port', type=int, default=8765)
    server.add_argument('--interval', type=float, default=3.0)
    server.add_argument('--service-id', default='')
    importer = sub.add_parser('sync')
    importer.add_argument('--rebuild', action='store_true', help='Rebuild the derived index; original Codex logs are read-only')
    for name in ('hook', 'hook-worker', 'hook-config', 'install-hooks', 'uninstall-hooks'):
        sub.add_parser(name)
    args = parser.parse_args()
    if args.command == 'serve':
        if not 1 <= args.port <= 65535:
            parser.error('--port must be between 1 and 65535')
        if args.interval < 0.5:
            parser.error('--interval must be at least 0.5 seconds')
        serve(args)
    elif args.command == 'sync':
        sync(args)
    elif args.command == 'hook':
        hook(args)
    elif args.command == 'hook-worker':
        # Let the writer flush the final response, then reconcile once more.
        time.sleep(0.25)
        sync(args, quiet=True)
        time.sleep(1)
        sync(args, quiet=True)
    elif args.command == 'hook-config':
        print(json.dumps(hook_definition(args), ensure_ascii=False, indent=2))
    else:
        install_hooks(args, remove=args.command == 'uninstall-hooks')


if __name__ == '__main__':
    main()
