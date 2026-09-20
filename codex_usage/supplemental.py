"""Recover observations missing from rollout accounting; unknown is never zero."""
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from .messages import user_text

SCHEMA = '''
CREATE TABLE IF NOT EXISTS observed_turns (
 thread_id TEXT, turn_id TEXT, occurred_at REAL, model TEXT DEFAULT 'unknown',
 project TEXT DEFAULT '', prompt TEXT DEFAULT '', truncated INTEGER DEFAULT 0,
 PRIMARY KEY(thread_id,turn_id)
);
CREATE TABLE IF NOT EXISTS observed_samples (
 id TEXT PRIMARY KEY, thread_id TEXT, turn_id TEXT, occurred_at REAL,
 model TEXT, tokens INTEGER, finished INTEGER
);
CREATE INDEX IF NOT EXISTS observed_samples_thread ON observed_samples(thread_id,occurred_at);
CREATE TABLE IF NOT EXISTS observed_tools (
 id TEXT PRIMARY KEY, thread_id TEXT, turn_id TEXT, name TEXT
);
CREATE TABLE IF NOT EXISTS supplemental_links (
 thread_id TEXT PRIMARY KEY, session_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnostic_cursors (
 path TEXT PRIMARY KEY, inode INTEGER, last_id INTEGER, anchor TEXT
);
CREATE TABLE IF NOT EXISTS telemetry_events (
 id TEXT PRIMARY KEY, thread_id TEXT, occurred_at REAL, event TEXT, model TEXT,
 payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS telemetry_events_thread ON telemetry_events(thread_id,occurred_at);
'''
QUOTED = r'"(?:[^"\\]|\\.)*"'
IDENT = r'[a-zA-Z0-9_-]{1,160}'
TARGETS = ('codex_core::session::handlers', 'codex_core::session::turn', 'codex_core::stream_events_utils')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def decode_string(value):
    # Rust Debug uses \u{...}; JSON uses \uXXXX. Do not eval log text.
    value = re.sub(r'\\(?:u\{([0-9a-fA-F]{1,6})\}|.)',
                   lambda m: json.dumps(chr(int(m[1],16)))[1:-1] if m[1] else (r'\u0000' if m[0]==r'\0' else m[0]), value)
    return json.loads(value)


def clean_prompt(text):
    text = user_text(text)
    data = text.encode('utf-8')
    return data[:32768].decode('utf-8','ignore'), int(len(data)>32768)


def observe(con, thread, turn, ts, model='unknown', project='', prompt=''):
    text, truncated = clean_prompt(prompt)
    con.execute('''INSERT INTO observed_turns VALUES (?,?,?,?,?,?,?)
      ON CONFLICT(thread_id,turn_id) DO UPDATE SET
       occurred_at=MIN(observed_turns.occurred_at,excluded.occurred_at),
       model=CASE WHEN excluded.model='unknown' THEN observed_turns.model ELSE excluded.model END,
       project=CASE WHEN excluded.project='' THEN observed_turns.project ELSE excluded.project END,
       prompt=CASE WHEN excluded.prompt='' THEN observed_turns.prompt ELSE excluded.prompt END,
       truncated=CASE WHEN excluded.prompt='' THEN observed_turns.truncated ELSE excluded.truncated END''',
      (thread,turn,ts,model,project,text,truncated))


def consume_log(con, row):
    thread = row['thread_id']
    if not isinstance(thread,str) or not re.fullmatch(IDENT,thread):
        return
    body = row['feedback_log_body'] or ''
    ts = row['ts'] + (row['ts_nanos'] or 0)/1e9
    target = row['target']
    turn = re.search(r'\bturn(?:\.id|_id)="?('+IDENT+')',body)
    model = re.search(r'\bmodel[=:] (?:Some\()?('+QUOTED+')',body) or re.search(r'\bmodel=("?)([\w.:-]+)',body)
    model_name = 'unknown'
    if model:
        model_name = decode_string(model[1]) if model[1].startswith('"') and len(model[1])>1 else model[2]
    if target==TARGETS[0] and 'Submission sub=Submission' in body:
        start = re.search(r'Submission sub=Submission \{ id: ('+QUOTED+r'), op: TurnInput \{',body)
        if not start:
            return
        turn_id = decode_string(start[1])
        content = body[start.end():]
        boundary = next((m.start() for m in re.finditer(QUOTED+r'|thread_settings:',content)
                         if m[0]=='thread_settings:'),len(content))
        content = content[:boundary]
        texts = [decode_string(m[1]) for m in re.finditer(r'Text \{ text: ('+QUOTED+')',content)]
        cwd = re.search(r'(?:legacy_fallback_cwd|cwd): (?:Some\()?AbsolutePathBuf\(('+QUOTED+r')\)',body)
        observe(con,thread,turn_id,ts,model_name,decode_string(cwd[1]) if cwd else '', '\n'.join(texts))
    elif target==TARGETS[1] and 'post sampling token usage' in body and turn:
        count = re.search(r'\btotal_usage_tokens=(\d+)\b',body)
        if not count or int(count[1])>2**63-1:
            return
        observe(con,thread,turn[1],ts,model_name)
        con.execute('INSERT OR IGNORE INTO observed_samples VALUES (?,?,?,?,?,?,?)',
          (digest(thread+'|'+str(ts)+'|'+body),thread,turn[1],ts,model_name,int(count[1]),int('needs_follow_up=false' in body)))
    elif target==TARGETS[2] and turn:
        tool = re.search(r'\bToolCall: ([\w.:/-]{1,180}) ',body)
        if tool:
            con.execute('INSERT OR IGNORE INTO observed_tools VALUES (?,?,?,?)',
                (digest(thread+'|'+str(ts)+'|'+body),thread,turn[1],tool[1]))


def log_fingerprint(row):
    # A recycled row ID and routine body can coincide after in-place rotation.
    return digest(json.dumps(list(row),ensure_ascii=False))


def scan_diagnostics(con, home):
    report = dict(rows=0, errors=[], scanned_at=time.time())
    for path in sorted(Path(home).glob('logs_*.sqlite')):
        src = None
        try:
            src = sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)
            src.row_factory = sqlite3.Row
            src.execute('BEGIN')
            last = con.execute('SELECT * FROM diagnostic_cursors WHERE path=?',(str(path),)).fetchone()
            inode = path.stat().st_ino
            after = last['last_id'] if last and last['inode']==inode else 0
            if after:
                anchor = src.execute('SELECT id,ts,ts_nanos,target,thread_id,feedback_log_body FROM logs WHERE id=?',(after,)).fetchone()
                if not anchor or log_fingerprint(anchor)!=last['anchor']:
                    after=0
            # Bounded batches; continuing scans catch up without blocking the UI.
            rows = src.execute('SELECT id,ts,ts_nanos,target,thread_id,feedback_log_body FROM logs WHERE id>? ORDER BY id LIMIT 5000',(after,)).fetchall()
            for row in rows:
                if row['target'] in TARGETS:
                    try:
                        consume_log(con,row)
                    except (ValueError,TypeError,OverflowError,UnicodeError):
                        report['errors'].append('诊断记录 %s 无法解析' % row['id'])
                report['rows']+=1
            if rows:
                end=rows[-1]
                con.execute('INSERT OR REPLACE INTO diagnostic_cursors VALUES (?,?,?,?)',
                            (str(path),inode,end['id'],log_fingerprint(end)))
        except (OSError,sqlite3.Error) as exc:
            report['errors'].append(path.name+': '+str(exc))
        finally:
            if src: src.close()
    report['errors']=report['errors'][:20]
    con.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('diagnostics_scan',json.dumps(report)))
    return report


def link_session(con, thread, session):
    if not con.execute('SELECT 1 FROM observed_turns WHERE thread_id=? UNION SELECT 1 FROM telemetry_events WHERE thread_id=?',(thread,thread)).fetchone():
        raise ValueError('补充记录不存在')
    if session and not con.execute('SELECT 1 FROM threads WHERE id=? AND session_id=id',(session,)).fetchone():
        raise ValueError('目标会话不存在')
    if session:
        con.execute('INSERT OR REPLACE INTO supplemental_links VALUES (?,?)',(thread,session))
    else:
        con.execute('DELETE FROM supplemental_links WHERE thread_id=?',(thread,))
    con.commit()


def supplemental(con, params, session=None):
    result=[]
    sql='''SELECT o.*,l.session_id FROM observed_turns o LEFT JOIN supplemental_links l ON l.thread_id=o.thread_id
      WHERE NOT EXISTS(SELECT 1 FROM usage u WHERE u.thread_id=o.thread_id AND u.turn_id=o.turn_id)
      ORDER BY o.occurred_at DESC'''
    for row in con.execute(sql):
        r=dict(row)
        if session and r['session_id']!=session: continue
        if params.get('scope')=='agents': continue
        if params.get('from') and r['occurred_at']<float(params['from']):continue
        if params.get('to') and r['occurred_at']>=float(params['to']):continue
        if params.get('model') and r['model']!=params['model']:continue
        if params.get('project') and r['project']!=params['project']:continue
        if params.get('q') and params['q'].casefold() not in (r['prompt']+' '+r['thread_id']+' '+r['project']).casefold():continue
        r.update(preview=' '.join(r['prompt'].split())[:240],total_tokens=None,costs={'credits':None,'usd':None})
        r['context_samples']=[dict(x) for x in con.execute('SELECT occurred_at,tokens FROM observed_samples WHERE thread_id=? AND turn_id=? ORDER BY occurred_at',(r['thread_id'],r['turn_id']))]
        r['tools']=[dict(x) for x in con.execute('SELECT name,COUNT(*) AS calls FROM observed_tools WHERE thread_id=? AND turn_id=? GROUP BY name',(r['thread_id'],r['turn_id']))]
        result.append(r)
    # User-prompt events remain discoverable even before diagnostic logs arrive.
    for e in con.execute("SELECT * FROM telemetry_events WHERE event='codex.user_prompt' ORDER BY occurred_at DESC"):
        link=con.execute('SELECT session_id FROM supplemental_links WHERE thread_id=?',(e['thread_id'],)).fetchone()
        sid=link[0] if link else None
        if session and session!=sid:continue
        if con.execute('SELECT 1 FROM threads WHERE id=?',(e['thread_id'],)).fetchone():continue
        a=json.loads(e['payload']);text=a.get('prompt','')
        if con.execute('SELECT 1 FROM observed_turns WHERE thread_id=? AND prompt=? AND occurred_at BETWEEN ? AND ?',
                       (e['thread_id'],text,e['occurred_at']-10,e['occurred_at']+10)).fetchone():continue
        if params.get('scope')=='agents' or params.get('project'):continue
        if params.get('from') and e['occurred_at']<float(params['from']):continue
        if params.get('to') and e['occurred_at']>=float(params['to']):continue
        if params.get('model') and e['model']!=params['model']:continue
        if params.get('q') and params['q'].casefold() not in text.casefold():continue
        result.append(dict(thread_id=e['thread_id'],turn_id='pending:'+e['id'],occurred_at=e['occurred_at'],
          model=e['model'],project='',prompt=text,truncated=a.get('prompt_truncated',0),session_id=sid,
          preview=' '.join(text.split())[:240],total_tokens=None,costs={'credits':None,'usd':None},context_samples=[],tools=[]))
    from .telemetry import measured_rows
    from collections import Counter
    measured=Counter((r['thread_id'],r['turn_id']) for r in measured_rows(con,{}))
    pending=[]
    for r in result:
        r['measured_requests']=measured[(r['thread_id'],r['turn_id'])]
        r['missing_samples']=max(0,len(r['context_samples'])-r['measured_requests'])
        if not r['measured_requests'] or r['missing_samples']:
            pending.append(r)
    return sorted(pending,key=lambda r:r['occurred_at'],reverse=True)


def collection_status(con):
    r=con.execute("SELECT value FROM settings WHERE key='diagnostics_scan'").fetchone()
    config=con.execute("SELECT value FROM settings WHERE key='telemetry_config'").fetchone()
    from .telemetry import measured_rows
    measured=len(measured_rows(con,{}))
    pending=con.execute("SELECT COUNT(*) FROM telemetry_events e WHERE event='response.completed' AND NOT EXISTS(SELECT 1 FROM threads t WHERE t.id=e.thread_id)").fetchone()[0]-measured
    return dict(measured_requests=measured,pending_events=pending,config=json.loads(config[0]) if config else {'enabled': False}, diagnostics=json.loads(r[0]) if r else {},
      observed_turns=con.execute('SELECT COUNT(*) FROM observed_turns').fetchone()[0],
      telemetry_events=con.execute('SELECT COUNT(*) FROM telemetry_events').fetchone()[0],
      latest_telemetry=con.execute('SELECT MAX(occurred_at) FROM telemetry_events').fetchone()[0])
