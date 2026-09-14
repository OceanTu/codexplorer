import json
import time
from datetime import datetime, timedelta
from .tools import attach as attach_tools, combine as combine_tools

TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens', 'total_tokens')


def aggregate(rows):
    result = {k: sum(r[k] for r in rows) for k in TOKEN_FIELDS}
    result.update(requests=len(rows), exact_requests=sum(r['kind'] == 'exact' for r in rows),
                  sessions=len({r['session_id'] for r in rows}),
                  turns=len({(r['session_id'], r['root_turn_id']) for r in rows}),
                  agent_tokens=sum(r['total_tokens'] for r in rows if r['is_agent']),
                  quality_warnings=sum(bool(r['quality']) for r in rows))
    result['uncached_input_tokens'] = max(0, result['input_tokens'] - result['cached_input_tokens'])
    result['cache_hit_rate'] = result['cached_input_tokens'] / result['input_tokens'] if result['input_tokens'] else 0
    return result


def group(rows, key):
    result = {}
    for row in rows:
        result.setdefault(key(row), []).append(row)
    return result


def ranked_models(rows):
    groups = group(rows, lambda r: r['model'])
    return sorted(groups, key=lambda name: sum(r['total_tokens'] for r in groups[name]), reverse=True)


class API:
    def __init__(self, con):
        self.con = con

    def rows(self, params, session=None, turn=None):
        clauses, args = [], []
        for name, sql in [('from', 'u.occurred_at >= ?'), ('to', 'u.occurred_at < ?')]:
            if params.get(name):
                value = float(params[name])
                if not (0 <= value <= 32503680000):
                    raise ValueError('Invalid date range')
                clauses.append(sql)
                args.append(value)
        for name, sql in [('model', 'u.model = ?'), ('project', "COALESCE(t.cwd,'') = ?")]:
            if params.get(name):
                clauses.append(sql)
                args.append(params[name])
        if session:
            clauses.append('u.session_id = ?')
            args.append(session)
        if turn:
            clauses.append('u.root_turn_id = ?')
            args.append(turn)
        is_agent = "(u.thread_id != u.session_id OR COALESCE(t.source,'') IN ('subagent','guardian_review'))"
        if params.get('scope') in ('main', 'agents'):
            clauses.append(is_agent if params['scope'] == 'agents' else 'NOT ' + is_agent)
        if params.get('q'):
            clauses.append("(COALESCE(s.title,'') || ' ' || u.session_id || ' ' || COALESCE(t.cwd,'') || ' ' || COALESCE(tr.preview,'')) LIKE ? ESCAPE '\\'")
            query = params['q'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            args.append('%' + query + '%')
        sql = """SELECT u.*, COALESCE(t.cwd,'') AS project,
          COALESCE(NULLIF(s.title,''),NULLIF(t.title,''),u.session_id) AS session_title,
          COALESCE(NULLIF(t.title,''),t.source,u.thread_id) AS thread_title,
          COALESCE(t.source,'unknown') AS source, COALESCE(s.archived,0) AS archived,
          COALESCE(tr.preview,'') AS preview, COALESCE(tr.status,'observed') AS status,
          tr.started_at, tr.ended_at, """ + is_agent + """ AS is_agent
          FROM usage u LEFT JOIN threads t ON t.id=u.thread_id
          LEFT JOIN threads s ON s.id=u.session_id
          LEFT JOIN turns tr ON tr.thread_id=u.thread_id AND tr.turn_id=u.turn_id"""
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        return [dict(r) for r in self.con.execute(sql + ' ORDER BY u.occurred_at,u.id', args)]

    def status(self):
        row = self.con.execute("SELECT value FROM settings WHERE key='last_scan'").fetchone()
        scan = json.loads(row[0]) if row else {}
        file_stats = dict(self.con.execute('SELECT COUNT(*) AS files, COALESCE(SUM(malformed),0) AS malformed, COALESCE(SUM(pending),0) AS pending FROM files').fetchone())
        hook = self.con.execute('SELECT occurred_at,event FROM hook_events ORDER BY occurred_at DESC LIMIT 1').fetchone()
        unmeasured = self.con.execute('SELECT COUNT(*) FROM threads t WHERE NOT EXISTS(SELECT 1 FROM usage u WHERE u.thread_id=t.id)').fetchone()[0]
        return dict(scan=scan, files=file_stats, latest_hook=dict(hook) if hook else None,
                    hooks_received=self.con.execute('SELECT COUNT(*) FROM hook_events').fetchone()[0],
                    unmeasured_threads=unmeasured)

    def overview(self, params):
        rows = self.rows(params)
        sessions = []
        for sid, items in group(rows, lambda r: r['session_id']).items():
            sessions.append(dict(id=sid, title=items[-1]['session_title'],
                                 project=items[-1]['project'], archived=items[-1]['archived'],
                                 first_at=items[0]['occurred_at'], last_at=items[-1]['occurred_at'],
                                 models=ranked_models(items),
                                 threads=len({r['thread_id'] for r in items}), **aggregate(items)))
        sessions.sort(key=lambda r: r['total_tokens'], reverse=True)
        daily = [dict(day=day, **aggregate(items)) for day, items in
                 group(rows, lambda r: datetime.fromtimestamp(r['occurred_at']).date().isoformat()).items()]
        if daily:
            by_day = {d['day']: d for d in daily}
            start = datetime.fromisoformat(daily[0]['day'])
            end = datetime.fromisoformat(daily[-1]['day'])
            if (end - start).days <= 730:
                daily = [by_day.get((start + timedelta(days=i)).date().isoformat(),
                                   dict(day=(start + timedelta(days=i)).date().isoformat(), **aggregate([])))
                         for i in range((end - start).days + 1)]
        models = [dict(model=name, **aggregate(items)) for name, items in group(rows, lambda r: r['model']).items()]
        models.sort(key=lambda r: r['total_tokens'], reverse=True)
        return dict(summary=aggregate(rows), sessions=sessions, daily=daily, models=models,
                    status=self.status(), generated_at=time.time())

    def options(self):
        return dict(models=[r[0] for r in self.con.execute('SELECT DISTINCT model FROM usage ORDER BY model')],
                    projects=[r[0] for r in self.con.execute("SELECT DISTINCT cwd FROM threads WHERE cwd!='' ORDER BY cwd")],
                    first_at=self.con.execute('SELECT MIN(occurred_at) FROM usage').fetchone()[0])

    def session(self, sid, params):
        rows = self.rows(params, session=sid)
        attach_tools(self.con, rows)
        ordinals = {r[0]: i + 1 for i, r in enumerate(self.con.execute(
            'SELECT root_turn_id FROM usage WHERE session_id=? GROUP BY root_turn_id ORDER BY MIN(occurred_at),root_turn_id', (sid,)))}
        turns = []
        for tid, items in group(rows, lambda r: r['root_turn_id']).items():
            main = next((r for r in items if not r['is_agent'] and r['preview']), items[0])
            meta = self.con.execute('SELECT * FROM turns WHERE thread_id=? AND turn_id=?', (sid, tid)).fetchone()
            turns.append(dict(id=tid, preview=(meta['preview'] if meta and meta['preview'] else main['preview']),
                              first_at=items[0]['occurred_at'], last_at=items[-1]['occurred_at'],
                              status=meta['status'] if meta else main['status'],
                              duration_seconds=items[-1]['occurred_at'] - items[0]['occurred_at'],
                              execution_turns=len({(r['thread_id'], r['turn_id']) for r in items}),
                              models=ranked_models(items),
                              threads=len({r['thread_id'] for r in items}),
                              tools=combine_tools(r['tools'] for r in items), **aggregate(items)))
        turns.sort(key=lambda r: r['first_at'])
        for turn in turns:
            turn['number'] = ordinals[turn['id']]
        return dict(id=sid, title=rows[-1]['session_title'] if rows else sid, summary=aggregate(rows), turns=turns)

    def turn(self, sid, tid, params):
        rows = self.rows(params, session=sid, turn=tid)
        executions = []
        for (thread, turn), items in group(rows, lambda r: (r['thread_id'], r['turn_id'])).items():
            executions.append(dict(id=turn, thread_id=thread, is_agent=items[0]['is_agent'],
                                   source=items[0]['source'], first_at=items[0]['occurred_at'],
                                   **aggregate(items)))
        executions.sort(key=lambda r: r['first_at'])
        if params.get('execution'):
            rows = [r for r in rows if r['turn_id'] == params['execution']]
        attach_tools(self.con, rows)
        for i, row in enumerate(rows):
            row['number'] = i + 1
        return dict(id=tid, session_id=sid, summary=aggregate(rows), requests=rows, executions=executions,
                    tools=combine_tools(r['tools'] for r in rows))
