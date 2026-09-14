#!/usr/bin/env python3
"""Build a reproducible, allowlisted ZIP. Never archive the workspace wholesale."""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (
    'app.py', 'manage.py', 'VERSION',
    'codex_usage/__init__.py', 'codex_usage/api.py', 'codex_usage/importer.py',
    'codex_usage/messages.py', 'codex_usage/store.py', 'codex_usage/lifecycle.py',
    'codex_usage/tools.py',
    'codex_usage/pricing.py',
    'codex_usage/installation.py',
    'static/index.html', 'static/app.js', 'static/pricing.js', 'static/style.css', 'static/favicon.svg',
)


def build(output, source=ROOT):
    version = (source / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must be major.minor.patch')
    files = {name: (source / name).read_bytes() for name in ('install.command', 'install.sh', 'installer.py', 'VERSION')}
    files['README.md'] = (source / 'packaging/README.md').read_bytes()
    files.update({'payload/' + name: (source / name).read_bytes() for name in RUNTIME})
    manifest = dict(app='token-lens', version=version, platform='macos', python='>=3.9',
                    files={name: hashlib.sha256(value).hexdigest() for name, value in sorted(files.items())})
    files['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    output.mkdir(parents=True, exist_ok=True)
    name = 'token-lens-v%s-macos' % version
    archive = output / (name + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for path, data in sorted(files.items()):
            info = zipfile.ZipInfo(name + '/' + path, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            executable = path in ('install.command', 'install.sh')
            info.external_attr = ((0o100755 if executable else 0o100644) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, data)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(checksum + '  ' + archive.name + '\n')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    print(build(args.output))
