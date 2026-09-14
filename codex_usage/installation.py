"""Verified local-package installation with transactional version switching."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from codex_usage.lifecycle import (agent_path, atomic_write, default_root, health,
    install_launchers, management_lock, open_browser, read_config, set_hooks, start,
    stop, write_json)
from codex_usage.store import SCHEMA_VERSION


def verify_package(package):
    package = Path(package).resolve()
    manifest = json.loads((package / 'manifest.json').read_text())
    version = (package / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or version != manifest.get('version'):
        raise ValueError('安装包版本信息无效')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('安装包文件清单无效')
    actual = {str(p.relative_to(package)) for p in package.rglob('*')
              if p.is_file() and p.name != '.DS_Store'}
    if actual != set(files) | {'manifest.json'}:
        raise ValueError('安装包文件不完整或包含额外文件，请重新解压原始 ZIP')
    for name, expected in files.items():
        path = package / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(package):
            raise ValueError('安装包包含无效路径：' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('安装包校验失败：' + name)
    if (package / 'payload/VERSION').read_text().strip() != version:
        raise ValueError('程序版本与安装包不一致')
    return version, hashlib.sha256((package / 'manifest.json').read_bytes()).hexdigest()[:12]


def database_version(path):
    if not path.exists():
        return 0
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        return con.execute('PRAGMA user_version').fetchone()[0]
    finally:
        con.close()


def backup_database(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=20)
    dst = sqlite3.connect(str(destination), timeout=20)
    try:
        src.backup(dst, pages=256, sleep=.05)
        result = dst.execute('PRAGMA quick_check').fetchone()[0]
        if result != 'ok':
            raise ValueError('SQLite 备份校验失败：' + result)
    finally:
        dst.close()
        src.close()
    destination.chmod(0o600)


def point_current(root, target):
    link = root / ('.current-' + uuid.uuid4().hex)
    try:
        link.symlink_to(target)
        link.replace(root / 'current')
    finally:
        link.unlink(missing_ok=True)


def legacy_commands(source, codex_home):
    """Only retire commands provably tied to the explicitly selected source."""
    path = codex_home / 'hooks.json'
    if not path.exists():
        return []
    found = []
    for groups in json.loads(path.read_text()).get('hooks', {}).values():
        for group in groups:
            for hook in group.get('hooks', []):
                try:
                    bits = shlex.split(hook.get('command', ''))
                except ValueError:
                    continue
                if len(bits) == 7 and bits[1:] == [str(source / 'app.py'), '--data-dir',
                        str(source / '.data'), '--codex-home', str(codex_home), 'hook']:
                    found.append(hook['command'])
    return found


def install(package, args):
    if sys.platform != 'darwin':
        raise ValueError('此发行包支持 macOS；Windows 和 Linux 安装支持尚未提供')
    if sys.version_info < (3, 9):
        raise ValueError('需要 Python 3.9 或更新版本')
    version, digest = verify_package(package)
    root = args.install_dir
    if any(ord(c) < 32 for c in str(root)):
        raise ValueError('安装路径不能包含控制字符')
    if root.is_relative_to(package) or package.is_relative_to(root):
        raise ValueError('安装目录必须独立于 ZIP 的解压目录，才能安全删除解压包')
    if args.migrate_from and (root.is_relative_to(args.migrate_from) or args.migrate_from.is_relative_to(root)):
        raise ValueError('安装目录必须独立于旧开发目录')
    if root.exists() and any(root.iterdir()) and not (root / '.token-lens-install.json').exists():
        raise ValueError('目标目录已有其他内容，请选择空目录或已有 Token Lens 安装目录')
    if (root / '.token-lens-install.json').exists():
        if json.loads((root / '.token-lens-install.json').read_text()).get('app') != 'token-lens':
            raise ValueError('目标目录的安装标记无效，未修改该目录')
    print('校验通过：Token Lens ' + version, flush=True)
    print('安装目录：' + str(root), flush=True)
    with management_lock(root):
        # Preflight can fail (e.g. missing Codex); leave a recognizable directory
        # so the user can correct the parameter and retry in the same location.
        if not (root / '.token-lens-install.json').exists():
            write_json(root / '.token-lens-install.json', dict(app='token-lens', service_id='pending'))
        old = read_config(root) if (root / 'config.json').exists() else None
        if old and args.codex_home and str(args.codex_home) != old['codex_home']:
            raise ValueError('已有安装的数据源不同；请使用独立安装目录，避免混合两个 Codex 目录')
        if old and args.migrate_from:
            raise ValueError('迁移只适用于首次安装；当前统计数据已保留')
        codex_home = Path(old['codex_home']) if old else (args.codex_home or Path(
            os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve())
        if not codex_home.is_dir():
            raise ValueError('找不到 Codex 目录：%s；请指定 --codex-home' % codex_home)
        if not any((codex_home / folder).is_dir() for folder in ('sessions', 'archived_sessions')):
            print('提示：尚无历史日志，启动后会等待新的 Codex 记录。', flush=True)
        source_db = args.migrate_from / '.data/usage.sqlite3' if args.migrate_from else root / 'data/usage.sqlite3'
        if args.migrate_from and not source_db.is_file():
            raise ValueError('迁移源缺少 .data/usage.sqlite3')
        if database_version(source_db) > SCHEMA_VERSION:
            raise ValueError('统计数据库来自更新版本；为保护数据，已拒绝降级')
        config = dict(old or {})
        service_id = config.setdefault('service_id', uuid.uuid4().hex)
        config.update(python=str(Path(sys.executable).absolute()), codex_home=str(codex_home),
                      version=version, installed=True)
        config.setdefault('port', 8765)
        config.setdefault('autostart', False)
        config.setdefault('hooks', True)
        config.setdefault('launch_label', 'local.token-lens.' + service_id)
        if args.port is not None:
            config['port'] = args.port
        if args.autostart is not None:
            config['autostart'] = args.autostart
        if args.hooks is not None:
            config['hooks'] = args.hooks
        if args.interactive and not args.yes and args.autostart is None and not old:
            answer = input('登录 Mac 时自动启动？[y/N] ').strip().lower()
            config['autostart'] = answer in ('y', 'yes')
        for name in ('versions', 'data', 'logs', 'backups', 'bin'):
            (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        target = root / 'versions' / (version + '-' + digest)
        staging = root / 'versions' / ('.staging-' + uuid.uuid4().hex)
        shutil.copytree(package / 'payload', staging, ignore=shutil.ignore_patterns('.DS_Store'))
        # Exercise imports and static assets before touching the active version.
        check = subprocess.run([config['python'], '-B', str(staging / 'app.py'), '--help'],
                               capture_output=True, text=True)
        if check.returncode:
            shutil.rmtree(staging)
            raise ValueError('新版本运行检查失败：' + check.stderr.strip())
        if target.exists():
            # An immutable version must still match the freshly verified package.
            same = all((target / p.relative_to(staging)).is_file() and
                       (target / p.relative_to(staging)).read_bytes() == p.read_bytes()
                       for p in staging.rglob('*') if p.is_file())
            if not same:
                shutil.rmtree(staging)
                raise ValueError('已有同版本文件被修改，请保留目录并用新版安装包修复')
            shutil.rmtree(staging)
        else:
            staging.rename(target)
        prior_target = (root / 'current').readlink() if (root / 'current').is_symlink() else None
        was_running = bool(old and health(old))
        backup = root / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        hook_path = codex_home / 'hooks.json'
        hook_before = hook_path.read_bytes() if hook_path.exists() else None
        hook_after = None
        old_commands = legacy_commands(args.migrate_from, codex_home) if args.migrate_from else []
        launch_before = (root / 'bin/token-lens').read_bytes() if (root / 'bin/token-lens').exists() else None
        agent_before = agent_path(config).read_bytes() if agent_path(config).exists() else None
        had_db = (root / 'data/usage.sqlite3').exists()
        data_backup = None
        with (root / 'data/sync.lock').open('a') as lock:
            if old:
                stop(root, old)
            deadline = time.monotonic() + 20
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        if old and was_running:
                            start(root, old)
                        raise ValueError('采集器正在写入，请稍后再安装')
                    time.sleep(.1)
            try:
                if source_db.exists():
                    print('正在创建一致性数据库备份…', flush=True)
                    data_backup = backup / 'usage.sqlite3'
                    backup_database(source_db, data_backup)
                    if args.migrate_from:
                        backup_database(data_backup, root / 'data/usage.sqlite3')
                if old:
                    write_json(backup / 'config.json', old)
                point_current(root, target.relative_to(root))
                write_json(root / 'config.json', config)
                write_json(root / '.token-lens-install.json', dict(app='token-lens', service_id=service_id))
                install_launchers(root, config, shortcuts=False)
                set_hooks(root, config, remove=not config['hooks'], old_commands=old_commands)
                hook_after = hook_path.read_bytes() if hook_path.exists() else None
                if args.migrate_from and (args.migrate_from / '.data/pending').is_dir():
                    pending = root / 'data/pending'
                    pending.mkdir(exist_ok=True, mode=0o700)
                    for path in (args.migrate_from / '.data/pending').glob('*.json'):
                        shutil.copy2(path, pending / path.name)
                # Hold off collector writes through the startup check. The server
                # initializes its schema before scanning; scans retry after unlock.
                if not args.no_start:
                    print('启动本地服务并检查响应…', flush=True)
                    info = start(root, config)
                    if info['version'] != version:
                        raise ValueError('运行版本与安装版本不一致')
            except Exception:
                print('安装未完成，正在恢复此前版本…', file=sys.stderr, flush=True)
                if (root / 'config.json').exists():
                    stop(root, config)
                if prior_target:
                    point_current(root, prior_target)
                else:
                    (root / 'current').unlink(missing_ok=True)
                if old:
                    write_json(root / 'config.json', old)
                else:
                    (root / 'config.json').unlink(missing_ok=True)
                    (root / '.token-lens-install.json').unlink(missing_ok=True)
                if launch_before is not None:
                    atomic_write(root / 'bin/token-lens', launch_before, 0o700)
                else:
                    (root / 'bin/token-lens').unlink(missing_ok=True)
                if hook_after is not None and hook_path.exists() and hook_path.read_bytes() == hook_after:
                    if hook_before is None:
                        hook_path.unlink()
                    else:
                        atomic_write(hook_path, hook_before)
                if data_backup and had_db:
                    # All our writers are stopped/locked. SQLite backup also handles WAL safely.
                    backup_database(data_backup, root / 'data/usage.sqlite3')
                elif not had_db:
                    for name in ('usage.sqlite3', 'usage.sqlite3-wal', 'usage.sqlite3-shm'):
                        (root / 'data' / name).unlink(missing_ok=True)
                agent_path(config).unlink(missing_ok=True)
                if agent_before is not None:
                    atomic_write(agent_path(config), agent_before)
                fcntl.flock(lock, fcntl.LOCK_UN)
                if old and was_running:
                    start(root, old)
                if not old:
                    # Keep failed-install logs for diagnosis, mark the directory as ours for retry.
                    write_json(root / '.token-lens-install.json', dict(app='token-lens', service_id=service_id))
                raise
        install_launchers(root, config, shortcuts=not args.no_shortcuts)
        if not config['autostart']:
            agent_path(config).unlink(missing_ok=True)
        print('\n安装完成：Token Lens ' + version)
        print('程序、前端、配置及数据均位于安装目录。可以删除 ZIP 和解压文件夹，不影响使用。')
        print('管理命令：' + shlex.quote(str(root / 'bin/token-lens')) + ' open')
        if not args.no_shortcuts:
            print('双击入口：~/Applications/Token Lens.command')
        if not args.no_start:
            print('看板地址：http://127.0.0.1:%s/' % config['port'])
        if config['hooks']:
            print('Hook 配置已写入：' + str(hook_path))
            print('请在终端运行 codex，进入 Codex CLI 后输入 /hooks 检查并信任新增定义。桌面版入口可能不同。')
            print('配置写入不等于 Codex 已加载或信任；若找不到 /hooks，请运行 token-lens doctor，并核对 codex --version。')
        if args.migrate_from:
            print('已复制旧索引并迁移本工具的 hook；原始开发目录保留。请停止旧目录启动的服务。')
        if not args.no_open and not args.no_start:
            try:
                open_browser(config)
            except (OSError, subprocess.SubprocessError):
                print('无法自动打开浏览器，请手动打开上述地址。')


def main(package):
    parser = argparse.ArgumentParser(description='将 Token Lens 安装到固定用户目录；无需 sudo')
    path = lambda s: Path(s).expanduser().resolve()
    parser.add_argument('--install-dir', type=path, default=default_root())
    parser.add_argument('--codex-home', type=path)
    parser.add_argument('--port', type=int)
    parser.add_argument('--migrate-from', type=path, help='首次安装时复制旧开发目录的 .data 索引')
    auto = parser.add_mutually_exclusive_group()
    auto.add_argument('--autostart', dest='autostart', action='store_true')
    auto.add_argument('--no-autostart', dest='autostart', action='store_false')
    hooks = parser.add_mutually_exclusive_group()
    hooks.add_argument('--hooks', dest='hooks', action='store_true')
    hooks.add_argument('--no-hooks', dest='hooks', action='store_false')
    parser.set_defaults(autostart=None, hooks=None)
    parser.add_argument('--no-start', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--no-shortcuts', action='store_true')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--yes', action='store_true', help='使用默认选项，不询问登录自启')
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('--port 必须在 1–65535 之间')
    if args.no_start and args.autostart:
        parser.error('--no-start 不能和 --autostart 同时使用')
    try:
        os.umask(0o077)
        install(package, args)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        parser.exit(1, '安装失败：' + str(exc) + '\n')
