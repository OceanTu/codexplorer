"""Cost estimates must partition tokens and reconcile across every drill-down."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from codex_usage.api import API
from codex_usage.importer import Importer
from codex_usage.store import connect
from test_usage import event, tokens


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.home = Path(self.temp.name) / 'codex'
        (self.home / 'sessions').mkdir(parents=True)
        self.con = connect(self.data)
        self.addCleanup(lambda: self.con.close())

    def fixture(self):
        records = [event('session_meta', dict(id='s', session_id='s', cwd='/project'))]
        for n, model in enumerate(('gpt-6-astra', 'gpt-5.6-sol', 'codex-auto-review'), 1):
            usage = tokens(100000, 80000, 10000, 8000)
            usage['cache_write_input_tokens'] = 10000
            records += [event('turn_context', dict(turn_id='t'+str(n), model=model)),
                        event('token_usage_record', dict(response_id='r'+str(n), thread_id='s',
                              session_id='s', turn_id='t'+str(n), root_turn_id='t'+str(n), usage=usage), n)]
        (self.home / 'sessions/a.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
        Importer(self.con, self.home).scan(self.data)

    def test_default_partition_and_reasoning_not_double_counted(self):
        self.fixture()
        request = API(self.con).turn('s', 't1', {})['requests'][0]
        c, u = request['costs']['credits'], request['costs']['usd']
        # Ordinary input 10K, read 80K, write 10K, visible output 2K, reasoning 8K.
        self.assertAlmostEqual(c['total'], 19.5)
        self.assertAlmostEqual(u['total'], .805)
        self.assertEqual(u['breakdown'], dict(input=.1, cached=.08, cache_write=.125,
                                              output=.1, reasoning=.4))
        self.assertEqual(request['total_tokens'], 110000)

    def test_aggregate_filtering_partial_and_unknown_are_not_free(self):
        self.fixture()
        api = API(self.con)
        overview = api.overview({})
        summary = overview['summary']
        self.assertAlmostEqual(summary['costs']['usd']['total'], 1.127)
        self.assertEqual(summary['costs']['usd']['unpriced_requests'], 1)
        self.assertEqual(summary['costs']['usd']['unpriced_tokens'], 110000)
        for unit in ('credits', 'usd'):
            total = summary['costs'][unit]['total']
            for collection in ('sessions', 'models', 'daily'):
                self.assertAlmostEqual(sum(x['costs'][unit]['total'] or 0 for x in overview[collection]), total)
            session = api.session('s', {})
            self.assertAlmostEqual(sum(t['costs'][unit]['total'] or 0 for t in session['turns']), total)
            turn = api.turn('s', 't1', {})
            self.assertEqual(turn['summary']['costs'][unit], turn['executions'][0]['costs'][unit])
        unknown = api.overview({'model':'codex-auto-review'})['summary']['costs']['usd']
        self.assertIsNone(unknown['total'])
        self.assertEqual(unknown['priced_requests'], 0)
        empty = api.overview({'model':'no-records'})['summary']['costs']['usd']
        self.assertEqual(empty['total'], 0)
        self.assertAlmostEqual(api.overview({'model':'gpt-6-astra'})['summary']['costs']['usd']['total'], .805)

    def test_save_persists_and_reprices_without_rescan_or_token_changes(self):
        from codex_usage.pricing import Pricing
        self.fixture()
        before = API(self.con).overview({})['summary']
        config = Pricing(self.con).settings()
        astra = next(m for m in config['models'] if m['model']=='gpt-6-astra')
        astra['usd']['cached_multiplier'] = .2
        astra['usd']['reasoning_multiplier'] = 7
        unknown = next(m for m in config['models'] if m['model']=='codex-auto-review')
        unknown['credits']['input_per_million'] = 0  # Explicitly free differs from unknown.
        Pricing(self.con).save(config)
        self.con.close(); self.con = connect(self.data)
        after = API(self.con).overview({})['summary']
        self.assertEqual(before['total_tokens'], after['total_tokens'])
        self.assertEqual(before['requests'], after['requests'])
        self.assertAlmostEqual(after['costs']['usd']['total'] - before['costs']['usd']['total'], .24)
        self.assertEqual(after['costs']['credits']['unpriced_requests'], 0)
        self.assertEqual(after['costs']['usd']['unpriced_requests'], 1)
        with self.assertRaises(ValueError):
            Pricing(self.con).save(config)  # Old browser tab cannot overwrite newer settings.

    def test_long_context_and_fast_apply_per_request_not_aggregate(self):
        from codex_usage.pricing import Pricing
        pricing = Pricing(self.con)
        config = pricing.settings()
        next(m for m in config['models'] if m['model']=='gpt-6-astra')['mode'] = 'fast'
        pricing.save(config)
        pricing = Pricing(self.con)
        at = dict(model='gpt-6-astra', **tokens(272000, 200000, 10000, 8000))
        above = dict(at, input_tokens=272001)
        base = pricing.estimate(at)
        long = pricing.estimate(above)
        self.assertAlmostEqual(base['usd']['total'], (72000*10+200000*1+10000*50)*2/1e6)
        self.assertAlmostEqual(long['usd']['total'], (72001*20+200000*2+10000*75)*2/1e6)
        self.assertAlmostEqual(long['credits']['total']-base['credits']['total'], 250*2.5/1e6)
        self.assertEqual(long['usd']['long_context'], True)
        self.assertEqual(long['credits']['long_context'], False)

    def test_bad_subsets_unpriced_and_exact_model_matching(self):
        from codex_usage.pricing import Pricing
        pricing = Pricing(self.con)
        for row in [dict(model='gpt-6-astra', **tokens(100, 101)),
                    dict(model='gpt-6-astra', **tokens(100, 60, 20, 21)),
                    dict(model='gpt-6-astra-custom', **tokens())]:
            self.assertIsNone(pricing.estimate(row)['usd']['total'])
        row = dict(model='gpt-6-astra-2026-09-01', **tokens())
        self.assertIsNone(pricing.estimate(row)['usd']['total'], 'Do not guess prices for unseen model IDs')

    def test_invalid_configuration_is_atomic(self):
        from codex_usage.pricing import Pricing
        pricing = Pricing(self.con)
        original = pricing.settings()
        for bad in (-1, float('nan'), float('inf'), True, 'oops'):
            changed = copy.deepcopy(original)
            changed['models'][0]['usd']['cached_multiplier'] = bad
            with self.assertRaises(ValueError): pricing.save(changed)
            self.assertEqual(pricing.settings()['revision'], original['revision'])
        for mutate in (lambda d: d['models'].append(copy.deepcopy(d['models'][0])),
                       lambda d: d['models'][0].update(mode='unrecognized'),
                       lambda d: d['models'][0]['usd'].pop('input_per_million')):
            changed = copy.deepcopy(original); mutate(changed)
            with self.assertRaises(ValueError): pricing.save(changed)
        self.assertEqual(pricing.settings()['revision'], original['revision'])

    def test_credit_reference_legacy_upgrade_and_roundtrip(self):
        from codex_usage.pricing import Pricing, defaults
        self.fixture()
        legacy = json.dumps(dict(version=1, models=defaults()))
        self.con.execute("INSERT INTO settings(key,value) VALUES ('pricing',?)", (legacy,))
        self.con.commit()
        before = API(self.con).overview({})['summary']
        config = Pricing(self.con).settings()
        self.assertEqual(config['credit_reference'], dict(name='7 天 ×20 套餐等价', credits=50400))
        self.assertEqual(self.con.execute("SELECT value FROM settings WHERE key='pricing'").fetchone()[0], legacy)
        config['credit_reference'] = dict(name='自定义套餐', credits=12600.5)
        saved = Pricing(self.con).save(config)
        self.con.close(); self.con = connect(self.data)
        self.assertEqual(Pricing(self.con).settings()['credit_reference'], config['credit_reference'])
        api = API(self.con)
        for result in (api.overview({}), api.session('s', {}), api.turn('s', 't1', {}), api.overview({'model':'empty'})):
            self.assertEqual(result['credit_reference'], config['credit_reference'])
        self.assertEqual(api.overview({})['summary'], before, 'Reference changes must not reprice usage')
        saved.pop('credit_reference')  # A pre-upgrade page must preserve a custom reference.
        self.assertEqual(Pricing(self.con).save(saved)['credit_reference'], config['credit_reference'])

    def test_invalid_credit_reference_does_not_overwrite_settings(self):
        from codex_usage.pricing import Pricing
        pricing = Pricing(self.con)
        original = pricing.settings()
        for bad in (None, [], {}, dict(name='套餐'), dict(name='', credits=1),
                    dict(name=' ' , credits=1), dict(name='x'*81, credits=1), dict(name=1, credits=1)):
            changed = copy.deepcopy(original); changed['credit_reference'] = bad
            with self.assertRaises(ValueError): pricing.save(changed)
        for bad in (0, -1, None, True, '50400', float('nan'), float('inf'), 1e12+1):
            changed = copy.deepcopy(original)
            changed['credit_reference'] = dict(name='套餐', credits=bad)
            with self.assertRaises(ValueError): pricing.save(changed)
        self.assertEqual(pricing.settings()['revision'], original['revision'])


if __name__ == '__main__':
    unittest.main()
