"""Named GSI facts from generated journals; no device, game or map truth claims."""
import copy
import unittest

from dota2_insights.gsi_evidence_census import GsiEvidenceCensus


def row(sequence=1, *, stunned=False):
    return {'sequence': sequence, 'match_id': '12345', 'game_session_id': 'game_'+'a'*32,
        'record_session_id': 'run_'+'b'*32, 'provider_timestamp': sequence,
        'received_at_utc': f'1970-01-01T00:00:{sequence:02d}Z',
        'provider_at_utc': f'1970-01-01T00:00:{sequence:02d}Z', 'clock_time_s': sequence,
        'self_economy': {'hero_id': 'npc_dota_hero_axe'},
        'self_status': {'schema_version': 1, 'hero_id': 'npc_dota_hero_axe', 'flags': {'stunned': stunned}},
        'gsi_probe': {'schema_version': 1, 'activity': 'playing',
            'own_position': {'status': 'reported', 'hero_id': 'npc_dota_hero_axe', 'xpos': 0, 'ypos': 0,
                             'basis': 'current_local_gsi_not_screen_position'},
            'map_daylight': {'daytime': True, 'paused': False},
            'objectives': {'map_roshan_state_type': '-', 'nodes': {'roshan': {'shape': 'missing'}}, 'events': []}}}


class GsiCensusTests(unittest.TestCase):
    def test_duplicate_provider_time_is_not_source_identity(self):
        c = GsiEvidenceCensus()
        a = row(); b = row(2)
        b['provider_at_utc'] = a['provider_at_utc']
        c.add(a); c.add(b)
        evidence = c.result()['source_identity_evidence']
        self.assertEqual(evidence['duplicated_provider_times'], 1)
        self.assertEqual(evidence['provider_times_with_distinct_position_or_clock'], 1)
        self.assertEqual(evidence['missing_source_id_rows'], 2)
        self.assertFalse(evidence['timestamp_is_unique_identity'])

    def test_source_id_missing_invalid_and_duplicate_are_distinct(self):
        c = GsiEvidenceCensus()
        for token in (None, True, 'gsi_'+'a'*32, 'gsi_'+'a'*32):
            c.add({**row(), 'gsi_source_id': token})
        evidence = c.result()['source_identity_evidence']
        self.assertEqual(evidence['source_id_rows'], 2)
        self.assertEqual(evidence['duplicate_source_ids'], 1)
        self.assertEqual(evidence['invalid_source_id_rows'], 1)
        self.assertEqual(evidence['missing_source_id_rows'], 1)

    def test_zero_coordinates_retained_with_source_clocks(self):
        c = GsiEvidenceCensus(collect_positions=True)
        c.add(row(), line=7)
        result = c.result()
        self.assertEqual(result['valid_local_position_rows'], 1)
        self.assertEqual(c.positions[0]['xpos'], 0)
        self.assertEqual(c.positions[0]['line'], 7)
        self.assertEqual(c.positions[0]['provider_timestamp'], 1)
        self.assertFalse(result['map_pixel_mapping_verified'])

    def test_partial_axes_bool_and_foreign_hero_not_exported(self):
        for patch in ({'xpos': None}, {'ypos': True}, {'xpos': float('inf')}, {'hero_id': 'npc_dota_hero_lion'}):
            r = row()
            r['gsi_probe']['own_position'].update(patch)
            c = GsiEvidenceCensus(collect_positions=True)
            c.add(r)
            self.assertEqual(c.result()['valid_local_position_rows'], 0)
            self.assertEqual(len(c.positions), 0)
            self.assertEqual(sum(c.result()['position_rejected'].values()), 1)

    def test_flags_keep_true_false_missing_and_invalid_separate(self):
        c = GsiEvidenceCensus()
        for i, value in enumerate((True, False, None, 0), 1):
            c.add(row(i, stunned=value))
        self.assertEqual(c.result()['hero_flags']['stunned'], {'true': 1, 'false': 1, 'not_reported': 1, 'invalid': 1})
        self.assertEqual(c.result()['hero_flags']['teleporting'], {'not_reported': 4})
        self.assertEqual(c.result()['objective_node_shapes']['roshan'], {'missing': 4})
        self.assertFalse(c.result()['missing_fields_prove_game_unsupported'])

    def test_consecutive_sampled_edge_not_exact_buff_boundary(self):
        c = GsiEvidenceCensus()
        c.add(row(1, stunned=False)); c.add(row(2, stunned=True))
        result = c.result()
        self.assertEqual(result['sampled_transition_counts']['hero.stunned'], 1)
        event = result['sampled_transition_examples'][0]
        self.assertEqual((event['previous_sequence'], event['sequence']), (1, 2))
        self.assertIn('not_exact_effect_boundary', event['basis'])

    def test_gap_missing_or_cross_session_never_bridge_boolean_edge(self):
        for change in ('time', 'missing', 'session'):
            c = GsiEvidenceCensus()
            c.add(row(1))
            if change == 'missing':
                c.add({'sequence': 2})
                last = row(3, stunned=True)
            else:
                last = row(9 if change == 'time' else 2, stunned=True)
                if change == 'session': last['game_session_id'] = 'game_'+'c'*32
            c.add(last)
            self.assertEqual(c.result()['sampled_transition_counts'], {})

    def test_unknown_probe_schema_does_not_claim_support(self):
        c = GsiEvidenceCensus()
        r = row(); r['gsi_probe']['schema_version'] = 2
        c.add(r)
        self.assertEqual(c.result()['unsupported_probe_rows'], 1)
        self.assertEqual(c.result()['valid_local_position_rows'], 0)
        self.assertEqual(c.result()['hero_flags']['stunned'], {})

    def test_repeated_objective_event_is_not_counted_as_repeated_kill(self):
        c = GsiEvidenceCensus()
        for i in range(1, 5):
            r = row(i)
            r['gsi_probe']['objectives']['events'] = [{'type': 'roshan_killed', 'game_time': 1000}]
            c.add(r)
        result = c.result()
        self.assertEqual(result['objective_event_row_mentions'], {'roshan_killed': 4})
        self.assertEqual(result['objective_distinct_timed_event_keys'], {'roshan_killed': 1})
        self.assertEqual(result['objective_event_examples'][0]['mentions'], 4)
        self.assertEqual(result['objective_event_examples'][0]['last_sequence'], 4)

    def test_untimed_objective_mentions_do_not_invent_distinct_events(self):
        c = GsiEvidenceCensus()
        r = row()
        r['gsi_probe']['objectives']['events'] = [{'type': 'roshan_killed'}]
        c.add(r)
        self.assertEqual(c.result()['objective_untimed_event_mentions'], {'roshan_killed': 1})
        self.assertEqual(c.result()['objective_distinct_timed_event_keys'], {})

    def test_event_game_time_is_not_assumed_match_clock(self):
        c = GsiEvidenceCensus()
        r = row(); r['clock_time_s'] = 1732; r['map_context'] = {'game_time_s': 1940}
        r['gsi_probe']['objectives']['events'] = [{'type': 'roshan_killed', 'game_time': 1940}]
        c.add(r)
        event = c.result()['objective_event_examples'][0]
        self.assertEqual(event['reported_game_time'], 1940)
        self.assertEqual(event['first_observed_match_clock_s'], 1732)
        self.assertEqual(event['first_observed_map_game_time_s'], 1940)

    def test_position_export_cap_is_explicit_and_does_not_cap_aggregate(self):
        c = GsiEvidenceCensus(collect_positions=True, position_limit=1)
        c.add(row(1)); c.add(row(2))
        self.assertEqual(c.result()['valid_local_position_rows'], 2)
        self.assertEqual(c.result()['exported_position_rows'], 1)
        self.assertTrue(c.result()['position_export_truncated'])

    def test_dead_position_preserved_but_not_counted_as_living_marker_evidence(self):
        c = GsiEvidenceCensus(collect_positions=True)
        r = row(); r['self_status']['flags']['alive'] = False
        c.add(r)
        self.assertEqual(c.result()['valid_local_position_rows'], 1)
        self.assertEqual(c.result()['fresh_living_local_position_rows'], 0)
        self.assertIs(c.positions[0]['alive'], False)
        r = row(2); r['self_status']['flags']['alive'] = True
        r['provider_at_utc'] = '1969-12-31T23:59:00Z'
        c.add(r)
        self.assertEqual(c.result()['living_local_position_rows'], 1)
        self.assertEqual(c.result()['fresh_living_local_position_rows'], 0)
        self.assertGreater(c.positions[-1]['source_age_s'], 5)

    def test_native_export_is_reverified_and_csv_keeps_explicit_identity(self):
        import csv
        import hashlib
        import json
        from datetime import datetime
        from pathlib import Path
        from unittest.mock import patch
        from test_match_journal_index import NativeIndexTests
        from tools.review_gsi_evidence import run
        f = NativeIndexTests(); f.setUp(); self.addCleanup(f.doCleanups)
        # Existing generated native journal uses another hero; adapt named
        # probe and status to its actual explicit identity without relabeling it.
        for payload in f.fixture.rows:
            extra = row(payload['sequence'])
            payload.update(gsi_probe=extra['gsi_probe'], self_status=extra['self_status'])
            payload['provider_timestamp'] = int(datetime.fromisoformat(payload['provider_at_utc']).timestamp())
        f.fixture.publish()
        result = run(f.project, roots=f.roots, latest=2)
        output = Path(result['output'])
        doc = json.loads((output/'report.json').read_text(encoding='utf-8'))
        entry = doc['reports'][0]
        path = output / entry['positions_file']
        with path.open(encoding='utf-8-sig', newline='') as stream:
            positions = list(csv.DictReader(stream))
        self.assertEqual(len(positions), 2)
        self.assertEqual(positions[0]['match_id'], f.fixture.mid)
        self.assertEqual(positions[0]['game_session_id'], f.fixture.game)
        self.assertEqual(positions[0]['xpos'], '0')
        self.assertEqual(entry['positions_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(entry['source_payload_sha256'], f.fixture.manifest['payload']['sha256'])
        self.assertTrue(entry['source_verified_before_and_after'])
        from dota2_insights.match_journal_index import read_index
        old = read_index(f.project/'diagnostics/native-match-handoff/index.json')
        with (f.fixture.folder/'samples.jsonl').open('ab') as stream:
            stream.write(b'{}\n')
        with patch('dota2_insights.match_journal_index.sync', return_value=old):
            with self.assertRaises(ValueError):
                run(f.project, roots=f.roots)
