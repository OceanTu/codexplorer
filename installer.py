#!/usr/bin/env python3
"""Bootstrap from the extracted package, then copy all runtime files into place."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / 'payload'))
from codex_usage.installation import main

if __name__ == '__main__':
    main(Path(__file__).resolve().parent)
