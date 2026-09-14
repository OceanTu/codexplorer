import sqlite3,json,collections,statistics
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
c=sqlite3.connect((ROOT/'.data/usage.sqlite3').as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
start=datetime.fromisoformat('2026-09-05T00:00:00+08:00').timestamp();end=datetime.fromisoformat('2026-09-12T00:00:00+08:00').timestamp()
rows=[dict(r) for r in c.execute('select u.*,t.source,t.title,root.title session_title from usage u left join threads t on t.id=u.thread_id left join threads root on root.id=u.session_id where occurred_at<? order by occurred_at,id',(end,))];byresp={r['response_id']:r for r in rows if r['response_id']};compact=set()
for path in set(r['path'] for r in rows if r['occurred_at']>=start):
 for line in Path(path).open():
  if '"compacted"' not in line[:200]:continue
  p=json.loads(line).get('payload',{});rid=p.get('compaction_response_id')
  if rid in byresp:compact.add(rid)
week=[r for r in rows if r['occurred_at']>=start]; main=[r for r in week if r['source']=='user']; mainall=[r for r in rows if r['source']=='user' and r['kind']=='exact']
print('SESSIONS')
for sid in sorted(set(r['session_id'] for r in week),key=lambda sid:-sum(r['total_tokens'] for r in week if r['session_id']==sid))[:6]:
 rs=[r for r in week if r['session_id']==sid];print(rs[0]['session_title'],sum(r['total_tokens'] for r in rs))
print('TOP ROOTS')
g=collections.defaultdict(list)
for r in week:g[(r['session_id'],r['root_turn_id'])].append(r)
for (sid,tid),rs in sorted(g.items(),key=lambda kv:-sum(r['total_tokens'] for r in kv[1]))[:8]:
 meta=c.execute('select preview from turns where thread_id=? and turn_id=?',(sid,tid)).fetchone();print(sid,tid,meta[0][:70] if meta else '',len(rs),len(set(r['turn_id'] for r in rs)),sum(r['total_tokens'] for r in rs))
print('COMP MAIN')
for tid,rs in __import__('itertools').groupby(sorted(mainall,key=lambda r:(r['thread_id'],r['occurred_at'])),lambda r:r['thread_id']):
 rs=list(rs)
 for i,r in enumerate(rs[:-1]):
  if r['response_id'] in compact and r['occurred_at']>=start:
   n=rs[i+1];print(r['input_tokens'],r['input_tokens']-r['cached_input_tokens'],r['output_tokens'],'->',n['input_tokens'],n['input_tokens']-n['cached_input_tokens'])
print('STEP_STATS')
g=collections.defaultdict(list)
for r in mainall:g[r['thread_id']].append(r)
deltas=[]; neg=[]
for rs in g.values():
 for p,r in zip(rs,rs[1:]):
  if r['occurred_at']<start:continue
  d=r['input_tokens']-p['input_tokens']
  if d>=0:deltas.append(d)
  elif p['response_id'] not in compact:neg.append(d)
print('growth',len(deltas),[statistics.quantiles(deltas,n=100)[i] for i in [24,49,74,89,94,98]],'sum',sum(deltas),'drops',len(neg),'dropgt32k',sum(d < -32000 for d in neg))
(ROOT/'.data/analysis-base.json').write_text(json.dumps({'start':start,'end':end,'rows':rows,'compact_ids':list(compact)}))
