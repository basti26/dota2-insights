"""Closed-source pairing fixtures only; no GSI, capture or other repository imports."""
import copy
import json
import shutil
from pathlib import Path
import unittest

from dota2_insights.match_journal_index import sync as sync_gsi
from dota2_insights.minimap_index import sync as sync_minimap
from dota2_insights.minimap_gsi_alignment import prepare
from test_minimap_game_context import sample, header
from test_gsi_evidence_census import row as position_row


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        from test_minimap_handoff import MinimapHandoffTests
        from test_match_journal_index import NativeIndexTests
        self.n = NativeIndexTests(); self.n.setUp(); self.addCleanup(self.n.doCleanups)
        self.m = MinimapHandoffTests(); self.m.setUp(); self.addCleanup(self.m.doCleanups)
        self.token = 'gsi_'+'d'*32
        self.m.rows[0].update(sample())
        context = self.m.rows[0]['game_context_binding']['context']
        context.update(gsi_source_id=self.token, provider_at_utc='2026-10-26T00:00:01Z')
        self.m.rows[0]['game_context_binding']['provider_age_at_capture_s'] = 0.
        r = self.n.fixture.rows[0]
        extra = position_row()
        r.update(gsi_source_id=self.token, clock_time_s=10,
                 gsi_probe=extra['gsi_probe'], self_status=extra['self_status'])
        r['self_status']['flags']['alive'] = True

    def publish(self):
        self.n.fixture.publish()
        self.m.session['frame_context_relay'] = header()
        self.m.publish(); self.m.document['frame_context_relay'] = header(); self.m.write_receipt()
        shutil.copytree(self.m.root/'minimap-learning', self.n.fixture.root/'minimap-learning', dirs_exist_ok=True)
        a = sync_minimap(self.n.project, self.n.roots)['entries'][0]
        b = sync_gsi(self.n.project, self.n.roots)['entries']
        return a, b

    def result(self):
        a, b = self.publish()
        return prepare(a, b, self.n.roots)

    def test_exact_source_id_zero_coordinates_without_pixel_truth_claim(self):
        result = self.result()
        item = result['samples'][0]
        self.assertEqual(item['source_record_status'], 'exact_recorded_source_id')
        self.assertEqual(item['position']['xpos'], 0)
        self.assertTrue(item['position_evidence_usable'])
        self.assertFalse(result['calibration_verified'])
        self.assertFalse(item['pixel_identity_verified'])
        scope = result['native_scope_selection'][0]
        self.assertEqual(scope['lookup_status'], 'matching_packages_verified')
        self.assertEqual(scope['wanted_distinct_source_ids'], 1)
        self.assertEqual(scope['verified_native_package_ids'], result['native_package_ids'])

    def test_unselected_scope_is_not_called_missing_source_record(self):
        a, b = self.publish()
        result = prepare(a, [], self.n.roots)
        scope = result['native_scope_selection'][0]
        self.assertEqual(scope['lookup_status'], 'scope_not_in_selected_index')
        self.assertEqual(scope['frame_rows'], 1)
        self.assertEqual(scope['verified_native_package_ids'], [])
        self.assertNotEqual(result['samples'][0]['source_record_status'], 'exact_recorded_source_id')

    def test_not_ready_metadata_does_not_open_body_or_verify_package(self):
        a, b = self.publish()
        b[0]['state'] = 'source_error'
        b[0]['source'] = 'not/a/valid/path'
        scope = prepare(a, b, self.n.roots)['native_scope_selection'][0]
        self.assertEqual(scope['lookup_status'], 'matching_index_entries_not_ready')
        self.assertEqual(scope['indexed_not_ready_entries'], 1)
        self.assertEqual(scope['verified_native_package_ids'], [])

    def test_verified_scope_does_not_imply_individual_source_ID_exists(self):
        self.n.fixture.rows[0]['gsi_source_id'] = 'gsi_'+'e'*32
        result = self.result()
        self.assertEqual(result['native_scope_selection'][0]['lookup_status'], 'matching_packages_verified')
        self.assertEqual(result['samples'][0]['source_record_status'], 'unknown')

    def test_timestamp_alone_or_other_session_never_matches(self):
        for case in ('no_token', 'other_session', 'other_token'):
            with self.subTest(case=case):
                original = copy.deepcopy(self.n.fixture.rows)
                if case == 'no_token': self.n.fixture.rows[0].pop('gsi_source_id')
                if case == 'other_token': self.n.fixture.rows[0]['gsi_source_id'] = 'gsi_'+'e'*32
                if case == 'other_session':
                    sid = 'run_'+'e'*32
                    self.n.fixture.manifest['record_session_id'] = sid
                    for r in self.n.fixture.rows: r['record_session_id'] = sid
                self.assertEqual(self.result()['samples'][0]['source_record_status'], 'unknown')
                self.n.fixture.rows = original
                self.n.fixture.manifest['record_session_id'] = original[0]['record_session_id']

    def test_duplicate_source_id_is_ambiguous_even_identical_coordinates(self):
        duplicate = copy.deepcopy(self.n.fixture.rows[0])
        duplicate['sequence'] = 2
        self.n.fixture.rows[1]['sequence'] = 3
        self.n.fixture.rows.insert(1, duplicate)
        item = self.result()['samples'][0]
        self.assertEqual(item['reason'], 'source_id_ambiguous')
        self.assertEqual(item['matching_record_count'], 2)
        self.assertNotIn('position', item)

    def test_matching_id_with_conflicting_clocks_not_used(self):
        self.n.fixture.rows[0]['clock_time_s'] = 11
        self.assertEqual(self.result()['samples'][0]['reason'], 'source_id_content_disagrees')
        self.n.fixture.rows[0]['clock_time_s'] = 10
        self.n.fixture.rows[0]['provider_at_utc'] = None
        self.assertEqual(self.result()['samples'][0]['reason'], 'source_id_content_disagrees')

    def test_death_and_missing_position_not_promoted_to_calibration(self):
        self.n.fixture.rows[0]['self_status']['flags']['alive'] = False
        item = self.result()['samples'][0]
        self.assertEqual(item['source_record_status'], 'exact_recorded_source_id')
        self.assertFalse(item['position_evidence_usable'])
        self.n.fixture.rows[0]['gsi_probe'].pop('own_position')
        self.assertIsNone(self.result()['samples'][0]['position'])

    def test_old_context_without_source_id_is_unknown_not_backfilled(self):
        self.m.rows[0]['game_context_binding']['context'].pop('gsi_source_id')
        item = self.result()['samples'][0]
        self.assertEqual(item['reason'], 'source_id_not_recorded')

    def test_changed_source_after_sync_refuses_before_publication(self):
        a, b = self.publish()
        self.n.fixture.rows[0]['clock_time_s'] = 11; self.n.fixture.publish()
        with self.assertRaises(ValueError): prepare(a, b, self.n.roots)

    def test_cli_syncs_before_reading_and_publishes_completed_json(self):
        from tools.prepare_minimap_gsi_alignment import run
        self.publish()
        result = run(self.m.run, root=self.n.project, roots=self.n.roots)
        output = Path(result['output'])
        report = json.loads((output/'report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['source_verified_before_and_after'])
        self.assertEqual(report['outcomes'], {'exact_recorded_source_id':1})
        self.assertFalse((output/'report.json.tmp').exists())

    def test_daily_report_includes_exact_source_coverage_without_all_sample_payloads(self):
        from tools.review_recordings import run
        self.publish()
        result = run(self.n.project, roots=self.n.roots)
        folder = Path(result['report']).parent
        doc = json.loads((folder/'report.json').read_text(encoding='utf-8'))
        summary = doc['minimap_run_reports'][0]['gsi_source_alignment']
        self.assertEqual(summary['sample_rows'], 1)
        self.assertEqual(summary['frame_source_id_rows'], 1)
        self.assertEqual(summary['exact_source_record_rows'], 1)
        self.assertEqual(summary['usable_position_rows'], 1)
        self.assertEqual(summary['unmatched_reasons'], {})
        self.assertNotIn('samples', summary)
        self.assertFalse(summary['calibration_verified'])
        self.assertIn('来源覆盖，不是识别准确率', (folder/'REPORT.md').read_text(encoding='utf-8'))
        self.assertEqual(summary['native_scope_selection'][0]['lookup_status'], 'matching_packages_verified')
        self.assertIn('匹配范围包已核验', (folder/'REPORT.md').read_text(encoding='utf-8'))

    def test_daily_report_old_context_and_missing_index_remain_distinct(self):
        from tools.review_recordings import run
        from tools.review_closed_record_content import generate
        self.m.rows[0]['game_context_binding']['context'].pop('gsi_source_id')
        self.publish()
        run(self.n.project, roots=self.n.roots)
        (self.n.project/'diagnostics/native-match-handoff/index.json').unlink()
        result = generate(self.n.project, roots=self.n.roots)
        doc = json.loads((Path(result['report']).parent/'report.json').read_text(encoding='utf-8'))
        summary = doc['minimap_run_reports'][0]['gsi_source_alignment']
        self.assertEqual(summary['native_source_index_state'], 'missing_not_treated_as_zero')
        self.assertEqual(summary['frame_source_id_rows'], 0)
        self.assertEqual(summary['unmatched_reasons'], {'source_id_not_recorded':1})
