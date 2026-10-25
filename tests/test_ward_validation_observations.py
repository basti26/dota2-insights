import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from dota2_insights.ward_observation_check import (
    MAP_DISTANCE_MAX, WORLD_DISTANCE_MAX, compare_distance, compare_interval,
    compare_lifetime, compare_map_position, compare_world_position, preflight, template,
)
from dota2_insights.ward_validation_package import sha


class WardObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        (self.package / 'review').mkdir(parents=True)
        (self.package / 'evidence').mkdir()
        blind = json.dumps([{'sample_id': 'synthetic-0', 'record_index': 0}]).encode()
        (self.package / 'review/blind.json').write_bytes(blind)
        freeze = b'{}'
        (self.package / 'evidence/freeze.json').write_bytes(freeze)
        data = {'status': 'samples_frozen_pending_visual_review', 'role': 'development',
                'source_freeze_sha256': sha(freeze), 'match_id': '9050000001',
                'files': {'review/blind.json': sha(blind)}}
        body = json.dumps(data).encode()
        (self.package / 'package.json').write_bytes(body)
        (self.package / 'package.sha256').write_text(sha(body), encoding='ascii')
        self.observed = template(self.package)
        self.path = self.root / 'observations.json'

    def check(self):
        self.path.write_text(json.dumps(self.observed), encoding='utf-8')
        return preflight(self.package, self.path)

    def frames(self):
        # Synthetic image files test decoding/integrity only, never game truth.
        for index, color in enumerate(('black', 'white')):
            name = f'frame-{index}.png'
            Image.new('RGB', (640, 360), color).save(self.root / name)
            self.observed['evidence'].append({'evidence_id': str(index), 'file': name,
                'sha256': sha((self.root / name).read_bytes()), 'match_id': '9050000001',
                'clock_basis': 'client_game_clock', 'game_time_s': index, 'clock_precision_s': 1,
                'captured_at_utc': '2026-10-23T00:00:00Z', 'event_role': 'before' if index == 0 else 'after'})
        self.observed.update(observer='synthetic_fixture', reviewed_at_utc='2026-10-23T00:01:00Z')

    def test_blank_observations_stay_pending(self):
        result = self.check()
        self.assertTrue(all(v == 'pending' for v in result['samples'][0]['fields'].values()))
        self.assertFalse(result['independent_validation_passed'])
        self.assertEqual(result['frame_count'], 0)

    def test_values_without_images_do_not_become_comparable(self):
        self.observed['samples'][0].update(observed_type='observer', observed_team=2)
        self.assertEqual(self.check()['samples'][0]['fields']['ward_type'], 'pending')

    def test_brackets_need_two_different_images_and_clock_precision(self):
        self.frames()
        row = self.observed['samples'][0]
        row.update(placed_window_s=[0, 2], field_evidence={'placed_time': ['0', '1']})
        self.assertEqual(self.check()['samples'][0]['fields']['placed_time'], 'ready_for_comparison')
        self.observed['evidence'][1].update(file='frame-0.png', sha256=self.observed['evidence'][0]['sha256'])
        self.assertEqual(self.check()['samples'][0]['fields']['placed_time'], 'pending')

    def test_wrong_match_hash_and_http_receive_clock_rejected(self):
        self.frames()
        frame = self.observed['evidence'][0]
        for key, value in (('match_id', '9050000002'), ('sha256', 'a' * 64), ('clock_basis', 'http_received_at')):
            original = frame[key]
            frame[key] = value
            with self.assertRaises(ValueError):
                self.check()
            frame[key] = original

    def test_changed_sample_identity_rejected(self):
        self.observed['samples'][0]['record_index'] = 99
        with self.assertRaisesRegex(ValueError, 'record_index'):
            self.check()

    def test_unjudgeable_requires_reviewed_frame(self):
        row = self.observed['samples'][0]
        row['unjudgeable_fields'] = {'world_position': 'client does not display coordinates'}
        self.assertEqual(self.check()['samples'][0]['fields']['world_position'], 'pending')
        self.frames()
        row['field_evidence'] = {'world_position': ['0']}
        self.assertEqual(self.check()['samples'][0]['fields']['world_position'], 'unjudgeable')

    def test_position_requires_independent_basis(self):
        self.frames()
        row = self.observed['samples'][0]
        row.update(world_position=[100, 200], map_position=[.2, .3],
                   field_evidence={'world_position': ['0'], 'map_position': ['0']})
        self.assertEqual(self.check()['samples'][0]['fields']['world_position'], 'pending')
        self.assertEqual(self.check()['samples'][0]['fields']['map_position'], 'pending')

    def test_fixed_numeric_boundaries_and_lifetime_uncertainty(self):
        self.assertEqual(compare_interval(-60, [-61, -59]), 'pass')
        self.assertEqual(compare_interval(-58.9, [-61, -59]), 'mismatch')
        self.assertEqual(compare_lifetime(102, [0, 2], [100, 102]), 'pass')
        self.assertEqual(compare_lifetime(102.1, [0, 2], [100, 102]), 'mismatch')
        for limit in (WORLD_DISTANCE_MAX, MAP_DISTANCE_MAX):
            self.assertEqual(compare_distance([limit, 0], [0, 0], tolerance=limit), 'pass')
            self.assertEqual(compare_distance([limit * 1.001, 0], [0, 0], tolerance=limit), 'mismatch')
        with self.assertRaises(ValueError):
            compare_interval(0, [0, 2.01])
        with self.assertRaises(ValueError):
            compare_interval(0, [False, 1])
        self.assertEqual(compare_world_position([64, 0], [0, 0]), 'pass')
        self.assertEqual(compare_map_position([.3, .3], [.29, .3]), 'pass')
        with self.assertRaises(ValueError):
            compare_map_position([0, 0], [-.01, 0])
