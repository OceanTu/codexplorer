"""Small native-platform primitives shared by app, installer and manager."""
import errno
import os
from pathlib import Path
import shlex
import subprocess
import sys

WINDOWS = sys.platform == 'win32'

if WINDOWS:
    import msvcrt
else:
    import fcntl


def lock_exclusive(file):
    """Nonblocking exclusive lock, automatically released when file closes."""
    if WINDOWS:
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BlockingIOError(errno.EAGAIN, 'File is locked') from exc
            raise
    else:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(file):
    if WINDOWS:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(file, fcntl.LOCK_UN)


def background_options():
    if WINDOWS:
        return dict(creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True)
    return dict(start_new_session=True, close_fds=True)


def command_line(arguments):
    return subprocess.list2cmdline(arguments) if WINDOWS else shlex.join(arguments)


def split_command_line(command):
    if not WINDOWS:
        return shlex.split(command)
    import ctypes
    from ctypes import wintypes
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = ctypes.windll.kernel32.LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    values = parse(command, ctypes.byref(count))
    if not values:
        raise ValueError('Invalid Windows command line')
    try:
        return [values[i] for i in range(count.value)]
    finally:
        free(values)


def current_target(root):
    pointer = root / 'current'
    if pointer.is_symlink():
        return pointer.readlink()
    if WINDOWS and pointer.is_file():
        target = Path(pointer.read_text(encoding='utf-8').strip())
        if target.is_absolute() or '..' in target.parts or target.parts[:1] != ('versions',):
            raise ValueError('Invalid installed version pointer')
        return target
    return None


def current_dir(root):
    target = current_target(root)
    if target is None:
        raise ValueError('未找到安装版本，请重新安装')
    resolved = (root / target).resolve()
    if not resolved.is_relative_to((root / 'versions').resolve()):
        raise ValueError('Invalid installed version path')
    return resolved


def launcher_path(root):
    return root / 'bin' / ('token-lens.cmd' if WINDOWS else 'token-lens')


def windows_script(arguments, service_id):
    # Batch expands percent signs even inside quotes. Delayed expansion is off
    # so literal exclamation marks in a user's path survive as well.
    command = ' '.join('"' + str(arg).replace('%', '%%') + '"' for arg in arguments)
    return ('@echo off\n@chcp 65001 >nul\nsetlocal DisableDelayedExpansion\n'
            'rem Token Lens managed shortcut: ' + service_id + '\n'
            'set "PYTHONUTF8=1"\n' + command + ' %*\n')
