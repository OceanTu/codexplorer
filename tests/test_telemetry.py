import json
import unittest
from pathlib import Path
import test_supplemental as recovery
from test_supplemental import THREAD,TURN,BASE,prompt,sample
from codex_usage.api import API


def payload(at=BASE+5, **changes):
    attrs={'event.name':'codex.sse_event','event.kind':'response.completed','conversation.id':THREAD,
        'model':'gpt-6-astra','input_token_count':'80','cached_token_count':60,
        'cache_write_token_count':0,'output_token_count':'20','reasoning_token_count':5}
    attrs.update(changes)
    attrs={k:v for k,v in attrs.items() if v is not None}
    return {'resourceLogs':[{'scopeLogs':[{'logRecords':[{'timeUnixNano':str(int(at*1e9)),
        'attributes':[{'key':k,'value':{'intValue':str(v)} if isinstance(v,int) else {'stringValue':v}} for k,v in attrs.items()]}]}]}]}


class TelemetryTests(unittest.TestCase):
    setUp=recovery.RecoveryTests.setUp
    tearDown=recovery.RecoveryTests.tearDown
    add=recovery.RecoveryTests.add
    scan=recovery.RecoveryTests.scan
    recovered=recovery.RecoveryTests.recovered
    def ingest(self,p):
        from codex_usage.telemetry import ingest
        ingest(self.con,p);self.con.commit()
    def test_match_real_sampling_and_deduplicate_delivery(self):
        self.add(prompt());self.add(sample(),BASE+5,'codex_core::session::turn');self.scan()
        self.ingest(payload());self.ingest(payload())
        summary=API(self.con).overview({})['summary']
        self.assertEqual(summary['total_tokens'],100)
        self.assertEqual(summary['cached_input_tokens'],60)
        self.assertEqual(summary['requests'],1)
        self.assertAlmostEqual(summary['costs']['credits']['total'],.0315)
        rows=API(self.con).turn(THREAD,TURN,{})['requests'];self.assertEqual(len(rows),1)
        from codex_usage.messages import RequestMessages
        m=RequestMessages(self.con,self.home).read(rows[0]['id'])
        self.assertEqual(m['context'][0]['text'],'OCR status?')
        self.assertEqual(m['output'],[]);self.assertTrue(m['warnings'])

    def test_prewarm_and_ambiguous_events_are_not_billed(self):
        self.add(prompt());self.add(sample(),BASE+5,'codex_core::session::turn');self.scan()
        self.ingest(payload(BASE+1,input_token_count='55',output_token_count='0',reasoning_token_count=0,cached_token_count=0))
        self.assertEqual(API(self.con).overview({})['summary']['requests'],0)
        self.ingest(payload());self.ingest(payload(BASE+5.1))
        self.assertEqual(API(self.con).overview({})['summary']['requests'],0)

    def test_missing_cache_counter_and_invalid_subsets_stay_unknown(self):
        self.add(prompt());self.add(sample(),BASE+5,'codex_core::session::turn');self.scan()
        self.ingest(payload(cached_token_count=None))
        self.assertEqual(API(self.con).overview({})['summary']['requests'],0)
        self.assertIsNone(self.recovered()[0]['total_tokens'])
        self.ingest(payload(BASE+5.1,cached_token_count=90))
        self.assertEqual(API(self.con).overview({})['summary']['requests'],0)

    def test_late_diagnostics_and_native_rollout_supersede_telemetry(self):
        self.ingest(payload())
        self.assertEqual(API(self.con).overview({})['summary']['requests'],0)
        self.add(prompt());self.add(sample(),BASE+5,'codex_core::session::turn');self.scan()
        self.assertEqual(API(self.con).overview({})['summary']['requests'],1)
        from test_usage import event,tokens
        folder=self.home/'sessions';folder.mkdir()
        records=[event('session_meta',dict(id=THREAD,session_id=THREAD)),event('turn_context',dict(turn_id=TURN)),event('token_usage_record',dict(thread_id=THREAD,session_id=THREAD,turn_id=TURN,root_turn_id=TURN,response_id='r',usage=tokens(80,60,20,5)))]
        (folder/'a.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records));self.scan()
        self.assertEqual(API(self.con).overview({})['summary']['total_tokens'],100)
        self.assertEqual(API(self.con).overview({})['summary']['requests'],1)

    def test_association_is_explicit_and_survives_rescan(self):
        from codex_usage.supplemental import link_session
        self.add(prompt());self.scan()
        self.con.execute("INSERT INTO threads(id,session_id,title) VALUES ('parent','parent','Parent task')")
        self.assertEqual(API(self.con).session('parent',{})['supplemental'],[])
        link_session(self.con,THREAD,'parent');self.scan()
        self.assertEqual(len(API(self.con).session('parent',{})['supplemental']),1)
        self.add(sample(),BASE+5,'codex_core::session::turn');self.scan();self.ingest(payload())
        self.assertEqual(API(self.con).session('parent',{})['summary']['total_tokens'],100)

    def test_telemetry_without_diagnostics_is_still_visible_and_redacts_metadata(self):
        self.ingest(payload(**{'event.name':'codex.user_prompt','prompt':'my request','user.email':'private@example.com'}))
        rows=self.recovered();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['preview'],'my request');self.assertIsNone(rows[0]['total_tokens'])
        self.assertNotIn('private@example.com',''.join(r[0] for r in self.con.execute('SELECT payload FROM telemetry_events')))

    def test_partial_sampling_remains_visible_as_incomplete(self):
        self.add(prompt());self.add(sample(),BASE+5,'codex_core::session::turn')
        self.add(sample(200),BASE+10,'codex_core::session::turn');self.scan();self.ingest(payload())
        rows=self.recovered()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['measured_requests'],1)
        self.assertEqual(rows[0]['missing_samples'],1)
        self.assertIsNone(rows[0]['total_tokens'])

    def test_ambiguous_event_invalidates_all_competing_samples(self):
        self.add(prompt());self.add(sample(),BASE+5,'codex_core::session::turn')
        self.add(sample(),BASE+7,'codex_core::session::turn');self.scan()
        # First event matches both samples; second only the later sample.
        self.ingest(payload(BASE+5));self.ingest(payload(BASE+7))
        self.assertEqual(API(self.con).overview({})['summary']['requests'],0)

    def test_later_prompt_without_diagnostic_turn_is_not_hidden_and_can_link(self):
        from codex_usage.supplemental import link_session
        self.add(prompt());self.scan()
        self.ingest(payload(BASE+20,**{'event.name':'codex.user_prompt','prompt':'later request'}))
        self.assertEqual(len(self.recovered()),2)
        self.con.execute("INSERT INTO threads(id,session_id,title) VALUES ('parent','parent','Parent task')")
        link_session(self.con,THREAD,'parent')
        self.assertEqual(len(API(self.con).session('parent',{})['supplemental']),2)
        self.ingest(payload(BASE,**{'event.name':'codex.user_prompt','prompt':'OCR status?'}))
        self.assertEqual(len(self.recovered()),2)

    def test_app_server_zero_event_time_uses_timestamp_attribute(self):
        from datetime import datetime,timezone
        self.add(prompt());self.add(sample(),BASE+5,'codex_core::session::turn');self.scan()
        timestamp=datetime.fromtimestamp(BASE+5,timezone.utc).isoformat().replace('+00:00','Z')
        event=payload(**{'event.timestamp':timestamp})
        record=event['resourceLogs'][0]['scopeLogs'][0]['logRecords'][0]
        record['timeUnixNano']='0';record['observedTimeUnixNano']=str(int((BASE+9)*1e9))
        self.ingest(event)
        record['observedTimeUnixNano']=str(int((BASE+20)*1e9));self.ingest(event)
        self.assertEqual(API(self.con).overview({})['summary']['total_tokens'],100)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM telemetry_events').fetchone()[0],1)
