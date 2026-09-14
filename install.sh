#!/bin/sh
set -eu
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(uname -s)" != Darwin ]; then
    echo '此安装包目前支持 macOS。' >&2
    exit 1
fi
check_python() {
    "$1" -B -c 'import sys, sqlite3, fcntl; sys.exit(0 if sys.version_info >= (3, 9) else 1)' </dev/null >/dev/null 2>&1
}
if [ -n "${TOKEN_LENS_PYTHON:-}" ]; then
    if check_python "$TOKEN_LENS_PYTHON"; then
        exec "$TOKEN_LENS_PYTHON" -B "$package_dir/installer.py" "$@"
    fi
    echo 'TOKEN_LENS_PYTHON 不是可用的 Python 3.9+ 解释器。' >&2
    exit 1
fi
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)" /usr/bin/python3; do
    if [ -n "$candidate" ] && [ -x "$candidate" ] && check_python "$candidate"; then
        exec "$candidate" -B "$package_dir/installer.py" "$@"
    fi
done
echo '需要 Python 3.9+（包含 SQLite）。请先安装 Python，再重新双击 install.command。' >&2
echo '已有 Python 可使用：TOKEN_LENS_PYTHON=/完整路径/python3 sh install.sh' >&2
exit 1
