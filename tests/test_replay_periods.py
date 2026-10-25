"""Generated fixtures only: period labels never need a replay or a network call."""
import copy
from dataclasses import replace
import hashlib
import json
import unittest

from dota2_insights.replay_periods import (
    MAX_ABS_GAME_TIME_S,
    DurationEvidence,
    ObservedEndEvidence,
    bind_available_end_evidence,
    bind_duration_evidence,
    bind_observed_end_evidence,
    iter_period_labels,
    label_replay_periods,
)
from dota2_insights.replay_phase_evidence import PhaseTracker


class ReplayPeriodTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {
            'kind': 'offline_replay_archive',
            'match_id': '9044645010',
            'session_id': 'generated-session-a',
            'source_sha256': 'a' * 64,
            'files': {'events.jsonl': {'sha256': 'b' * 64}},
            'last_exported_game_time_s': 2599,
        }

    def bound(self, facts=None, request_changes=None, **kwargs):
        facts = {'match_id': 9044645010, 'duration': 2454} if facts is None else facts
        raw = json.dumps(facts, allow_nan=True).encode('utf-8')
        request = {
            'match_id': '9044645010',
            'method': 'GET',
            'http_status': 200,
            'status': 'downloaded_sanitized',
            'url': 'https://api.opendota.com/api/matches/9044645010',
            'facts_sha256': hashlib.sha256(raw).hexdigest(),
            'parsed_version': None,
        }
        request.update(request_changes or {})
        return bind_duration_evidence(self.manifest, raw, request, **kwargs)

    def rows(self, clocks):
        return ({'match_id': '9044645010', 'session_id': 'generated-session-a',
                 'kind': 'combat_event', 'tick': i + 20, 'game_time_s': clock,
                 'event_type': 'damage', 'event_game_time_s': clock}
                for i, clock in enumerate(clocks))

    def test_integer_duration_has_closed_one_second_uncertainty_band(self):
        report = label_replay_periods(
            self.rows([-1, 0, 2453.999, 2454, 2454.5, 2455, 2455.001, 2599]),
            self.manifest, self.bound())
        self.assertEqual([row['period'] for row in report['labels']], [
            'pregame', 'in_match', 'in_match', 'end_boundary_uncertain',
            'end_boundary_uncertain', 'end_boundary_uncertain',
            'post_match_tail', 'post_match_tail'])
        self.assertEqual(report['summary']['counts']['end_boundary_uncertain'], 3)
        self.assertEqual(report['summary']['duration_s'], 2454)
        self.assertFalse(report['summary']['exact_game_state_verified'])
        self.assertEqual(report['summary']['end_boundary_interval_s'], [2454, 2455])

    def test_missing_independent_evidence_never_uses_archive_last_clock_or_marker(self):
        self.manifest['match_phase_boundary'] = {'combat_log_match_duration_s': 2454}
        report = label_replay_periods(self.rows([-1, 0, 2599]), self.manifest)
        self.assertEqual([row['period'] for row in report['labels']],
                         ['pregame', 'end_unknown', 'end_unknown'])
        self.assertIsNone(report['summary']['duration_s'])
        self.assertEqual(report['summary']['duration_evidence']['status'], 'unavailable')

    def test_missing_or_null_facts_duration_is_end_unknown(self):
        for facts in ({'match_id': 9044645010}, {'match_id': 9044645010, 'duration': None}):
            with self.subTest(facts=facts):
                evidence = self.bound(facts)
                report = label_replay_periods(self.rows([2599]), self.manifest, evidence)
                self.assertEqual(report['labels'][0]['period'], 'end_unknown')
                self.assertEqual(evidence.reason, 'independent_duration_missing')
                self.assertIsNotNone(report['summary']['duration_evidence']['facts_sha256'])

    def test_facts_without_receipt_cannot_bind_a_duration(self):
        raw = b'{"match_id":9044645010,"duration":2454}'
        evidence = bind_duration_evidence(self.manifest, raw, None)
        report = label_replay_periods(self.rows([2599]), self.manifest, evidence)
        self.assertEqual(report['labels'][0]['period'], 'end_unknown')
        self.assertEqual(evidence.reason, 'source_receipt_missing')

    def test_one_sided_sources_still_reject_an_explicit_wrong_identity(self):
        with self.assertRaises(ValueError):
            bind_duration_evidence(self.manifest, b'{"match_id":123,"duration":2454}')
        request = {'match_id': '123', 'method': 'GET', 'http_status': 200,
                   'status': 'downloaded_sanitized', 'facts_sha256': 'b' * 64,
                   'url': 'https://api.opendota.com/api/matches/123'}
        with self.assertRaises(ValueError):
            bind_duration_evidence(self.manifest, None, request)

    def test_missing_and_null_clock_are_distinct_from_zero_and_not_filled(self):
        rows = [{'match_id': '9044645010', 'tick': 1},
                {'match_id': '9044645010', 'tick': 2, 'game_time_s': None},
                {'match_id': '9044645010', 'tick': 3, 'game_time_s': 0}]
        original = copy.deepcopy(rows)
        report = label_replay_periods(rows, self.manifest, self.bound())
        self.assertEqual([label['period'] for label in report['labels']],
                         ['clock_unknown', 'clock_unknown', 'in_match'])
        self.assertNotIn('game_time_s', report['labels'][0]['source_clocks'])
        self.assertIsNone(report['labels'][1]['source_clocks']['game_time_s'])
        self.assertEqual(rows, original)

    def test_original_order_raw_clocks_and_event_dicts_remain_unchanged(self):
        rows = list(self.rows([2599, 2, -1]))
        rows[0].update({'event_tick': 8, 'packet_tick': 21,
                        'event_game_time_s': 2450, 'timestamp_raw': 17.5,
                        'nested': {'keep': ['original']}})
        original = copy.deepcopy(rows)
        labels = list(iter_period_labels(iter(rows), self.manifest, self.bound()))
        self.assertEqual(rows, original)
        self.assertEqual([label['sequence'] for label in labels], [1, 2, 3])
        self.assertEqual([label['source_clocks']['game_time_s'] for label in labels], [2599, 2, -1])
        self.assertEqual(labels[0]['source_clocks']['event_game_time_s'], 2450)
        self.assertEqual(labels[0]['source_ref'], 'events.jsonl:1')
        self.assertNotIn('event_type', labels[0])
        report = label_replay_periods(iter(rows), self.manifest, self.bound())
        self.assertEqual(report['summary']['clock_order_regressions'], 2)
        self.assertEqual(report['summary']['max_observed_game_time_s'], 2599)
        self.assertEqual(report['summary']['duration_s'], 2454)

    def test_refs_and_source_hashes_survive_without_claiming_version_or_clock_certification(self):
        evidence = self.bound(facts_ref='source/facts.json', request_ref='source/request.json')
        summary = label_replay_periods(self.rows([1]), self.manifest, evidence)['summary']
        bound = summary['duration_evidence']
        self.assertEqual(bound['facts_ref'], 'source/facts.json')
        self.assertEqual(bound['request_ref'], 'source/request.json')
        self.assertEqual(bound['source_replay_sha256'], 'a' * 64)
        self.assertEqual(len(bound['request_sha256']), 64)
        self.assertIsNone(bound['source_version'])
        self.assertFalse(bound['global_clock_certified'])

    def test_bad_match_or_receipt_is_rejected(self):
        changes = [
            {'match_id': '123'}, {'match_id': True}, {'http_status': True},
            {'http_status': 201}, {'method': 'POST'},
            {'url': 'https://example.com/api/matches/9044645010'},
            {'url': 'https://api.opendota.com/api/matches/9044645010?api_key=x'},
            {'facts_sha256': '0' * 64}, {'status': 'failed'},
            {'session_id': 'other-session'},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bound(request_changes=change)
        with self.assertRaises(ValueError):
            self.bound({'match_id': 123, 'duration': 2454})

    def test_invalid_duration_is_rejected(self):
        for value in (True, False, -1, 0, 1.5, 2454.0, '2454',
                      float('nan'), float('inf'), 10 ** 100):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.bound({'match_id': 9044645010, 'duration': value})

    def test_bad_clock_values_are_rejected_instead_of_coerced_or_hidden(self):
        for value in (True, False, '1', float('nan'), float('inf'),
                      -float('inf'), MAX_ABS_GAME_TIME_S + 1, 10 ** 100):
            with self.subTest(value=value), self.assertRaises(ValueError):
                label_replay_periods(self.rows([value]), self.manifest, self.bound())
        for field in ('tick', 'event_tick', 'packet_tick', 'net_tick'):
            for value in (True, 1.5, 10 ** 100):
                row = next(self.rows([0]))
                row[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    label_replay_periods([row], self.manifest, self.bound())
        row = next(self.rows([0]))
        row['event_game_time_s'] = float('nan')
        with self.assertRaises(ValueError):
            label_replay_periods([row], self.manifest, self.bound())

    def test_row_match_or_session_mismatch_is_rejected(self):
        for change in ({'match_id': '123'}, {'match_id': True},
                       {'session_id': 'other-session'}, {'session_id': None}):
            row = next(self.rows([0]))
            row.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                label_replay_periods([row], self.manifest, self.bound())

    def test_bound_evidence_cannot_be_reused_after_archive_identity_changes(self):
        evidence = self.bound()
        for change in ({'session_id': 'session-b'}, {'source_sha256': 'c' * 64},
                       {'files': {'events.jsonl': {'sha256': 'd' * 64}}},
                       {'match_id': '123'}):
            other = copy.deepcopy(self.manifest)
            other.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                label_replay_periods([], other, evidence)
        with self.assertRaises(ValueError):
            label_replay_periods([], self.manifest, replace(evidence, duration_s=True))

    def test_missing_archive_or_row_identity_is_not_defaulted(self):
        for field in ('match_id', 'source_sha256', 'kind'):
            manifest = copy.deepcopy(self.manifest)
            del manifest[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                label_replay_periods([], manifest)
        with self.assertRaises(ValueError):
            label_replay_periods([{'tick': 1, 'game_time_s': 0}], self.manifest)

    def test_malformed_duplicate_and_nonfinite_facts_json_is_rejected(self):
        for raw in (b'[]', b'{', b'{"match_id":9044645010,"match_id":123}',
                    b'{"match_id":9044645010,"duration":NaN}'):
            receipt = {'match_id': '9044645010', 'method': 'GET', 'http_status': 200,
                       'status': 'downloaded_sanitized',
                       'url': 'https://api.opendota.com/api/matches/9044645010',
                       'facts_sha256': hashlib.sha256(raw).hexdigest()}
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                bind_duration_evidence(self.manifest, raw, receipt)

    def test_hero_samples_can_use_separate_refs_and_empty_input_has_no_clock_extent(self):
        rows = list(self.rows([0, 2599]))
        for row in rows:
            row['kind'] = 'hero_sample'
        labels = list(iter_period_labels(rows, self.manifest, self.bound(),
                                         source_name='hero_samples.jsonl'))
        self.assertEqual(labels[1]['source_ref'], 'hero_samples.jsonl:2')
        report = label_replay_periods(iter(()), self.manifest, self.bound())
        self.assertEqual(report['summary']['rows'], 0)
        self.assertIsNone(report['summary']['min_observed_game_time_s'])
        self.assertIsNone(report['summary']['max_observed_game_time_s'])

    def test_streaming_does_not_collect_or_read_ahead(self):
        def source():
            yield next(self.rows([0]))
            raise RuntimeError('reader reached second source row')
        labels = iter_period_labels(source(), self.manifest, self.bound())
        self.assertEqual(next(labels)['period'], 'in_match')
        with self.assertRaisesRegex(RuntimeError, 'second source row'):
            next(labels)

    def native(self, rows=None, complete=True, **options):
        tracker = PhaseTracker(**options)
        for values in ([(1, 0, 5), (2, 10.25, 5), (3, 10.5, 6), (4, 100, 6)]
                       if rows is None else rows):
            tracker.observe(*values)
        self.manifest['phase_evidence'] = tracker.manifest(observation_complete=complete)
        return bind_observed_end_evidence(self.manifest)

    def test_native_end_is_an_observed_interval_without_an_integer_duration(self):
        evidence = self.native()
        self.assertIsInstance(evidence, ObservedEndEvidence)
        report = label_replay_periods(self.rows([-1, 0, 10.24, 10.25, 10.5, 10.51]),
                                      self.manifest, evidence)
        self.assertEqual([label['period'] for label in report['labels']],
                         ['pregame', 'in_match', 'in_match', 'end_boundary_uncertain',
                          'end_boundary_uncertain', 'post_match_tail'])
        self.assertIsNone(report['summary']['duration_s'])
        self.assertIsNone(report['summary']['duration_evidence'])
        self.assertEqual(report['summary']['end_boundary_interval_s'], [10.25, 10.5])
        self.assertEqual(report['summary']['end_evidence']['end_window_tick'], (2, 3))
        self.assertEqual(evidence.phase_evidence_ref, 'manifest.json:phase_evidence')
        self.assertEqual(len(evidence.phase_evidence_sha256), 64)

    def test_native_rejects_historical_six_seven_enum_pair(self):
        wrong = PhaseTracker(in_progress=6, post_game=7)
        wrong.observe(1, 2454, 6)
        wrong.observe(2, 2574, 7)
        self.manifest['phase_evidence'] = wrong.manifest(observation_complete=True)
        self.assertEqual(self.manifest['phase_evidence']['status'], 'observed_interval')
        with self.assertRaisesRegex(ValueError, 'supported_enum_binding_required'):
            bind_observed_end_evidence(self.manifest)

    def test_native_disconnect_is_tail_after_first_real_post_not_a_second_boundary(self):
        evidence = self.native([(1, 0, 5), (2, 2454.76, 5),
                                (3, 2454.80, 6), (4, 2574.80, 7), (5, None, None)])
        self.assertEqual(evidence.end_window_game_time_s, (2454.76, 2454.80))
        self.assertEqual(label_replay_periods(self.rows([2454.78, 2455, 2574.80]),
                                            self.manifest, evidence)['summary']['counts']
                         ['end_boundary_uncertain'], 1)
        self.assertEqual(label_replay_periods(self.rows([2574.80]), self.manifest, evidence)
                         ['labels'][0]['period'], 'post_match_tail')

    def test_default_binding_uses_valid_native_but_explicit_api_inputs_take_priority(self):
        self.native()
        self.assertIsInstance(bind_available_end_evidence(self.manifest), ObservedEndEvidence)
        native = label_replay_periods(self.rows([100]), self.manifest)
        self.assertEqual(native['labels'][0]['period'], 'post_match_tail')
        raw = b'{"match_id":9044645010,"duration":2454}'
        partial = bind_available_end_evidence(self.manifest, raw, None)
        self.assertIsInstance(partial, DurationEvidence)
        self.assertIsNone(partial.duration_s)
        self.assertEqual(label_replay_periods(self.rows([100]), self.manifest, partial)
                         ['labels'][0]['period'], 'end_unknown')
        api = self.bound()
        self.assertEqual(label_replay_periods(self.rows([100]), self.manifest, api)
                         ['labels'][0]['period'], 'in_match')
        with self.assertRaises(ValueError):
            bind_available_end_evidence(self.manifest, b'{"match_id":123,"duration":1}', None)

    def test_native_missing_clock_no_prior_active_reversal_and_truncation_remain_unknown(self):
        sequences = [([], True, {}), ([(1, 10, 6)], True, {}),
                     ([(1, 0, 5), (2, None, 6), (3, 10, 6)], True, {}),
                     ([(1, 0, 5), (2, 10, 6), (3, 11, 5)], True, {}),
                     ([(2, 0, 5), (1, 10, 6)], True, {}),
                     ([(1, 10, 5), (2, 0, 6)], True, {}),
                     ([(1, -1, 4), (2, 0, 5), (3, 1, 6)], True, {'max_changes': 2}),
                     ([(1, 0, 5), (2, 10, 6)], False, {})]
        for rows, complete, options in sequences:
            with self.subTest(rows=rows, complete=complete, options=options):
                evidence = self.native(rows, complete, **options)
                self.assertEqual(evidence.status, 'unavailable')
                self.assertEqual(label_replay_periods(self.rows([2599]), self.manifest, evidence)
                                 ['labels'][0]['period'], 'end_unknown')

    def test_native_schema_and_cross_reference_tampering_is_rejected(self):
        self.native()
        original = copy.deepcopy(self.manifest['phase_evidence'])
        changes = [
            lambda report: report.update(schema_version=True),
            lambda report: report.update(basis='event_timestamp'),
            lambda report: report.update(field='m_nOther'),
            lambda report: report.update(sampling='hero_sample_1Hz'),
            lambda report: report.update(completed_packets=True),
            lambda report: report.update(observation_complete=1),
            lambda report: report.update(state_enums={'in_progress': 60, 'post_game': 70}),
            lambda report: report.update(status='unknown'),
            lambda report: report.update(reasons=['invented']),
            lambda report: report['end_window'].update(game_time_s=[0, 100]),
            lambda report: report['end_window'].update(tick=[True, 3]),
            lambda report: report['end_window'].update(exact_end_verified=True),
            lambda report: report['last_in_progress'].update(completed_packet=1,
                                                              source_ref='completed_packet:1'),
            lambda report: report['first_post_game'].update(game_time_s=11),
            lambda report: report['changes'][0].update(state_raw=True),
            lambda report: report['changes'][1].update(source_ref='completed_packet:999'),
        ]
        for change in changes:
            report = copy.deepcopy(original)
            change(report)
            self.manifest['phase_evidence'] = report
            with self.subTest(report=report), self.assertRaises(ValueError):
                bind_observed_end_evidence(self.manifest)

    def test_native_evidence_remains_bound_to_exact_archive_snapshot(self):
        evidence = self.native()
        other = copy.deepcopy(self.manifest)
        other['phase_evidence']['changes'][0]['game_time_s'] = -1
        with self.assertRaises(ValueError):
            label_replay_periods([], other, evidence)
        with self.assertRaises(ValueError):
            label_replay_periods([], self.manifest,
                                 replace(evidence, end_window_game_time_s=(1, 2)))

    def test_arbitrary_legacy_phase_marker_is_never_native_evidence(self):
        self.manifest['phase_marker'] = {'game_time_s': 10, 'state': 7}
        self.manifest['match_phase_boundary'] = {'post_game_game_clock_s': 10}
        evidence = bind_available_end_evidence(self.manifest)
        self.assertIsNone(evidence.duration_s)
        self.assertEqual(label_replay_periods(self.rows([2599]), self.manifest, evidence)
                         ['labels'][0]['period'], 'end_unknown')

    def test_native_signon_unknowns_and_destroyed_post_game_rules_are_preserved(self):
        evidence = self.native([(0, None, None), (-1, None, None), (-1, None, 4),
                                (1, 0, 5), (2, 10, 6), (3, None, None)])
        self.assertEqual(evidence.status, 'bound')
        self.assertEqual(evidence.end_window_completed_packet, (4, 5))
        self.assertEqual(label_replay_periods(self.rows([100]), self.manifest, evidence)
                         ['labels'][0]['period'], 'post_match_tail')

    def test_available_binding_rejects_non_mapping_manifest_consistently(self):
        for value in (None, [], True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bind_available_end_evidence(value)


if __name__ == '__main__':
    unittest.main()
