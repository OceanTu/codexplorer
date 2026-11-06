"""Per-user installation and process management; no shell process matching."""
import argparse
import contextlib
import json
import os
import plistlib
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace
from .platform_support import (WINDOWS, background_options, command_line, current_dir,
    launcher_path, lock_exclusive, windows_script)


def default_root():
    if WINDOWS:
        # LocalAppData/Programs is the native per-user software location and
        # avoids MSIX LocalAppData virtualization in packaged parent apps.
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Programs/Token Lens'
    return Path.home() / 'Library/Application Support/Token Lens'


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        if isinstance(content, bytes):
            temp.write_bytes(content)
        else:
            temp.write_text(content, encoding='utf-8')
        temp.chmod(mode)
        # Windows indexing/antimalware may briefly open a newly written script
        # without delete sharing. Keep the atomic replace, with a bounded retry.
        for attempt in range(21):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if not WINDOWS or attempt == 20:
                    raise
                time.sleep(.05)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def read_config(root):
    config = json.loads((root / 'config.json').read_text(encoding="utf-8"))
    marker = json.loads((root / '.token-lens-install.json').read_text(encoding="utf-8"))
    if marker.get('app') != 'token-lens' or marker.get('service_id') != config.get('service_id'):
        raise ValueError('安装标记不匹配，请重新运行安装包修复')
    return config


@contextlib.contextmanager
def management_lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'management.lock').open('a') as lock:
        try:
            lock_exclusive(lock)
        except BlockingIOError:
            raise ValueError('另一个安装或管理操作正在运行，请稍后重试')
        yield


def health(config):
    try:
        # Explicitly bypass proxy environment variables for this loopback request.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:%s/api/health' % config['port'], timeout=1) as response:
            result = json.load(response)
        if result.get('app') == 'token-lens' and result.get('service_id') == config['service_id']:
            return result
    except (OSError, ValueError):
        pass
    return None


def owned_process(pid, service_id):
    if not isinstance(pid, int) or pid <= 1:
        return False
    if WINDOWS:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
            '(Get-CimInstance Win32_Process -Filter "ProcessId = %d").CommandLine' % pid],
            capture_output=True, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10)
    else:
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='],
                                capture_output=True, text=True)
    command = result.stdout.strip()
    # The UUID is unique to this installation. Never trust a stale PID alone.
    return bool(result.returncode == 0 and
                command.endswith('--service-id ' + service_id) and re.search(r'app\.py"?\s', command))


def process_id(root, config):
    try:
        record = json.loads((root / 'data/service.json').read_text(encoding="utf-8"))
        if record.get('service_id') == config['service_id'] and owned_process(record.get('pid'), config['service_id']):
            return record['pid']
    except (OSError, ValueError):
        pass
    info = health(config)
    return info['pid'] if info and owned_process(info.get('pid'), config['service_id']) else None


def choose_port(preferred):
    candidates = [preferred] + [p for p in range(8765, 8865) if p != preferred]
    for port in candidates:
        with socket.socket() as sock:
            if WINDOWS:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise ValueError('没有可用的本机端口，请用安装参数 --port 指定其他端口')


def app_command(root, config, command):
    return [config['python'], '-X', 'utf8', '-B', str(current_dir(root) / 'app.py'),
            '--data-dir', str(root / 'data'), '--codex-home', config['codex_home'],
            '--log-dir', str(root / 'logs'), command]


def agent_path(config):
    if WINDOWS:
        return (Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) /
                'Microsoft/Windows/Start Menu/Programs/Startup' / (config['launch_label'] + '.vbs'))
    return Path.home() / 'Library/LaunchAgents' / (config['launch_label'] + '.plist')


def launchctl(*args):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True)


def unload_agent(config):
    if sys.platform == 'darwin':
        launchctl('bootout', 'gui/%s/%s' % (os.getuid(), config['launch_label']))


def load_agent(root, config):
    if WINDOWS:
        # Startup runs a script with a hidden window, including the manager
        # process that checks whether an instance is already running.
        arguments = [config['python'], '-X', 'utf8', '-B', str(current_dir(root) / 'manage.py'),
                     '--install-dir', str(root), 'start']
        command = subprocess.list2cmdline(arguments).replace('"', '""')
        atomic_write(agent_path(config), ("' Token Lens: " + config['service_id'] + '\n' +
                     'CreateObject("WScript.Shell").Run "' + command + '", 0, False\n').encode('utf-16'))
        return
    if sys.platform != 'darwin':
        raise ValueError('登录自启目前只支持 macOS')
    path = agent_path(config)
    value = dict(Label=config['launch_label'],
                 ProgramArguments=[str(root / 'bin/token-lens'), 'run-service'],
                 WorkingDirectory=str(root), RunAtLoad=True, KeepAlive=True,
                 ThrottleInterval=10, StandardOutPath=str(root / 'logs/service-output.log'),
                 StandardErrorPath=str(root / 'logs/service-output.log'))
    atomic_write(path, plistlib.dumps(value))
    result = launchctl('bootstrap', 'gui/%s' % os.getuid(), str(path))
    if result.returncode:
        raise ValueError('登录自启加载失败：' + (result.stderr.strip() or result.stdout.strip()))


def start(root, config):
    info = health(config)
    if info:
        return info
    if process_id(root, config):
        raise ValueError('服务进程仍在运行但未响应，请运行 restart 或 doctor')
    port = choose_port(config['port'])
    if port != config['port']:
        print('原端口被占用，改用 %s。' % port)
        config['port'] = port
        write_json(root / 'config.json', config)
    (root / 'logs').mkdir(parents=True, exist_ok=True, mode=0o700)
    child = None
    if config.get('autostart'):
        unload_agent(config)
        load_agent(root, config)
    if not config.get('autostart') or WINDOWS:
        # Rotate detached process output before launch; application logs rotate themselves.
        output = root / 'logs/service-output.log'
        if output.exists() and output.stat().st_size > 2 * 1024 * 1024:
            output.replace(output.with_suffix('.log.1'))
        with output.open('ab') as log:
            child = subprocess.Popen(app_command(root, config, 'serve') +
                ['--port', str(config['port']), '--service-id', config['service_id']],
                cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                **background_options())
    for _ in range(100):
        info = health(config)
        if info:
            return info
        if child and child.poll() is not None:
            break
        time.sleep(.1)
    if child and child.poll() is None:
        child.terminate()
        child.wait(timeout=5)
    if config.get('autostart'):
        unload_agent(config)
    raise ValueError('服务启动失败，详见 ' + str(root / 'logs/service-output.log'))


def stop(root, config):
    pid = process_id(root, config)
    unload_agent(config)
    if pid and owned_process(pid, config['service_id']):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(100):
            if not owned_process(pid, config['service_id']):
                break
            time.sleep(.1)
        else:
            raise ValueError('服务未及时退出，已保留安装文件，请运行 doctor 检查')
    (root / 'data/service.json').unlink(missing_ok=True)


def hook_args(root, config, old_commands=()):
    command = ([config['python'], '-X', 'utf8', '-B', str(root / 'bin/manage.py'),
                '--install-dir', str(root), 'hook'] if WINDOWS else [str(root / 'bin/token-lens'), 'hook'])
    return SimpleNamespace(data_dir=root / 'data', codex_home=Path(config['codex_home']),
                           hook_command=command_line(command),
                           old_hook_commands=old_commands)


def set_hooks(root, config, remove=False, old_commands=()):
    from app import install_hooks
    install_hooks(hook_args(root, config, old_commands), remove=remove)


def install_launchers(root, config, shortcuts=True):
    if WINDOWS:
        # Stable bootstrap resolves the active version on every invocation, so
        # installed hook commands do not change or need re-trusting on upgrades.
        atomic_write(root / 'bin/manage.py',
            'import sys\nfrom pathlib import Path\n'
            'root = Path(__file__).resolve().parents[1]\n'
            'target = (root / "current").read_text(encoding="utf-8").strip()\n'
            'version = (root / target).resolve()\n'
            'if not version.is_relative_to((root / "versions").resolve()):\n'
            '    raise ValueError("Invalid version pointer")\n'
            'sys.path.insert(0, str(version))\n'
            'from codex_usage.lifecycle import main\nmain()\n')
        arguments = [config['python'], '-X', 'utf8', '-B', str(root / 'bin/manage.py'),
                     '--install-dir', str(root)]
        atomic_write(launcher_path(root), windows_script(arguments, config['service_id']))
        if shortcuts:
            shortcut = shortcut_path()
            marker = 'rem Token Lens managed shortcut: ' + config['service_id']
            if not shortcut.exists() or marker in shortcut.read_text(encoding='utf-8'):
                # CMD uses the system codepage to read batch files. UTF-8 is
                # selected by the first ASCII-only line before Unicode paths.
                atomic_write(shortcut, '@chcp 65001 >nul\n' + windows_script(arguments + ['open'], config['service_id']))
            else:
                print('保留已有快捷入口：' + str(shortcut))
        return
    launcher = '#!/bin/sh\n# Token Lens managed launcher\nexec ' + shlex.join(
        [config['python'], '-B', str(root / 'current/manage.py'), '--install-dir', str(root)]) + ' "$@"\n'
    atomic_write(root / 'bin/token-lens', launcher, 0o700)
    if not shortcuts:
        return
    link = Path.home() / '.local/bin/token-lens'
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists() and not link.is_symlink():
        link.symlink_to(root / 'bin/token-lens')
    elif not (link.is_symlink() and link.readlink() == root / 'bin/token-lens'):
        print('保留已有命令：' + str(link))
    shortcut = Path.home() / 'Applications/Token Lens.command'
    content = '#!/bin/sh\n# Token Lens managed shortcut: ' + config['service_id'] + '\n' + shlex.join(
        [str(root / 'bin/token-lens'), 'open']) + '\n'
    if not shortcut.exists() or ('# Token Lens managed shortcut: ' + config['service_id']) in shortcut.read_text(encoding="utf-8"):
        atomic_write(shortcut, content, 0o700)
    else:
        print('保留已有快捷入口：' + str(shortcut))


def shortcut_path():
    if WINDOWS:
        return (Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) /
                'Microsoft/Windows/Start Menu/Programs/Token Lens.cmd')
    return Path.home() / 'Applications/Token Lens.command'


def open_browser(config):
    if WINDOWS:
        os.startfile('http://127.0.0.1:%s/' % config['port'])
        return
    subprocess.run(['/usr/bin/open', 'http://127.0.0.1:%s/' % config['port']], check=True)


def doctor(root, config):
    from app import hook_definition
    info = health(config)
    hook_path = Path(config['codex_home']) / 'hooks.json'
    expected = hook_definition(hook_args(root, config))['hooks']
    try:
        hooks = json.loads(hook_path.read_text(encoding="utf-8")).get('hooks', {})
        hooks_ok = all(expected[event][0] in hooks.get(event, []) for event in expected)
    except (OSError, ValueError, AttributeError):
        hooks_ok = False
    checks = dict(version=(current_dir(root) / 'VERSION').read_text(encoding="utf-8").strip(),
                  python=config['python'], python_available=Path(config['python']).is_file(),
                  install_dir=str(root), data_dir=str(root / 'data'),
                  codex_home=config['codex_home'], codex_home_exists=Path(config['codex_home']).is_dir(),
                  service_running=bool(info), url='http://127.0.0.1:%s/' % config['port'],
                  hooks_enabled=config.get('hooks', False), hooks_configured=hooks_ok,
                  hooks_file=str(hook_path), hooks_file_exists=hook_path.is_file(),
                  codex_cli_on_path=shutil.which('codex'),
                  hook_trust='未知：hooks_configured 只表示文件配置匹配，不代表 Codex 已加载或信任',
                  hook_next_step='在终端运行 codex，进入 Codex CLI 后输入 /hooks 检查与信任定义；桌面版入口可能不同。若 CLI 也没有该命令，请核对 codex --version 和 codex features list。',
                  autostart=config.get('autostart', False), logs=str(root / 'logs'))
    if info:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(checks['url'] + 'api/status', timeout=3) as response:
                status = json.load(response)
            checks.update(hooks_received=status['hooks_received'], latest_hook=status['latest_hook'],
                          scan=status['scan'], progress=status.get('progress'))
        except (OSError, ValueError):
            checks['scan'] = '状态暂不可读，请查看日志'
    print(json.dumps(checks, ensure_ascii=False, indent=2))


def uninstall(root, config):
    from .telemetry_config import configure
    configure(Path(config['codex_home']), root / 'data', config['port'], False)
    stop(root, config)
    # Keep user edits in hooks.json; remove only this installation's exact command.
    if (Path(config['codex_home']) / 'hooks.json').exists():
        set_hooks(root, config, remove=True)
    path = agent_path(config)
    if path.exists():
        if WINDOWS:
            if "' Token Lens: " + config['service_id'] in path.read_text(encoding='utf-16'):
                path.unlink()
        else:
            value = plistlib.loads(path.read_bytes())
            if value.get('Label') == config['launch_label']:
                path.unlink()
    link = Path.home() / '.local/bin/token-lens'
    if link.is_symlink() and link.readlink() == root / 'bin/token-lens':
        link.unlink()
    shortcut = shortcut_path()
    marker = ('rem ' if WINDOWS else '# ') + 'Token Lens managed shortcut: ' + config['service_id']
    if shortcut.is_file() and marker in shortcut.read_text(encoding='utf-8'):
        shortcut.unlink()
    (root / 'current').unlink(missing_ok=True)
    for name in ('versions', 'bin'):
        path = root / name
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    config.update(installed=False, autostart=False, hooks=False)
    write_json(root / 'config.json', config)
    print('已卸载程序、服务和本工具的 hook。统计数据与配置保留于：' + str(root))


def main():
    parser = argparse.ArgumentParser(description='Token Lens 本机服务管理')
    parser.add_argument('--install-dir', type=lambda s: Path(s).expanduser().resolve(), default=default_root())
    parser.add_argument('command', choices=('open', 'start', 'stop', 'restart', 'status', 'doctor',
                                           'uninstall', 'hook', 'run-service', 'sync', 'hooks', 'autostart', 'telemetry'))
    parser.add_argument('action', nargs='?', choices=('on', 'off'))
    args = parser.parse_args()
    try:
        root = args.install_dir
        config = read_config(root)
        if args.command == 'hook':
            from app import hook
            values = hook_args(root, config)
            values.log_dir = root / 'logs'
            hook(values)
            return
        if args.command in ('run-service', 'sync'):
            command = app_command(root, config, 'serve' if args.command == 'run-service' else 'sync')
            if args.command == 'run-service':
                command += ['--port', str(config['port']), '--service-id', config['service_id']]
            os.execv(command[0], command)
        if args.command in ('status', 'doctor'):
            doctor(root, config)
            return
        with management_lock(root):
            if args.command in ('stop', 'restart'):
                stop(root, config)
            if args.command in ('start', 'open', 'restart'):
                start(root, config)
                print('Token Lens → http://127.0.0.1:%s/' % config['port'])
                if args.command == 'open':
                    open_browser(config)
            elif args.command == 'uninstall':
                uninstall(root, config)
            elif args.command == 'telemetry':
                if not args.action: parser.error('telemetry 需要 on 或 off')
                from .telemetry_config import configure
                configure(Path(config['codex_home']), root / 'data', config['port'], args.action == 'on')
                print('本地遥测配置已更新；请在方便时重新启动 Codex。')
            elif args.command in ('hooks', 'autostart'):
                if not args.action:
                    parser.error(args.command + ' 需要 on 或 off')
                enabled = args.action == 'on'
                if args.command == 'hooks':
                    set_hooks(root, config, remove=not enabled)
                    config['hooks'] = enabled
                    write_json(root / 'config.json', config)
                else:
                    stop(root, config)
                    config['autostart'] = enabled
                    write_json(root / 'config.json', config)
                    if not enabled:
                        agent_path(config).unlink(missing_ok=True)
                    start(root, config)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if args.command == 'hook':
            print('{}')
            print('Token Lens hook: ' + str(exc), file=sys.stderr)
        else:
            parser.exit(1, 'Token Lens：' + str(exc) + '\n')
