import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_id TEXT, title TEXT DEFAULT '',
 cwd TEXT DEFAULT '', source TEXT DEFAULT 'unknown', model TEXT DEFAULT 'unknown',
 created_at REAL DEFAULT 0, archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS turns (
 thread_id TEXT, turn_id TEXT, session_id TEXT, root_turn_id TEXT,
 started_at REAL DEFAULT 0, ended_at REAL, status TEXT DEFAULT 'observed',
 preview TEXT DEFAULT '', PRIMARY KEY(thread_id,turn_id)
);
CREATE TABLE IF NOT EXISTS usage (
 id TEXT PRIMARY KEY, response_id TEXT, session_id TEXT NOT NULL,
 thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, root_turn_id TEXT NOT NULL,
 occurred_at REAL NOT NULL, model TEXT NOT NULL, effort TEXT DEFAULT '',
 input_tokens INTEGER NOT NULL, cached_input_tokens INTEGER NOT NULL,
 cache_write_input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
 reasoning_output_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL,
 reported_total INTEGER, kind TEXT NOT NULL, quality TEXT DEFAULT '',
 cumulative_key TEXT, path TEXT, byte_offset INTEGER
);
CREATE INDEX IF NOT EXISTS usage_time ON usage(occurred_at);
CREATE INDEX IF NOT EXISTS usage_session ON usage(session_id, root_turn_id);
CREATE INDEX IF NOT EXISTS usage_cumulative ON usage(thread_id, cumulative_key);
CREATE INDEX IF NOT EXISTS usage_source_position ON usage(path, byte_offset);
CREATE TABLE IF NOT EXISTS files (
 path TEXT PRIMARY KEY, inode INTEGER, mtime_ns INTEGER, size INTEGER,
 offset INTEGER, checkpoint TEXT, state TEXT, malformed INTEGER DEFAULT 0,
 pending INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hook_events (
 id TEXT PRIMARY KEY, occurred_at REAL, event TEXT, session_id TEXT, turn_id TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(data_dir):
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(path / 'usage.sqlite3'), timeout=20)
    con.row_factory = sqlite3.Row
    version = con.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        con.close()
        raise ValueError('Index was created by a newer Token Lens; upgrade the application')
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=20000')
    if version < SCHEMA_VERSION:
        # Recheck after taking the writer lock: concurrent API connections must
        # not invalidate cursors again after another connection has migrated.
        con.execute('BEGIN IMMEDIATE')
        try:
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError('Index was created by a newer Token Lens; upgrade the application')
            if version < 1:
                for statement in SCHEMA.split(';'):
                    if statement.strip():
                        con.execute(statement)
            if version < 2:
                # Usage is accounting history; files is only a rebuildable cursor.
                # Replay once to repair positions saved by INSERT OR IGNORE.
                con.execute('DELETE FROM files')
            con.execute('PRAGMA user_version=%d' % SCHEMA_VERSION)
            con.commit()
        except Exception:
            con.rollback()
            con.close()
            raise
    return con
