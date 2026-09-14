"""Offline counterfactuals; no model calls and no Codex configuration changes.

Input is the frozen metadata snapshot .data/analysis-base.json produced during
the 2026-09-12 audit. Counterfactuals preserve the observed request sequence and
positive context growth, replace observed compactions with threshold-triggered
compactions, and do NOT model changes in task quality or extra rework.
"""
import collections
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / '.data/analysis-base.json').read_text())
start, end = data['start'], data['end']
all_rows = data['rows']
rows = [r for r in all_rows if start <= r['occurred_at'] < end]
main = [r for r in rows if r['source'] == 'user']
compacts = set(data['compact_ids'])
MODELS = {'gpt-6-astra': (250, 25, 1250), 'gpt-5.6-sol': (100, 10, 500)}


def ref_cost(r, api_long=False):
    # Official Codex standard credit rate, ONLY for publicly priced models.
    if r['model'] not in MODELS:
        return 0
    a, b, o = MODELS[r['model']]
    long = api_long and r['input_tokens'] > 272000
    i = r['input_tokens']
    c = min(i, r['cached_input_tokens'])
    return (((i-c)*a+c*b)*(2 if long else 1) + r['output_tokens']*o*(1.5 if long else 1))/1e6


def summary(rs):
    fields = ['input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens']
    d = {k: sum(r[k] for r in rs) for k in fields}
    d['requests'] = len(rs)
    d['uncached'] = d['input_tokens']-d['cached_input_tokens']
    d['cache_hit'] = d['cached_input_tokens']/d['input_tokens'] if d['input_tokens'] else 0
    d['known_model_reference_credits'] = sum(map(ref_cost, rs))
    return d


def simulate(threshold, retained=90000, cold=70000, summary_output=5500, compact_cache=0.7):
    result, extra = [], 0
    groups = collections.defaultdict(list)
    for r in all_rows:
        if r['source'] == 'user' and r['kind'] == 'exact':
            groups[r['thread_id']].append(r)
    for seq in groups.values():
        ctx, prev_input, just_compacted = None, None, False
        for r in seq:
            # Explicit response IDs identify actual summarization requests.
            if r['response_id'] in compacts:
                prev_input = r['input_tokens']
                just_compacted = True
                continue
            original = r['input_tokens']
            if ctx is None:
                ctx = original
            elif just_compacted:
                # Actual summary drops are replaced by our synthetic schedule.
                pass
            else:
                delta = original - prev_input
                if delta < -32000:
                    ctx = original  # A non-compaction reset/resume.
                else:
                    ctx = max(0, ctx + delta)
            prev_input, just_compacted = original, False
            did_compact = ctx > threshold
            in_period = start <= r['occurred_at'] < end
            if did_compact:
                compact = dict(r, input_tokens=ctx, cached_input_tokens=round(ctx*compact_cache),
                               output_tokens=summary_output, total_tokens=ctx+summary_output,
                               reasoning_output_tokens=0)
                if in_period:
                    result.append(compact)
                    extra += 1
                ctx = retained
            # Conservatively keep original cache misses, including misses caused
            # by the old compactions. Synthetic compactions add a cold-prefix bill.
            uncached = min(ctx, original-r['cached_input_tokens']+(cold if did_compact else 0))
            simulated = dict(r, input_tokens=ctx, cached_input_tokens=ctx-uncached,
                             total_tokens=ctx+r['output_tokens'])
            if in_period:
                result.append(simulated)
    return result, extra


baseline = summary(rows)
baseline_main = summary(main)
agent_rows = [r for r in rows if r['source'] != 'user']
result = dict(period={'start':start,'end':end}, all=baseline, main=baseline_main,
              agents=summary(agent_rows), scenarios=[], sensitivity=[])
for threshold in [128000,160000,192000,224000,256000,400000,600000,900000]:
    sim, n = simulate(threshold)
    s = summary(sim)
    delta = baseline_main['total_tokens']-s['total_tokens']
    result['scenarios'].append(dict(threshold=threshold, compactions=n,
        total_token_saved=delta, all_token_saving=delta/baseline['total_tokens'],
        main_token_saving=delta/baseline_main['total_tokens'],
        main_credit_reference_saving=1-s['known_model_reference_credits']/baseline_main['known_model_reference_credits'],
        main_api_reference_saving=1-sum(ref_cost(r,True) for r in sim)/sum(ref_cost(r,True) for r in main),
        result=s))
for threshold in [128000,160000,192000,224000,256000]:
    for retained in [80000,90000,110000]:
        for compact_cache in [0,0.7,0.98]:
            sim,n=simulate(threshold,retained=retained,cold=retained*.8,compact_cache=compact_cache)
            s=summary(sim)
            result['sensitivity'].append(dict(threshold=threshold,retained=retained,compact_cache=compact_cache,
                compactions=n, all_token_saving=(baseline_main['total_tokens']-s['total_tokens'])/baseline['total_tokens'],
                main_credit_reference_saving=1-s['known_model_reference_credits']/baseline_main['known_model_reference_credits']))
result['models']={m:summary([r for r in rows if r['model']==m]) for m in sorted({r['model'] for r in rows})}
result['effort']={m:summary([r for r in main if r['effort']==m]) for m in sorted({r['effort'] for r in main})}
result['sessions']={s:summary([r for r in rows if r['session_title']==s]) for s in sorted({r['session_title'] for r in rows})}
result['actual_main_compactions']=summary([r for r in main if r['response_id'] in compacts])
result['large_main']=summary([r for r in main if r['input_tokens']>272000])
result['main_context_quantiles']={str(p):statistics.quantiles([r['input_tokens'] for r in main],n=100)[p-1] for p in [25,50,75,90,95,99]}
out=ROOT/'.data/context-scenarios-2026-09-12.json'
out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ('sensitivity','sessions')},ensure_ascii=False,indent=2))
print('SENSITIVITY_RANGES',json.dumps({str(t):{k:[min(r[k] for r in result['sensitivity'] if r['threshold']==t),max(r[k] for r in result['sensitivity'] if r['threshold']==t)] for k in ['all_token_saving','main_credit_reference_saving']} for t in [128000,160000,192000,224000,256000]}))
