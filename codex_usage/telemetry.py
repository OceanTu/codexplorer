"""Minimal OTLP JSON intake. Only uniquely corroborated sampling is accounted."""
import hashlib
import json
import math
import re
from datetime import datetime
from .supplemental import clean_prompt

COUNTERS={'input_token_count':'input_tokens','cached_token_count':'cached_input_tokens',
 'cache_write_token_count':'cache_write_input_tokens','output_token_count':'output_tokens',
 'reasoning_token_count':'reasoning_output_tokens'}
KEEP={'event.name','event.kind','event.timestamp','conversation.id','model','model_reasoning_effort',
      'prompt','response.id','response_id'} | set(COUNTERS)


def ingest(con, payload):
    if not isinstance(payload,dict) or not isinstance(payload.get('resourceLogs'),list):
        raise ValueError('Expected OTLP resourceLogs')
    accepted=0
    for resource in payload['resourceLogs']:
        for scope in resource.get('scopeLogs',[]):
            for record in scope.get('logRecords',[]):
                attrs={x['key']:next(iter(x.get('value',{}).values()),None)
                       for x in record.get('attributes',[]) if x.get('key') in KEEP}
                event=attrs.get('event.name')
                if attrs.get('event.kind')=='response.completed' and event in ('codex.sse_event','codex.websocket_event'):
                    event='response.completed'
                if event not in ('response.completed','codex.user_prompt'):
                    continue
                thread=attrs.get('conversation.id')
                if not isinstance(thread,str) or not re.fullmatch(r'[\w-]{1,160}',thread):continue
                try:
                    ts=int(record.get('timeUnixNano',0))/1e9
                    if ts==0:
                        stamp=datetime.fromisoformat(str(attrs.get('event.timestamp','')).replace('Z','+00:00'))
                        if stamp.tzinfo is None:continue
                        ts=stamp.timestamp()
                    if not math.isfinite(ts) or not 0<ts<32503680000:continue
                except (KeyError,ValueError,TypeError,OverflowError,OSError):continue
                attrs['event.name']=event
                if 'prompt' in attrs:
                    attrs['prompt'],attrs['prompt_truncated']=clean_prompt(str(attrs['prompt']))
                # Drop envelope metadata, trace IDs, account IDs and tool contents.
                encoded=json.dumps(attrs,sort_keys=True,ensure_ascii=False)
                identity=json.dumps([thread,event,ts,attrs],sort_keys=True,ensure_ascii=False)
                key=hashlib.sha256(identity.encode()).hexdigest()
                cur=con.execute('INSERT OR IGNORE INTO telemetry_events VALUES (?,?,?,?,?,?)',
                                (key,thread,ts,event,str(attrs.get('model','unknown'))[:160],encoded))
                accepted+=cur.rowcount
    return dict(accepted=accepted)


def token_counts(attrs):
    result={}
    for src,dst in COUNTERS.items():
        value=attrs.get(src)
        if isinstance(value,bool) or not re.fullmatch(r'\d{1,16}',str(value)):return None
        result[dst]=int(value)
    if result['cached_input_tokens']+result['cache_write_input_tokens']>result['input_tokens']:return None
    if result['reasoning_output_tokens']>result['output_tokens']:return None
    result['total_tokens']=result['input_tokens']+result['output_tokens']
    return result


def measured_rows(con, params, session=None, turn=None):
    # A persisted thread always uses rollout, even if its rollout arrives later.
    # OTel has no response ID on this Codex build; never heuristically add it
    # on top of another accounting source for the same thread.
    events=[dict(r) for r in con.execute('''SELECT e.* FROM telemetry_events e
      WHERE event='response.completed' AND NOT EXISTS(SELECT 1 FROM threads t WHERE t.id=e.thread_id)
      ORDER BY occurred_at,id''')]
    candidates={}
    for e in events:
        e['counts']=token_counts(json.loads(e['payload']))
        if not e['counts']:continue
        matches=con.execute('''SELECT * FROM observed_samples WHERE thread_id=? AND model=?
          AND tokens=? AND occurred_at BETWEEN ? AND ?''',
          (e['thread_id'],e['model'],e['counts']['total_tokens'],e['occurred_at']-.25,e['occurred_at']+2)).fetchall()
        for match in matches:
            candidates.setdefault(match['id'],[]).append((e,dict(match),len(matches)))
    rows=[]
    for choices in candidates.values():
        if len(choices)!=1 or choices[0][2]!=1:continue
        e,sample,_=choices[0];thread=e['thread_id'];tid=sample['turn_id']
        if turn and tid!=turn:continue
        link=con.execute('SELECT session_id FROM supplemental_links WHERE thread_id=?',(thread,)).fetchone()
        sid=link[0] if link else thread
        if session and sid!=session:continue
        observation=con.execute('SELECT * FROM observed_turns WHERE thread_id=? AND turn_id=?',(thread,tid)).fetchone()
        obs=dict(observation) if observation else {}
        title=con.execute('SELECT title FROM threads WHERE id=?',(sid,)).fetchone()
        preview=' '.join(obs.get('prompt','').split())[:240]
        row=dict(e['counts'],id='telemetry:'+e['id'],response_id=None,session_id=sid,
          thread_id=thread,turn_id=tid,root_turn_id=tid,occurred_at=e['occurred_at'],model=e['model'],
          effort=json.loads(e['payload']).get('model_reasoning_effort',''),reported_total=e['counts']['total_tokens'],
          kind='telemetry_verified',quality='本地遥测与正式采样核对；对话正文可能不完整',cumulative_key=None,
          path='',byte_offset=0,project=obs.get('project',''),session_title=title[0] if title else preview or '临时会话',
          thread_title='侧聊 / 临时会话',source='supplemental',archived=0,preview=preview,
          status='completed' if sample['finished'] else 'observed',started_at=obs.get('occurred_at',e['occurred_at']),
          ended_at=sample['occurred_at'] if sample['finished'] else None,is_agent=False,
          agent_path='',agent_nickname='',agent_role='',agent_label='侧聊 / 临时会话')
        if params.get('scope')=='agents':continue
        if params.get('from') and row['occurred_at']<float(params['from']):continue
        if params.get('to') and row['occurred_at']>=float(params['to']):continue
        if params.get('model') and row['model']!=params['model']:continue
        if params.get('project') and row['project']!=params['project']:continue
        if params.get('q') and params['q'].casefold() not in (row['session_title']+' '+sid+' '+row['project']+' '+preview).casefold():continue
        rows.append(row)
    return rows


def messages(con, request_id):
    row=next((r for r in measured_rows(con,{}) if r['id']==request_id),None)
    if row is None:raise KeyError('这条遥测尚未核验，或已由原始日志替代。请刷新。')
    obs=con.execute('SELECT prompt,truncated FROM observed_turns WHERE thread_id=? AND turn_id=?',(row['thread_id'],row['turn_id'])).fetchone()
    text=obs['prompt'] if obs else ''
    item=dict(id=request_id+':prompt',role='user',kind='message',name='',call_id='',timestamp=None,
              text=text,total_chars=len(text),next_offset=None)
    return dict(request_id=request_id,association='diagnostic_turn',is_compaction=False,
      note='用户输入属于本次 Turn；遥测未保存逐请求完整上下行正文。',
      warnings=['补充采集没有完整助手回复或工具返回；以下仅显示已恢复的本轮用户输入。']+(['用户输入过长，已截断。'] if obs and obs['truncated'] else []),
      context=[item] if text else [],input=[],output=[],tool_results=[])
