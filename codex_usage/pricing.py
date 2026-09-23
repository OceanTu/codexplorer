"""Local, editable rate-card estimates. Never mutate the measured usage ledger."""
import hashlib
import json
import math
import re

UNITS = ('credits', 'usd')
PARTS = ('input', 'cached', 'cache_write', 'output', 'reasoning')
VERIFIED_AT = '2026-09-23'
DEFAULT_CREDIT_REFERENCE = dict(name='7 天 ×20 套餐等价', credits=50400)
SOURCES = [
    ('Codex credits', 'https://learn.chatgpt.com/docs/pricing'),
    ('Codex Fast', 'https://learn.chatgpt.com/docs/agent-configuration/speed'),
    ('缓存规则', 'https://developers.openai.com/api/docs/guides/prompt-caching'),
] + [(name, 'https://developers.openai.com/api/docs/models/' + name)
     for name in ('gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna')]


def rate(base=None, output=1, api=False):
    return dict(input_per_million=base, cached_multiplier=.1,
                cache_write_multiplier=1.25 if api else 1,
                output_multiplier=output, reasoning_multiplier=output,
                fast_multiplier=2 if api else 2.5,
                long_context_threshold=272000 if api else 0,
                long_input_multiplier=2 if api else 1,
                long_output_multiplier=1.5 if api else 1)


def defaults():
    return [dict(model=name, mode='standard', credits=rate(credits, output), usd=rate(usd, output, True))
            for name, credits, usd, output in [('gpt-6-astra', 250, 10, 5),
                ('gpt-6-sol', 50, 2, 5), ('gpt-6-luna', 2.5, .1, 5),
                ('gpt-5.6-sol', 100, 4, 5), ('gpt-5.6-terra', 50, 2, 6),
                ('gpt-5.6-luna', 5, .2, 6)]]


def blank_model(name):
    # Unknown models have no base price and no presumed long-context surcharge.
    return dict(model=name, mode='standard', credits=dict(rate(), fast_multiplier=1),
                usd=dict(rate(), fast_multiplier=1))


def validate(models):
    if not isinstance(models, list) or not 1 <= len(models) <= 200:
        raise ValueError('请配置 1–200 个模型')
    result, seen = [], set()
    for model in models:
        if not isinstance(model, dict):
            raise ValueError('模型配置格式无效')
        name = model.get('model')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}', name):
            raise ValueError('模型 ID 只支持字母、数字和 . _ : / -，最长 160 字符')
        if name in seen:
            raise ValueError('模型 ID 重复：' + name)
        seen.add(name)
        if model.get('mode') not in ('standard', 'fast'):
            raise ValueError('估算模式必须为 standard 或 fast')
        normalized = dict(model=name, mode=model['mode'])
        for unit in UNITS:
            source = model.get(unit)
            if not isinstance(source, dict) or set(source) != set(rate()):
                raise ValueError(name + ' 的 ' + unit + ' 费率字段不完整')
            target = {}
            for key, value in source.items():
                if key == 'input_per_million' and value is None:
                    target[key] = None
                    continue
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1000000:
                    raise ValueError(name + ' 的费率必须为 0–1,000,000 的有限数字')
                if key == 'long_context_threshold' and int(value) != value:
                    raise ValueError('长上下文阈值必须为整数；0 表示关闭')
                target[key] = value
            normalized[unit] = target
        result.append(normalized)
    return result


def validate_credit_reference(value):
    if not isinstance(value, dict) or set(value) != {'name', 'credits'}:
        raise ValueError('套餐参考配置必须包含名称和 Credits 额度')
    name, credits = value['name'], value['credits']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('套餐名称须为 1–80 个字符')
    if (isinstance(credits, bool) or not isinstance(credits, (int, float))
            or not math.isfinite(credits) or not 0 < credits <= 1e12):
        raise ValueError('参考 Credits 额度须为大于 0、不超过 1,000,000,000,000 的有限数字')
    return dict(name=name.strip(), credits=credits)


class Pricing:
    def __init__(self, con):
        self.con = con
        self.config = self.settings()
        self.models = {m['model']: m for m in self.config['models']}

    def settings(self):
        row = self.con.execute("SELECT value FROM settings WHERE key='pricing'").fetchone()
        raw = row[0] if row else ''
        saved = json.loads(raw) if raw else {}
        models = {m['model']: m for m in defaults()}
        models.update({m['model']: m for m in saved.get('models', [])})
        observed = [r[0] for r in self.con.execute('SELECT DISTINCT model FROM usage ORDER BY model')]
        for name in observed:
            models.setdefault(name, blank_model(name))
        return dict(models=list(models.values()), revision=hashlib.sha256(raw.encode()).hexdigest(),
                    credit_reference=dict(saved.get('credit_reference', DEFAULT_CREDIT_REFERENCE)),
                    verified_at=VERIFIED_AT, sources=[dict(label=k, url=v) for k, v in SOURCES],
                    defaults=defaults(), observed_models=observed)

    def save(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('费率配置必须为 JSON 对象')
        models = validate(payload.get('models'))
        # Check under the same writer lock as the update; concurrent browser tabs
        # must not silently discard each other's manual prices.
        with self.con:
            self.con.execute('BEGIN IMMEDIATE')
            current = self.settings()
            if payload.get('revision') != current['revision']:
                raise ValueError('配置已在其他页面更新，请重新加载后再保存')
            # Older browser clients omit this field; preserve the saved reference.
            reference = validate_credit_reference(payload.get('credit_reference', current['credit_reference']))
            encoded = json.dumps(dict(version=2, models=models, credit_reference=reference),
                                 ensure_ascii=False, allow_nan=False)
            self.con.execute("INSERT INTO settings(key,value) VALUES ('pricing',?) "
                             'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (encoded,))
        self.config = self.settings()
        self.models = {m['model']: m for m in self.config['models']}
        return self.config

    def estimate(self, row):
        i, c, w, o, r = (row.get(k, 0) for k in ('input_tokens', 'cached_input_tokens',
                          'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens'))
        valid = all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in (i,c,w,o,r))
        valid = valid and c + w <= i and r <= o
        counts = dict(zip(PARTS, (i-c-w, c, w, o-r, r))) if valid else {}
        model = self.models.get(row.get('model'))
        result = {}
        for unit in UNITS:
            card = model[unit] if model else None
            base = card['input_per_million'] if card else None
            priced = valid and base is not None
            fast = bool(model and model['mode'] == 'fast')
            long = bool(priced and card['long_context_threshold'] and i > card['long_context_threshold'])
            effective, breakdown = {}, dict.fromkeys(PARTS, 0)
            if priced:
                for part in PARTS:
                    multiplier = 1 if part == 'input' else card[part + '_multiplier']
                    if fast:
                        multiplier *= card['fast_multiplier']
                    if long:
                        multiplier *= card['long_input_multiplier' if part in ('input','cached','cache_write') else 'long_output_multiplier']
                    effective[part] = base * multiplier
                    breakdown[part] = counts[part] * effective[part] / 1000000
            result[unit] = dict(total=math.fsum(breakdown.values()) if priced else None,
                breakdown=breakdown, priced_requests=int(priced), unpriced_requests=int(not priced),
                unpriced_tokens=0 if priced else row.get('total_tokens', i+o),
                reason='' if priced else 'invalid_subsets' if not valid else 'missing_rate',
                effective_rates=effective, mode=model['mode'] if model else 'standard', long_context=long)
        return result


def aggregate_costs(rows):
    result = {}
    for unit in UNITS:
        costs = [r['costs'][unit] for r in rows]
        priced = sum(c['priced_requests'] for c in costs)
        unpriced = sum(c['unpriced_requests'] for c in costs)
        result[unit] = dict(total=math.fsum(c['total'] for c in costs if c['total'] is not None)
                           if priced or not rows else None,
                           priced_requests=priced, unpriced_requests=unpriced,
                           unpriced_tokens=sum(c['unpriced_tokens'] for c in costs),
                           breakdown={p: math.fsum(c['breakdown'][p] for c in costs) for p in PARTS},
                           agent_total=math.fsum(r['costs'][unit]['total'] or 0 for r in rows if r['is_agent']))
    return result
