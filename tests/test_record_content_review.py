"""Synthetic source journals; no real match, assets, capture or network."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from dota2_insights import record_content_review as review
from dota2_insights.closed_journals import ClosedJournals, explicit_match


class AnchorTests(unittest.TestCase):
    def test_same_match_bracketing_is_only_a_candidate(self):
        index = review.AnchorIndex([(0,'123'),(2,'123')])
        self.assertEqual(index.candidate('1970-01-01T00:00:01Z'),
            ('123','utc_bracket_candidate_not_explicit_identity'))

    def test_cross_match_large_gap_and_outside_stay_unassigned(self):
        for anchors, reason in (([(0,'123'),(2,'456')],'cross_match_anchor_boundary'),
                                ([(0,'123'),(10,'123')],'gsi_anchor_gap')):
            self.assertEqual(review.AnchorIndex(anchors).candidate('1970-01-01T00:00:01Z')[1],reason)
        self.assertIsNone(review.AnchorIndex([(2,'123'),(3,'123')]).candidate('1970-01-01T00:00:01Z')[0])

    def test_duplicate_conflicting_anchors_and_invalid_utc_rejected(self):
        index = review.AnchorIndex([(0,'123'),(0,'456'),(2,'123')])
        self.assertEqual(index.candidate('1970-01-01T00:00:01Z')[1],'ambiguous_gsi_anchor')
        for value in ('1970-01-01T00:00:01',None,'bad'):
            self.assertEqual(index.candidate(value)[1],'missing_or_invalid_utc')

    def test_identity_fields_must_not_conflict(self):
        self.assertEqual(explicit_match({'view_input':{'snapshot':{'match_id':'123'}}}),'123')
        with self.assertRaisesRegex(ValueError,'identity_conflict'):
            explicit_match({'match_id':'123','clock_context':{'match_id':'456'}})


class ContentTests(unittest.TestCase):
    def fixture(self):
        from test_helper_handoff import HandoffTests
        f = HandoffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        return f

    def gsi_row(self, amount=3000, *, second=0, status='partial'):
        return {'match_id':'12345','provider_at_utc':f'2026-10-25T09:10:{second:02d}Z',
            'received_at_utc':f'2026-10-25T09:10:{second:02d}.100000Z',
            'map_context':{'game_state':'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'},
            'self_economy':{'hero_id':'npc_dota_hero_axe', 'amount':amount,'cash_value':1000,
                'item_value':2000,'upgrade_value':0,'economy_status':status,'missing_reasons':[]}}

    def write_rows(self,path,rows):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')

    def add_component(self,f,p,kind,name,rows):
        path = f.history/'process-metrics'/('run_'+'e'*32)/name
        self.write_rows(path,rows)
        ref = {'root_key':'helper_history','path':path.relative_to(f.history).as_posix(),
            'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'content_kind':'jsonl'}
        p['sources'].append(ref)
        p['components'].append({'kind':kind,'state':'closed_verified','journals':[{'source':ref}],
            'warnings':[],'issues':[]})
        return ref

    def test_amount_decrease_is_retained_not_smoothed(self):
        f = self.fixture()
        self.write_rows(f.journal,[self.gsi_row(), self.gsi_row(2000,second=2)])
        result = review.summarize(f.inspect(), f.roots)
        own = result['own_gsi']['12345']
        self.assertEqual(own['large_decrease_count'],1)
        self.assertEqual(own['decreases'][0]['amount'],2000)
        self.assertEqual(own['arithmetic_disagreement_count'],1)
        self.assertTrue(result['source_verified_before_and_after'])

    def test_reported_net_worth_not_compared_with_partial_components(self):
        f = self.fixture()
        self.write_rows(f.journal,[self.gsi_row(9999,status='reported')])
        own = review.summarize(f.inspect(),f.roots)['own_gsi']['12345']
        self.assertEqual(own['arithmetic_disagreement_count'],0)
        self.assertEqual(own['arithmetic_comparable_rows'],0)
        self.assertEqual(own['economy_status']['reported'],1)

    def test_legacy_journal_also_audits_same_packet_display(self):
        f = self.fixture()
        row = self.gsi_row(status='reported')
        row['provider_timestamp'] = 100
        row['display_economy'] = {'amount': 1, 'raw_amount': 3000, 'valuation_provider_timestamp': 100}
        self.write_rows(f.journal, [row])
        result = review.summarize(f.inspect(), f.roots)
        display = result['own_economy_audit']['12345']['display_checks']
        self.assertEqual(display['normal_comparable_rows'], 1)
        self.assertEqual(display['amount_mismatches'], 1)
        self.assertIn('同包显示核对', review.render_markdown([result]))

    def test_boolean_and_unknown_components_not_coerced_to_zero(self):
        f = self.fixture()
        row = self.gsi_row()
        row['self_economy']['cash_value'] = True
        self.write_rows(f.journal,[row])
        own = review.summarize(f.inspect(),f.roots)['own_gsi']['12345']
        self.assertEqual(own['arithmetic_comparable_rows'],0)

    def test_utc_candidate_does_not_enter_match_facts(self):
        f = self.fixture()
        self.write_rows(f.journal,[self.gsi_row(),self.gsi_row(second=2)])
        p = f.inspect()
        self.add_component(f,p,'process_metrics','metrics.jsonl',[{'observed_at_utc':'2026-10-25T09:10:01Z'}])
        r = review.summarize(p,f.roots)
        j = r['journal_alignment'][0]
        self.assertEqual(j['utc_candidate_rows']['12345'],1)
        self.assertEqual(j['utc_candidate_spans'][0]['first_line'],1)
        self.assertEqual(j['utc_candidate_spans'][0]['time_field'],'observed_at_utc')
        self.assertEqual(r['match_facts'],{})

    def test_source_clock_discontinuity_blocks_utc_attribution(self):
        f = self.fixture()
        self.write_rows(f.journal,[self.gsi_row(),self.gsi_row(second=2)])
        p = f.inspect()
        self.add_component(f,p,'process_metrics','metrics.jsonl',[{'observed_at_utc':'2026-10-25T09:10:01Z',
            'utc_interval_consistent':False}])
        j = review.summarize(p,f.roots)['journal_alignment'][0]
        self.assertEqual(j['utc_candidate_rows'],{})
        self.assertEqual(j['unassigned_reasons']['source_utc_inconsistent'],1)

    def test_explicit_foreign_session_in_nested_context_rejected(self):
        f = self.fixture()
        p = f.inspect()
        self.add_component(f,p,'process_metrics','metrics.jsonl',[{'clock_context':{
            'match_id':'12345','record_session_id':'run_'+'f'*32}}])
        with self.assertRaisesRegex(ValueError,'session_identity_conflict'):
            review.summarize(p,f.roots)

    def test_candidate_with_explicit_identity_stays_explicit(self):
        f = self.fixture()
        p = f.inspect()
        self.add_component(f,p,'process_metrics','metrics.jsonl',[{'match_id':'12345',
            'observed_at_utc':'2026-10-25T09:10:01Z'}])
        j = review.summarize(p,f.roots)['journal_alignment'][0]
        self.assertEqual(j['explicit_match_rows']['12345'],1)
        self.assertEqual(j['utc_candidate_rows'],{})

    def test_raw_private_fields_do_not_escape_allowlist(self):
        f = self.fixture()
        result = review.summarize(f.inspect(),f.roots)
        raw = json.dumps(result)
        self.assertNotIn('MUST_NOT_BE_COPIED',raw)
        self.assertNotIn('PRIVATE_PIXELS',raw)
        self.assertNotIn(str(f.history),raw)

    def test_streaming_requires_verified_context(self):
        f = self.fixture()
        p = f.inspect()
        reader = ClosedJournals(p,f.roots)
        with self.assertRaisesRegex(ValueError,'verified_context'):
            list(reader.journals())
        with self.assertRaisesRegex(ValueError,'verified_context'):
            list(reader.rows(p['components'][0]['journals'][0]['source']))

    def test_mutation_during_read_is_not_published(self):
        f = self.fixture()
        p = f.inspect()
        with self.assertRaises(ValueError):
            with ClosedJournals(p,f.roots) as reader:
                _,ref = next(reader.journals())
                rows = reader.rows(ref)
                next(rows)
                with f.journal.open('a') as stream: stream.write('{}\n')
                list(rows)

    def test_partially_consumed_stream_cannot_resume_after_verified_context(self):
        f = self.fixture()
        self.write_rows(f.journal,[self.gsi_row(),self.gsi_row(second=2)])
        p = f.inspect()
        with ClosedJournals(p,f.roots) as reader:
            _,ref=next(reader.journals())
            rows=reader.rows(ref)
            next(rows)
        with self.assertRaisesRegex(ValueError,'verified_context'):
            next(rows)

    def test_foreign_or_sealed_match_is_refused(self):
        f = self.fixture()
        p = f.inspect()
        for mid in ('99999','9056629627'):
            value = copy.deepcopy(p)
            self.add_component(f,value,'process_metrics','metrics.jsonl',[{'match_id':mid}])
            with self.subTest(mid=mid), self.assertRaises(ValueError):
                review.summarize(value,f.roots)

    def test_high_cost_review_queue_is_not_a_confirmed_item(self):
        f = self.fixture()
        p = f.inspect()
        path = f.history/'matches/match_12345'/f.sid/'observations.jsonl'
        row = {'match_id':'12345','attempt':{'accepted':True,'hero':{
            'hero_id':'npc_dota_hero_axe','slots':[{'slot_id':'main_1','status':'unknown','candidate':'blink'}]}},
            'performance_ms':{'recognition':100}}
        self.write_rows(path,[row,row])
        ref = {'root_key':'helper_history','path':path.relative_to(f.history).as_posix(),
            'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'content_kind':'jsonl'}
        p['sources'].append(ref)
        p['components'].append({'kind':'economy_context','state':'closed_verified',
            'declared_match_id':'12345','journals':[{'source':ref}]})
        r = review.summarize(p,f.roots,catalogue={'blink':{'cost':2250}})
        priority = r['item_review_priorities'][0]
        self.assertEqual(priority['observations'],2)
        self.assertEqual(priority['first_evidence']['line'],1)
        self.assertEqual(priority['last_evidence']['line'],2)
        self.assertFalse(priority['identity_or_item_confirmed'])
        self.assertEqual(r['match_facts']['12345']['heroes']['npc_dota_hero_axe']['unknown_slots'],2)

    def test_catalogue_digest_mismatch_not_used_for_ranking(self):
        f = self.fixture()
        data = f.insights/'data'
        data.mkdir()
        (data/'items.json').write_text('{}')
        (data/'items.meta.json').write_text(json.dumps({'cache_sha256':'wrong'}))
        with self.assertRaisesRegex(ValueError,'catalogue_fingerprint'):
            review.item_catalogue(f.insights)

    def test_duplicate_session_associations_refused(self):
        f = self.fixture()
        p = f.inspect()
        p['matches'].append(copy.deepcopy(p['matches'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate_session'):
            review.summarize(p,f.roots)
