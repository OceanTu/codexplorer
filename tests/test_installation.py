"""Installation integration: isolated data/home paths; no user hooks or shortcuts."""
import hashlib
import json
import os
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from codex_usage import lifecycle
from codex_usage.store import SCHEMA_VERSION
from codex_usage.installation import backup_database, verify_package
from scripts.build_release import ROOT, RUNTIME, build
from test_usage import event, tokens

BASE_VERSION = (ROOT / 'VERSION').read_text().strip()
NEXT_VERSION = '.'.join(BASE_VERSION.split('.')[:-1] + [str(int(BASE_VERSION.split('.')[-1]) + 1)])
BROKEN_VERSION = '.'.join(BASE_VERSION.split('.')[:-1] + [str(int(BASE_VERSION.split('.')[-1]) + 2)])


@unittest.skipUnless(sys.platform == 'darwin', 'macOS installer integration')
class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='token-lens-install-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "安装 空间 ' $ [data]"
        self.codex = self.base / 'Codex 日志'
        (self.codex / 'sessions').mkdir(parents=True)
        self.rollout = self.codex / 'sessions/test.jsonl'
        records = [event('session_meta', dict(id='s', session_id='s', cwd='/test')),
                   event('turn_context', dict(turn_id='t', model='test')),
                   event('token_usage_record', dict(thread_id='s', session_id='s', turn_id='t',
                         root_turn_id='t', response_id='r', usage=tokens(), thread_token_usage=tokens()), 1)]
        self.rollout.write_text(''.join(json.dumps(r) + '\n' for r in records))
        self.original_hooks = {'description': 'keep me', 'hooks': {'Stop': [
            {'hooks': [{'type': 'command', 'command': 'existing notifier'}]}]}}
        (self.codex / 'hooks.json').write_text(json.dumps(self.original_hooks))
        self.package = self.extract(build(self.base / 'release'))
        self.addCleanup(self.stop_service)

    def extract(self, archive):
        folder = self.base / ('解压 包 ' + str(time.time_ns()))
        with zipfile.ZipFile(archive) as source:
            source.extractall(folder)
        return next(folder.iterdir())

    def stop_service(self):
        if (self.root / 'config.json').exists():
            config = lifecycle.read_config(self.root)
            lifecycle.stop(self.root, config)
            lifecycle.agent_path(config).unlink(missing_ok=True)

    def install(self, *extra, package=None, success=True):
        command = ['/bin/sh', str((package or self.package) / 'install.sh'), '--yes',
                   '--no-open', '--no-shortcuts', '--install-dir', str(self.root),
                   '--codex-home', str(self.codex), *extra]
        result = subprocess.run(command, capture_output=True, text=True, timeout=40,
                                env={**os.environ, 'TOKEN_LENS_PYTHON': sys.executable})
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def manage(self, *command):
        result = subprocess.run([str(self.root / 'bin/token-lens'), *command],
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def api(self, route):
        config = lifecycle.read_config(self.root)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:%s%s' % (config['port'], route), timeout=3) as response:
            return json.load(response)

    def wait_usage(self):
        for _ in range(80):
            data = self.api('/api/overview')
            if data['summary']['requests']:
                return data
            time.sleep(.1)
        self.fail('Background import did not complete')

    def variant(self, version, broken=False):
        source = self.base / ('source-' + version)
        source.mkdir()
        for name in list(RUNTIME) + ['install.sh', 'install.command', 'installer.py', 'packaging/README.md']:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (source / 'VERSION').write_text(version + '\n')
        if broken:
            path = source / 'app.py'
            content = path.read_text().replace("    if args.command == 'serve':", """    if args.command == 'serve':
        con = connect(args.data_dir)
        con.execute('DELETE FROM usage')
        con.execute('PRAGMA user_version=99')
        con.commit()
        con.close()
        raise ValueError('Simulated startup failure after database change')""")
            path.write_text(content)
        return self.extract(build(self.base / ('release-' + version), source))

    def test_install_delete_download_hook_upgrade_and_uninstall(self):
        # A busy foreign listener is left untouched; our URL is persisted elsewhere.
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            port = busy.getsockname()[1]
            self.install('--port', str(port))
            self.assertNotEqual(lifecycle.read_config(self.root)['port'], port)
        data = self.wait_usage()
        self.assertEqual(data['summary']['total_tokens'], 120)
        self.assertEqual(data['status']['version'], BASE_VERSION)
        from codex_usage.pricing import Pricing
        from codex_usage.store import connect
        con = connect(self.root / 'data')
        pricing = Pricing(con)
        configuration = pricing.settings()
        next(m for m in configuration['models'] if m['model']=='test')['usd']['input_per_million'] = 9
        saved_revision = pricing.save(configuration)['revision']
        con.close()
        before = (self.codex / 'hooks.json').read_bytes()
        self.install()  # repeat installation does not duplicate hooks or usage
        self.assertEqual((self.codex / 'hooks.json').read_bytes(), before)
        self.assertEqual(self.wait_usage()['summary']['total_tokens'], 120)
        shutil.rmtree(self.package.parent)
        shutil.rmtree(self.base / 'release')
        self.manage('restart')
        self.assertEqual(self.api('/api/health')['version'], BASE_VERSION)
        self.manage('stop')
        hook = json.loads(before)['hooks']['Stop'][-1]['hooks'][0]['command']
        result = subprocess.run(shlex.split(hook), input=json.dumps(dict(hook_event_name='Stop', session_id='s')),
                                text=True, capture_output=True, timeout=3)
        self.assertEqual(json.loads(result.stdout), {})
        for _ in range(60):
            con = sqlite3.connect(self.root / 'data/usage.sqlite3')
            count = con.execute('SELECT COUNT(*) FROM hook_events').fetchone()[0]
            con.close()
            if count:
                break
            time.sleep(.1)
        self.assertEqual(count, 1)
        new = self.variant(NEXT_VERSION)
        self.install(package=new)
        self.assertEqual(self.api('/api/health')['version'], NEXT_VERSION)
        self.assertEqual(self.api('/api/pricing')['revision'], saved_revision)
        self.assertGreater(self.api('/api/overview')['summary']['costs']['usd']['total'], 0)
        self.assertEqual((self.codex / 'hooks.json').read_bytes(), before)
        self.assertEqual(self.wait_usage()['summary']['total_tokens'], 120)
        self.assertTrue(list((self.root / 'backups').glob('*/usage.sqlite3')))
        self.manage('uninstall')
        self.assertEqual(json.loads((self.codex / 'hooks.json').read_text()), self.original_hooks)
        self.assertTrue((self.root / 'data/usage.sqlite3').exists())
        self.assertTrue(self.rollout.exists())
        self.assertFalse((self.root / 'current').exists())
        self.assertFalse((self.root / 'versions').exists())
        self.install(package=new)
        self.assertEqual(self.wait_usage()['summary']['total_tokens'], 120)

    def test_failed_upgrade_restores_database_hooks_and_running_version(self):
        self.install()
        self.wait_usage()
        before = (self.codex / 'hooks.json').read_bytes()
        broken = self.variant(BROKEN_VERSION, broken=True)
        self.install(package=broken, success=False)
        self.assertEqual(self.api('/api/health')['version'], BASE_VERSION)
        self.assertEqual(self.wait_usage()['summary']['total_tokens'], 120)
        self.assertEqual((self.codex / 'hooks.json').read_bytes(), before)
        con = sqlite3.connect(self.root / 'data/usage.sqlite3')
        self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        con.close()

    def test_migrate_snapshot_removes_only_legacy_hook(self):
        legacy = self.base / '旧 开发目录'
        legacy.mkdir()
        command = [sys.executable, str(ROOT / 'app.py'), '--data-dir', str(legacy / '.data'),
                   '--codex-home', str(self.codex), 'sync']
        subprocess.run(command, capture_output=True, check=True)
        old_command = shlex.join([sys.executable, str(legacy / 'app.py'), '--data-dir', str(legacy / '.data'),
                                 '--codex-home', str(self.codex), 'hook'])
        hooks = json.loads((self.codex / 'hooks.json').read_text())
        hooks['hooks']['Stop'][0]['hooks'].append(dict(type='command', command=old_command))
        (self.codex / 'hooks.json').write_text(json.dumps(hooks))
        self.install('--migrate-from', str(legacy))
        self.assertEqual(self.wait_usage()['summary']['total_tokens'], 120)
        self.assertTrue((legacy / '.data/usage.sqlite3').exists())
        result = (self.codex / 'hooks.json').read_text()
        self.assertNotIn(old_command, result)
        self.assertIn('existing notifier', result)

    def test_manifest_and_newer_schema_rejected_before_mutation(self):
        (self.package / 'payload/static/app.js').write_text('tampered')
        result = self.install(success=False)
        self.assertIn('校验失败', result.stderr)
        self.assertFalse(self.root.exists())
        self.package = self.extract(build(self.base / 'release'))
        self.install('--no-start')
        from codex_usage.store import connect
        con = connect(self.root / 'data')
        con.execute('PRAGMA user_version=99')
        con.close()
        config = (self.root / 'config.json').read_bytes()
        result = self.install(success=False)
        self.assertIn('拒绝降级', result.stderr)
        self.assertEqual((self.root / 'config.json').read_bytes(), config)

    def test_first_install_failure_can_retry_and_preserves_other_hooks(self):
        original = (self.codex / 'hooks.json').read_bytes()
        result = self.install('--codex-home', str(self.base / 'missing'), success=False)
        self.assertIn('找不到 Codex', result.stderr)
        broken = self.variant(BROKEN_VERSION, broken=True)
        self.install(package=broken, success=False)
        self.assertEqual((self.codex / 'hooks.json').read_bytes(), original)
        self.assertFalse((self.root / 'current').exists())
        self.install()
        self.assertEqual(self.wait_usage()['summary']['total_tokens'], 120)

    def test_autostart_load_stop_restart_disable(self):
        self.install('--autostart')
        config = lifecycle.read_config(self.root)
        self.assertTrue(lifecycle.agent_path(config).is_file())
        self.assertTrue(lifecycle.health(config))
        self.manage('stop')
        self.assertFalse(lifecycle.health(config))
        self.manage('start')
        self.assertEqual(lifecycle.read_config(self.root)['port'], config['port'])
        self.assertTrue(lifecycle.health(config))
        self.manage('autostart', 'off')
        config = lifecycle.read_config(self.root)
        self.assertFalse(config['autostart'])
        self.assertFalse(lifecycle.agent_path(config).exists())
        self.assertTrue(lifecycle.health(config))


class PackageTests(unittest.TestCase):
    def test_zip_is_reproducible_allowlisted_and_has_executable_installer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = build(root / 'a')
            second = build(root / 'b')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertIn(hashlib.sha256(first.read_bytes()).hexdigest(), first.with_suffix('.zip.sha256').read_text())
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                self.assertFalse(any(part in name.split('/') for name in names
                                     for part in ('.data', 'reports', 'analysis', '__pycache__', 'auth.json')))
                self.assertEqual(len(names), len(RUNTIME) + 6)
                self.assertTrue(archive.getinfo(names[0].split('/')[0] + '/install.command').external_attr >> 16 & 0o111)
                archive.extractall(root / 'unpacked')
            self.assertEqual(verify_package(next((root / 'unpacked').iterdir()))[0], BASE_VERSION)
            package = next((root / 'unpacked').iterdir())
            (package / '.DS_Store').write_bytes(b'Finder metadata')
            self.assertEqual(verify_package(package)[0], BASE_VERSION)

    def test_stale_pid_cannot_stop_unrelated_process(self):
        with patch('codex_usage.lifecycle.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '/usr/bin/python3 another-app.py'
            self.assertFalse(lifecycle.owned_process(123, 'test-installation'))

    def test_backup_includes_live_wal(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.db'
            con = sqlite3.connect(source)
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('CREATE TABLE example(value)')
            con.execute('INSERT INTO example VALUES (42)')
            con.commit()
            target = Path(temp) / 'backup.db'
            backup_database(source, target)
            check = sqlite3.connect(target)
            self.assertEqual(check.execute('SELECT value FROM example').fetchone()[0], 42)
            check.close()
            con.close()
