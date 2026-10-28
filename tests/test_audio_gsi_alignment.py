"""Generated detached packets only; no live GSI, device, network or replay."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from dota2_insights.audio_gsi_alignment import prepare
from dota2_insights.audio_match_index import sync as sync_audio
from dota2_insights.match_journal_index import sync as sync_gsi


class AudioGsiAlignmentTests(unittest.TestCase):
    def setUp(self):
        from test_audio_match_index import AudioMatchIndexTests
        from test_match_journals import MatchJournalTests
        self.audio=a=AudioMatchIndexTests();a.setUp();self.addCleanup(a.doCleanups)
        self.gsi=g=MatchJournalTests();g.setUp();self.addCleanup(g.doCleanups)
        g.game=a.fixture.scope['game_session_id'];g.session=a.fixture.scope['record_session_id']
        g.folder=(a.history/'self-economy'/('self-economy-'+g.run)/'closed-matches/match_12345'/g.game)
        g.folder.mkdir(parents=True)
        g.manifest.update(game_session_id=g.game,record_session_id=g.session)
        g.rows=[g.row(1,False),g.row(2,True)]
        context=a.fixture.reference['context']
        context.update(clock_time_s=10,hero_id='npc_dota_hero_axe',
            abilities=[{'slot':'ability0','name':'axe_berserkers_call','level':1,'cooldown':0,
                        'charges':None,'can_cast':True,'ability_active':False,'ultimate':False}])
        g.rows[0].update(gsi_source_id=context['recorded_source_context']['gsi_source_id'],
            provider_at_utc=context['recorded_source_context']['provider_at_utc'],clock_time_s=10,
            gsi_probe={'schema_version':1,'own_abilities':[
                {**context['abilities'][0],'slot':0}]})

    def publish(self):
        self.audio.fixture.write();self.gsi.publish()
        entry=sync_audio(self.audio.project,self.audio.roots)['entries'][0]
        native=sync_gsi(self.audio.project,self.audio.roots)['entries']
        return entry,native

    def result(self):
        entry,native=self.publish()
        return prepare(entry,native,self.audio.roots)

    def test_exact_source_and_zero_cooldown_are_compared_without_cast_truth(self):
        result=self.result();row=result['items'][0]
        self.assertEqual(row['source_record_status'],'exact_recorded_source_id')
        self.assertEqual(row['ability_comparison']['status'],'equal_on_comparable_fields')
        self.assertEqual(row['ability_comparison']['compared_fields'],5)
        self.assertEqual(row['ability_comparison']['missing_fields'],1)
        self.assertFalse(result['cast_or_CD_boundary_verified'])
        self.assertTrue(result['audio_source_verified_before_and_after'])

    def test_same_timestamp_without_same_source_or_scope_is_not_matched(self):
        original=deepcopy(self.gsi.rows)
        for change in ('missing','other_id','other_session'):
            self.gsi.rows=deepcopy(original)
            self.gsi.manifest['record_session_id']=self.audio.fixture.scope['record_session_id']
            if change=='missing':self.gsi.rows[0].pop('gsi_source_id')
            elif change=='other_id':self.gsi.rows[0]['gsi_source_id']='gsi_'+'e'*32
            else:
                self.gsi.manifest['record_session_id']='run_'+'e'*32
                for row in self.gsi.rows:row['record_session_id']='run_'+'e'*32
            with self.subTest(change=change):
                self.assertEqual(self.result()['items'][0]['source_record_status'],'unknown')

    def test_duplicate_source_id_is_ambiguous_even_identical_values(self):
        duplicate=deepcopy(self.gsi.rows[0]);duplicate['sequence']=2
        self.gsi.rows[1]['sequence']=3;self.gsi.rows.insert(1,duplicate)
        row=self.result()['items'][0]
        self.assertEqual(row['reason'],'source_id_ambiguous')
        self.assertEqual(row['matching_record_count'],2)
        self.assertNotIn('native_source',row)

    def test_matching_id_with_clock_hero_or_cooldown_conflict_is_unknown(self):
        original=deepcopy(self.gsi.rows)
        for change in ('clock','hero','cooldown'):
            self.gsi.rows=deepcopy(original)
            if change=='clock':self.gsi.rows[0]['clock_time_s']=11
            elif change=='hero':self.gsi.rows[0]['self_economy']['hero_id']='npc_dota_hero_omniknight'
            else:self.gsi.rows[0]['gsi_probe']['own_abilities'][0]['cooldown']=5
            with self.subTest(change=change):
                row=self.result()['items'][0]
                self.assertEqual(row['source_record_status'],'unknown')
                self.assertEqual(row['reason'],'source_id_ability_values_disagree' if change=='cooldown' else 'source_id_content_disagrees')

    def test_missing_native_abilities_are_not_zero_or_a_contradiction(self):
        self.gsi.rows[0].pop('gsi_probe')
        row=self.result()['items'][0]
        self.assertEqual(row['source_record_status'],'exact_recorded_source_id')
        self.assertEqual(row['ability_comparison']['status'],'not_comparable')

    def test_missing_clock_is_not_reported_as_a_numeric_mismatch(self):
        self.gsi.rows[0].pop('clock_time_s')
        row=self.result()['items'][0]
        self.assertEqual(row['source_record_status'],'unknown')
        self.assertEqual(row['reason'],'source_id_content_not_comparable')

    def test_changed_native_after_index_requires_resync(self):
        entry,native=self.publish()
        self.gsi.rows[0]['clock_time_s']=11;self.gsi.publish()
        with self.assertRaises(ValueError):prepare(entry,native,self.audio.roots)

    def test_daily_report_contains_counts_and_not_reference_payloads(self):
        from tools.review_recordings import run
        self.publish()
        output=run(self.audio.project,roots=self.audio.roots)
        folder=Path(output['report']).parent
        report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
        row=report['audio_match_reports'][0];summary=row['gsi_source_alignment']
        self.assertEqual(summary['reference_rows'],1)
        self.assertEqual(summary['exact_source_record_rows'],1)
        self.assertTrue(row['native_GSI_journal_join_verified'])
        self.assertFalse(summary['skill_audibility_verified'])
        self.assertNotIn('items',summary)
        self.assertIn('来源配对是日志覆盖',(folder/'REPORT.md').read_text(encoding='utf-8'))

    def test_missing_native_index_is_not_zero_source_records(self):
        from tools.review_recordings import run
        from tools.review_closed_record_content import generate
        self.publish();run(self.audio.project,roots=self.audio.roots)
        (self.audio.project/'diagnostics/native-match-handoff/index.json').unlink()
        output=generate(self.audio.project,roots=self.audio.roots)
        report=json.loads((Path(output['report']).parent/'report.json').read_text(encoding='utf-8'))
        summary=report['audio_match_reports'][0]['gsi_source_alignment']
        self.assertEqual(summary['native_source_index_state'],'missing_not_treated_as_zero')
        self.assertEqual(summary['unmatched_reasons'],{'native_index_not_available':1})

    def test_lineage_audit_cannot_consume_a_blind_deferred_reference_package(self):
        from dota2_insights.audio_match_handoff import verify
        from dota2_insights.audio_gsi_alignment import align_package
        _,native=self.publish()
        package=verify(self.audio.fixture.folder,defer_reference_review=True)
        with patch('dota2_insights.audio_gsi_alignment.open_gsi',side_effect=AssertionError('early native GSI')):
            with self.assertRaisesRegex(ValueError,'requires_semantic_review'):
                align_package(package,native,self.audio.roots,native_index_state='read_and_sources_revalidated')
