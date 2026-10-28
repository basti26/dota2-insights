"""Independent IPC-context fixtures; no Foresight import, capture or game."""
import copy
import unittest
from unittest.mock import patch

from dota2_insights.minimap_game_context import BASIS, summary, binding


def header():
    return {'schema_version':1, 'relay_id':'c'*32, 'basis':BASIS, 'declared_match_ids':['12345']}


def sample():
    return {'kind':'sample', 'sequence':1, 'match_id':None, 'captured_monotonic':10.,
        'captured_at_utc':'2026-10-26T00:00:01Z', 'game_context_binding': {
            'schema_version':1, 'status':'explicit_last_context', 'basis':BASIS, 'relay_id':'c'*32,
            'sequence':1, 'sent_monotonic_ns':9_500_000_000, 'capture_monotonic_ns':10_000_000_000,
            'provider_age_at_capture_s':1., 'context': {'match_id':'12345','game_session_id':'game_'+'a'*32,
                'record_session_id':'run_'+'b'*32,'provider_at_utc':'2026-10-26T00:00:00Z',
                'game_state':'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS','clock_time_s':10}}}


class MinimapContextTests(unittest.TestCase):
    def test_explicit_context_validated_without_whole_run_identity_claim(self):
        r = sample()
        self.assertEqual(binding(r, summary(header()))['context']['match_id'], '12345')
        self.assertIsNone(r['match_id'])
        self.assertIsNone(binding({'kind':'sample'}, None))

    def test_summary_checks_locked_match_before_stream_bodies(self):
        h = header();h['declared_match_ids']=['9056629627']
        with self.assertRaises(ValueError): summary(h)

    def test_null_public_match_id_keeps_local_session_only(self):
        h=header();h['declared_match_ids']=[]
        r=sample();r['game_context_binding']['context']['match_id']=None
        self.assertIsNone(binding(r,summary(h))['context']['match_id'])

    def test_future_expired_redefined_capture_or_old_provider_refused(self):
        for change in ({'sent_monotonic_ns':11_000_000_000}, {'sent_monotonic_ns':1},
                       {'capture_monotonic_ns':10_000_000_001}, {'provider_age_at_capture_s':0}):
            r = sample();r['game_context_binding'].update(change)
            with self.assertRaises(ValueError):binding(r, header())

    def test_video_or_undeclared_match_or_raw_payload_not_admitted(self):
        r=sample();r['source_video_time_s']=1
        with self.assertRaises(ValueError):binding(r,header())
        r=sample();r['game_context_binding']['context']['match_id']='12346'
        with self.assertRaises(ValueError):binding(r,header())
        r=sample();r['game_context_binding']['context']['raw_gsi']={}
        with self.assertRaises(ValueError):binding(r,header())

    def test_sealed_run_requires_matching_header_and_rejects_context_redefinition(self):
        from test_minimap_handoff import MinimapHandoffTests
        from dota2_insights.minimap_handoff import review,verify
        f=MinimapHandoffTests();f.setUp();self.addCleanup(f.doCleanups)
        f.rows[0].update(sample())
        f.session['frame_context_relay']=header()
        def publish():
            f.publish();f.document['frame_context_relay']=header();return f.write_receipt()
        publish()
        result=review(f.root,f.run)
        self.assertEqual(result['frame_context_counts'],{'explicit_last_context':1})
        self.assertEqual(result['explicit_context_scopes'][0]['match_id'],'12345')
        self.assertFalse(result['explicit_context_scopes'][0]['pixel_identity_verified'])
        local=copy.deepcopy(f.rows[0]);local['sequence']=2;local['captured_monotonic']=10.5
        local['captured_at_utc']='2026-10-26T00:00:01.500000Z'
        local['game_context_binding'].update(sequence=2,sent_monotonic_ns=10_000_000_000,
            capture_monotonic_ns=10_500_000_000,provider_age_at_capture_s=1.5)
        local['game_context_binding']['context'].update(match_id=None,game_session_id='game_'+'f'*32)
        f.rows.append(local);f.session['frames_saved']=2;publish()
        result=review(f.root,f.run)
        self.assertEqual({s['match_id'] for s in result['explicit_context_scopes']},{None,'12345'})
        # Equal coarse clock ticks are legal; decreasing send clocks are not.
        first_sent=f.rows[0]['game_context_binding']['sent_monotonic_ns']
        f.rows[1]['game_context_binding']['sent_monotonic_ns']=first_sent
        publish();verify(f.root,f.run)
        f.rows[1]['game_context_binding']['sent_monotonic_ns']=first_sent-1
        publish()
        with self.assertRaisesRegex(ValueError,'redefined'):verify(f.root,f.run)
        f.rows=f.rows[:1];f.session['frames_saved']=1
        bad=copy.deepcopy(f.rows[0]);bad['sequence']=2
        bad['game_context_binding']['context']['clock_time_s']=11
        f.rows.append(bad);f.session['frames_saved']=2;publish()
        with self.assertRaisesRegex(ValueError,'redefined'):verify(f.root,f.run)

    def test_combined_report_links_only_exact_match_and_both_sessions(self):
        import json
        import shutil
        from pathlib import Path
        from test_minimap_handoff import MinimapHandoffTests
        from test_match_journal_index import NativeIndexTests
        from tools.review_recordings import run
        f=MinimapHandoffTests();f.setUp();self.addCleanup(f.doCleanups)
        f.rows[0].update(sample());f.session['frame_context_relay']=header()
        f.publish();f.document['frame_context_relay']=header();f.write_receipt()
        n=NativeIndexTests();n.setUp();self.addCleanup(n.doCleanups)
        shutil.copytree(f.root/'minimap-learning',n.fixture.root/'minimap-learning')
        result=run(n.project,roots=n.roots)
        doc=json.loads((Path(result['report']).parent/'report.json').read_text(encoding='utf-8'))
        scope=doc['minimap_run_reports'][0]['explicit_context_scopes'][0]
        self.assertEqual(scope['linked_native_gsi_packages'],[doc['native_reports'][0]['package_id']])
        # Identical match and timestamps are insufficient after record-session change.
        sid='run_'+'e'*32
        n.fixture.manifest['record_session_id']=sid
        for row in n.fixture.rows:row['record_session_id']=sid
        n.fixture.publish()
        result=run(n.project,roots=n.roots)
        doc=json.loads((Path(result['report']).parent/'report.json').read_text(encoding='utf-8'))
        scope=doc['minimap_run_reports'][0]['explicit_context_scopes'][0]
        self.assertEqual(scope['linked_native_gsi_packages'],[])
