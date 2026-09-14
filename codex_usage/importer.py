"""Rollout adapter. Only usage, identifiers and short user labels are retained.

The rollout is not a stable API. Native records win over matching legacy
snapshots, and response IDs are globally unique across copies/archives/forks.
"""
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens', 'total_tokens')


def stamp(value):
    if isinstance(value, (float, int)):
        return float(value)
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0.0


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def counts(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for name in FIELDS:
        n = value.get(name, 0)
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 2**63 - 1:
            raise ValueError('invalid token counter: ' + name)
        result[name] = n
    if 'total_tokens' not in value:
        result['total_tokens'] = result['input_tokens'] + result['output_tokens']
    return result


def preview(text):
    if not isinstance(text, str):
        return ''
    if '## My request:' in text:
        text = text.rsplit('## My request:', 1)[-1]
    else:
        text = re.sub(r'<(environment_context|recommended_plugins|in-app-browser-context|user_instructions)\b[^>]*>.*?</\1>', '', text, flags=re.S)
    text = re.sub(r'\s+', ' ', text).strip()
    if text.startswith(('<', '# AGENTS.md', '## Skills')):
        return ''
    return text[:240]


class Importer:
    def __init__(self, con, codex_home):
        self.con = con
        self.home = Path(codex_home).expanduser().resolve()

    def ensure_thread(self, thread, session=None):
        self.con.execute('INSERT OR IGNORE INTO threads(id,session_id) VALUES (?,?)',
                         (thread, session or thread))

    def ensure_turn(self, state, turn=None, thread=None, session=None, root=None, ts=0):
        thread = thread or state['thread']
        turn = turn or state.get('turn') or 'unattributed'
        session = session or state.get('session') or thread
        root = root or state.get('root_turn') or turn
        self.ensure_thread(thread, session)
        self.con.execute('INSERT OR IGNORE INTO turns(thread_id,turn_id,session_id,root_turn_id,started_at) VALUES (?,?,?,?,?)',
                         (thread, turn, session, root, ts))
        self.con.execute('UPDATE turns SET root_turn_id=?, session_id=? WHERE thread_id=? AND turn_id=?',
                         (root, session, thread, turn))
        return thread, turn, session, root

    def record(self, state, p, ts, path, offset, native=False):
        u = counts(p.get('usage'))
        if u is None:
            return
        thread, turn, session, root = self.ensure_turn(
            state, p.get('turn_id'), p.get('thread_id'), p.get('session_id'), p.get('root_turn_id'), ts)
        cumulative = counts(p.get('thread_token_usage'))
        cumulative_key = digest(cumulative) if cumulative is not None else None
        response = p.get('response_id')
        key = ('response:' + response) if response else 'legacy:' + digest([thread, turn, ts, cumulative, u])
        if native and cumulative_key:
            # Also handles versions that emit token_count before usage_record.
            self.con.execute("DELETE FROM usage WHERE thread_id=? AND turn_id=? AND cumulative_key=? AND kind!='exact' AND ABS(occurred_at-?)<=30",
                             (thread, turn, cumulative_key, ts))
        if not native and cumulative_key and self.con.execute(
                "SELECT 1 FROM usage WHERE thread_id=? AND turn_id=? AND cumulative_key=? AND kind='exact' AND ABS(occurred_at-?)<=30", (thread, turn, cumulative_key, ts)).fetchone():
            return
        quality = p.get('quality', '')
        if u['cached_input_tokens'] + u['cache_write_input_tokens'] > u['input_tokens'] or u['reasoning_output_tokens'] > u['output_tokens']:
            quality += ' inconsistent_subsets'
        total = u['input_tokens'] + u['output_tokens']
        if u['total_tokens'] != total:
            quality += ' total_mismatch'
        values = (key, response, session, thread, turn, root, ts, state.get('model', 'unknown'),
                  state.get('effort', ''), u['input_tokens'], u['cached_input_tokens'],
                  u['cache_write_input_tokens'], u['output_tokens'], u['reasoning_output_tokens'],
                  total, u['total_tokens'], 'exact' if native else p.get('kind', 'legacy_delta'),
                  quality.strip(), cumulative_key, str(path), offset)
        # A response ID identifies one charge, even when its transcript moves.
        # Refresh provenance/labels, preserving the original token counters.
        self.con.execute('INSERT INTO usage VALUES (' + ','.join('?' for _ in values) + ''')
            ON CONFLICT(id) DO UPDATE SET
                path=excluded.path, byte_offset=excluded.byte_offset,
                session_id=excluded.session_id, thread_id=excluded.thread_id,
                turn_id=excluded.turn_id, root_turn_id=excluded.root_turn_id,
                occurred_at=excluded.occurred_at,
                model=CASE WHEN excluded.model='unknown' THEN usage.model ELSE excluded.model END,
                effort=CASE WHEN excluded.effort='' THEN usage.effort ELSE excluded.effort END
            ''', values)

    def relocate(self, response_id, path, offset):
        """Inherited/duplicate history repairs provenance without creating usage."""
        if response_id:
            self.con.execute('UPDATE usage SET path=?,byte_offset=? WHERE id=?',
                             (str(path), offset, 'response:' + response_id))

    def consume(self, d, s, path, offset):
        t, p = d.get('type'), d.get('payload')
        if not isinstance(p, dict):
            return
        ts = stamp(d.get('timestamp'))
        if t == 'session_meta':
            thread = p.get('id')
            if not thread:
                return
            s.update(thread=thread, session=p.get('session_id') or thread,
                     parent=p.get('parent_thread_id'), inherited_before=p.get('forked_from_ordinal_exclusive', 0))
            source = p.get('thread_source') or ('subagent' if isinstance(p.get('source'), dict) else p.get('source', 'unknown'))
            self.ensure_thread(thread, s['session'])
            self.con.execute('UPDATE threads SET session_id=?,parent_id=?,cwd=?,source=?,created_at=? WHERE id=?',
                             (s['session'], s['parent'], p.get('cwd', ''), source, stamp(p.get('timestamp')), thread))
            return
        if 'thread' not in s:
            return
        # Paginated forks can reference, or older rollouts can repeat, base history.
        if d.get('ordinal') is not None and d['ordinal'] < s.get('inherited_before', 0):
            if t == 'token_usage_record':
                self.relocate(p.get('response_id'), path, offset)
            return
        if t == 'turn_context':
            s['turn'] = p.get('turn_id') or s.get('turn') or 'unattributed'
            s['root_turn'] = p.get('root_turn_id') or s['turn']
            s['model'], s['effort'] = p.get('model', 'unknown'), p.get('effort', '')
            self.ensure_turn(s, ts=ts)
            self.con.execute('UPDATE threads SET model=? WHERE id=?', (s['model'], s['thread']))
        elif t == 'token_usage_record':
            response_id = p.get('response_id')
            seen = s.setdefault('native_seen', {})
            if response_id and response_id in seen:
                self.relocate(response_id, path, offset)
                return
            if response_id:
                seen[response_id] = True
            cumulative = counts(p.get('thread_token_usage'))
            matched_prior = cumulative and self.con.execute(
                "SELECT 1 FROM usage WHERE thread_id=? AND turn_id=? AND cumulative_key=? AND kind!='exact' AND ABS(occurred_at-?)<=30",
                (p.get('thread_id'), p.get('turn_id'), digest(cumulative), ts)).fetchone()
            self.record(s, p, ts, path, offset, native=True)
            # Native thread totals survive resumes; token_count may restart at 0.
            # Keep their baselines separate and subtract known requests from the
            # next legacy delta, instead of comparing the two cumulative totals.
            if p.get('thread_id') == s['thread'] and not matched_prior:
                pending = s.get('native_pending') or dict.fromkeys(FIELDS, 0)
                usage = counts(p.get('usage'))
                if usage:
                    s['native_pending'] = {k: pending[k] + usage[k] for k in FIELDS}
        elif t == 'event_msg':
            sub = p.get('type')
            if sub == 'task_started':
                s['turn'] = p.get('turn_id') or 'unattributed'
                s['root_turn'] = p.get('root_turn_id') or s['turn']
                self.ensure_turn(s, ts=stamp(p.get('started_at')) or ts)
                self.con.execute("UPDATE turns SET started_at=?, status='running' WHERE thread_id=? AND turn_id=?",
                                 (stamp(p.get('started_at')) or ts, s['thread'], s['turn']))
            elif sub in ('task_complete', 'turn_aborted'):
                turn = p.get('turn_id') or s.get('turn')
                if turn:
                    self.con.execute('UPDATE turns SET ended_at=?,status=? WHERE thread_id=? AND turn_id=?',
                                     (stamp(p.get('completed_at')) or ts, 'completed' if sub == 'task_complete' else 'interrupted', s['thread'], turn))
            elif sub == 'user_message':
                self.set_preview(s, p.get('message', ''))
            elif sub == 'token_count':
                info = p.get('info')
                if not isinstance(info, dict) or not info.get('total_token_usage'):
                    return
                total = counts(info['total_token_usage'])
                previous = s.get('total')
                s['total'] = total
                if previous == total:
                    return
                quality = ''
                if previous and all(total[k] >= previous[k] for k in FIELDS):
                    usage = {k: total[k] - previous[k] for k in FIELDS}
                    kind = 'legacy_delta'
                else:
                    usage = counts(info.get('last_token_usage'))
                    if usage is None:
                        s['missing_baselines'] = s.get('missing_baselines', 0) + 1
                        return  # An inherited cumulative baseline is not new consumption.
                    kind = 'legacy_last'
                    if previous:
                        quality = 'counter_reset'
                    elif usage != total:
                        quality = 'prior_usage_unattributed'
                pending = s.get('native_pending') or dict.fromkeys(FIELDS, 0)
                s['native_pending'] = {k: max(0, pending[k] - usage[k]) for k in FIELDS}
                usage = {k: max(0, usage[k] - pending[k]) for k in FIELDS}
                if usage['input_tokens'] or usage['output_tokens']:
                    self.record(s, {'usage': usage, 'thread_token_usage': total, 'kind': kind, 'quality': quality}, ts, path, offset)
        elif t == 'response_item' and p.get('type') == 'message' and p.get('role') == 'user':
            self.set_preview(s, ' '.join(x.get('text', '') for x in p.get('content', []) if isinstance(x, dict)))

    def set_preview(self, s, text):
        label = preview(text)
        if label:
            thread, turn, _, _ = self.ensure_turn(s)
            self.con.execute("UPDATE turns SET preview=? WHERE thread_id=? AND turn_id=? AND preview=''", (label, thread, turn))

    def scan_file(self, path):
        st = path.stat()
        old = self.con.execute('SELECT * FROM files WHERE path=?', (str(path),)).fetchone()
        if old and old['size'] == st.st_size and old['mtime_ns'] == st.st_mtime_ns and old['inode'] == st.st_ino:
            return 0
        state, offset, malformed = {}, 0, 0
        with path.open('rb') as f:
            fingerprint = hashlib.sha256()
            if old and old['inode'] == st.st_ino and st.st_size > old['size']:
                # Compare the entire previously parsed prefix. A tail-only hash
                # misses insertions/reordering in the middle followed by appends.
                remaining = old['offset']
                while remaining:
                    block = f.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    fingerprint.update(block)
                    remaining -= len(block)
                if not remaining and 'sha256:' + fingerprint.hexdigest() == old['checkpoint']:
                    state, offset, malformed = json.loads(old['state']), old['offset'], old['malformed']
            if not offset:
                fingerprint = hashlib.sha256()
            f.seek(offset)
            pending = 0
            read_bytes = 0
            while True:
                start = f.tell()
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b'\n'):
                    pending = 1
                    break  # Writer has not committed the last JSONL line yet.
                offset = f.tell()
                read_bytes += len(line)
                fingerprint.update(line)
                try:
                    d = json.loads(line)
                    if isinstance(d, dict):
                        self.consume(d, state, path, start)
                    else:
                        malformed += 1
                except (ValueError, TypeError, KeyError):
                    malformed += 1
            checkpoint = 'sha256:' + fingerprint.hexdigest()
        self.con.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)',
                         (str(path), st.st_ino, st.st_mtime_ns, st.st_size, offset, checkpoint, json.dumps(state), malformed, pending))
        return read_bytes

    def metadata(self):
        index = self.home / 'session_index.jsonl'
        if index.exists():
            for line in index.open():
                try:
                    p = json.loads(line)
                    self.con.execute('UPDATE threads SET title=? WHERE id=?', (p['thread_name'][:500], p['id']))
                except (ValueError, KeyError, TypeError):
                    continue
        # Optional title/archive enrichment only; tokens_used is not an accounting source.
        candidates = sorted(self.home.glob('state_*.sqlite'), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in candidates[:1]:
            try:
                other = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
                try:
                    columns = {r[1] for r in other.execute('PRAGMA table_info(threads)')}
                    label = "COALESCE(NULLIF(name,''),title)" if 'name' in columns else 'title'
                    for row in other.execute('SELECT id,' + label + ',archived FROM threads'):
                        self.con.execute('UPDATE threads SET title=?,archived=? WHERE id=?', (preview(row[1]) or row[1][:240], row[2], row[0]))
                finally:
                    other.close()
            except sqlite3.Error:
                pass

    def scan(self, data_dir, progress=None):
        started_at = time.time()
        started = time.monotonic()
        files = sorted(set(p.resolve() for folder in ('sessions', 'archived_sessions')
                           for p in (self.home / folder).rglob('*.jsonl')))
        read_bytes, errors = 0, []
        if progress:
            progress(dict(running=True, completed=0, total=len(files), started_at=started_at))
        for number, path in enumerate(files, 1):
            try:
                # A corrupt file does not roll back unrelated files or stop collection.
                with self.con:
                    read_bytes += self.scan_file(path)
            except (OSError, sqlite3.Error) as e:
                errors.append(path.name + ': ' + str(e))
            if progress:
                progress(dict(running=True, completed=number, total=len(files), started_at=started_at))
        self.metadata()
        pending = list((Path(data_dir) / 'pending').glob('*.json'))
        accepted = []
        for path in pending:
            try:
                p = json.loads(path.read_text())
                self.con.execute('INSERT OR IGNORE INTO hook_events VALUES (?,?,?,?,?)',
                                 (p['id'], p['occurred_at'], p['event'], p.get('session_id', ''), p.get('turn_id', '')))
                accepted.append(path)
            except (OSError, ValueError, KeyError):
                pass
        report = dict(started_at=started_at, scanned_at=time.time(), files=len(files), bytes_read=read_bytes,
                      duration_ms=round((time.monotonic() - started) * 1000), errors=errors)
        self.con.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('last_scan', json.dumps(report)))
        self.con.commit()
        for path in accepted:
            path.unlink(missing_ok=True)
        if progress:
            progress(dict(running=False, completed=len(files), total=len(files), started_at=started_at))
        return report
