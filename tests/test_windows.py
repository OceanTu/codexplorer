"""Native Windows regression tests; no writes to the user's Codex directory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PlatformTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows installation directory')
    def test_default_root_uses_per_user_programs_directory(self):
        from codex_usage.lifecycle import default_root
        with patch.dict(os.environ, {'LOCALAPPDATA': str(ROOT)}):
            self.assertEqual(default_root(), ROOT / 'Programs/Token Lens')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows native hooks')
    def test_migration_recognizes_native_windows_source_hook(self):
        from codex_usage.installation import legacy_commands
        from codex_usage.platform_support import command_line
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, home = base / 'source', base / 'codex'
            home.mkdir()
            command = command_line([sys.executable, str(source / 'app.py'), '--data-dir',
                                    str(source / '.data'), '--codex-home', str(home), 'hook'])
            (home / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [
                {'hooks': [{'type': 'command', 'command': command}]}]}}), encoding='utf-8')
            self.assertEqual(legacy_commands(source, home), [command])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows installer entry')
    def test_source_cmd_installs_independently_and_management_cmd_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / '安装 空格'
            home = base / 'Codex 日志'
            home.mkdir()
            command = [str(ROOT / 'install.cmd'), '--yes', '--no-start', '--no-open',
                       '--no-shortcuts', '--no-hooks', '--install-dir', str(root),
                       '--codex-home', str(home)]
            result = subprocess.run(command, shell=True, capture_output=True,
                text=True, encoding='utf-8', timeout=60,
                env={**os.environ, 'TOKEN_LENS_PYTHON': sys.executable, 'PYTHONUTF8': '1'})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(root / 'bin/token-lens.cmd'), 'doctor'], shell=True,
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            info = json.loads(result.stdout)
            self.assertFalse(info['service_running'])
            self.assertEqual(info['install_dir'], str(root))
            self.assertTrue((root / 'current').is_file())

    @unittest.skipUnless(sys.platform == 'win32', 'Windows batch launcher')
    def test_batch_launcher_handles_unicode_spaces_and_shell_characters(self):
        from codex_usage.platform_support import windows_script
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / '中文 空格 % ! &'
            root.mkdir()
            script = root / 'echo.py'
            script.write_text('import json,sys; print(json.dumps(sys.argv[1:]))', encoding='utf-8')
            launcher = root / 'launch.cmd'
            launcher.write_text(windows_script([sys.executable, str(script), str(root)], 'test'), encoding='utf-8')
            result = subprocess.run([str(launcher), 'status'], shell=True,
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout), [str(root), 'status'])

    def test_native_app_can_start(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'app.py'), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lock_contends_across_processes_and_releases_on_close(self):
        from codex_usage.platform_support import lock_exclusive
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'lock'
            script = ('from codex_usage.platform_support import lock_exclusive; '
                      'import sys; f=open(sys.argv[1], "a+b"); lock_exclusive(f)')
            with path.open('a+b') as lock:
                lock_exclusive(lock)
                child = subprocess.run([sys.executable, '-B', '-c', script, str(path)],
                                       cwd=ROOT, capture_output=True, text=True)
                self.assertNotEqual(child.returncode, 0)
                self.assertIn('BlockingIOError', child.stderr)
            child = subprocess.run([sys.executable, '-B', '-c', script, str(path)],
                                   cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(child.returncode, 0, child.stderr)

    def test_windows_zip_reproducible_and_verifiable(self):
        from scripts.build_release import build
        from codex_usage.installation import verify_package
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first = build(base / 'a', platform='windows')
            second = build(base / 'b', platform='windows')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertTrue(first.name.endswith('-windows.zip'))
            with zipfile.ZipFile(first) as archive:
                archive.extractall(base / '解压 包')
            package = next((base / '解压 包').iterdir())
            self.assertTrue((package / 'install.cmd').is_file())
            self.assertEqual(json.loads((package / 'manifest.json').read_text())['platform'], 'windows')
            self.assertEqual(verify_package(package)[0], (ROOT / 'VERSION').read_text().strip())


if __name__ == '__main__':
    unittest.main()
