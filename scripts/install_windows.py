"""Build a verified Windows package from a source checkout, then install it."""
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from build_release import build


def main():
    with tempfile.TemporaryDirectory(prefix='token-lens-package-') as temp:
        base = Path(temp)
        archive = build(base, platform='windows')
        with zipfile.ZipFile(archive) as package:
            package.extractall(base / 'unpacked')
        installer = next((base / 'unpacked').iterdir()) / 'installer.py'
        return subprocess.call([sys.executable, '-X', 'utf8', '-B', str(installer), *sys.argv[1:]])


if __name__ == '__main__':
    sys.exit(main())
