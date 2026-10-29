"""Generated archives only: inventory tools must reject locks before payload reads."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights import replay_economy_review as economy
from tools import prepare_replay_item_audit as audit
from tools import review_replay_last_visible_inventory as visible


class InventoryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.samples = self.root / 'hero_samples.jsonl'
        self.observations = self.root / 'observations.jsonl'
        self.report = self.root / 'report.json'
        self.manifest = self.root / 'manifest.json'
        self.mid = '123456789'
        slots = [{'slot': n, 'status': 'empty'} for n in range(9)]
        slots[0] = {'slot': 0, 'status': 'occupied', 'item_key': 'blink'}
        sample = {'kind': 'hero_sample', 'match_id': self.mid, 'hero_id': 'axe',
                  'game_time_s': 100, 'tick': 3000, 'inventory': slots}
        observed = [{'slot_id': f'main_{n+1}' if n < 6 else f'backpack_{n-5}',
                     'status': 'known' if n == 0 else 'empty',
                     'item_id': 'item_blink' if n == 0 else None} for n in range(9)]
        observation = {'match_id': self.mid,
            'attempt': {'accepted': True, 'hero': {'hero_id': 'axe', 'slots': observed}},
            'clock_context': {'clock_time_s': 100, 'alignment_delta_s': 0}}
        for path, row in ((self.samples, sample), (self.observations, observation)):
            path.write_text(json.dumps(row) + '\n', encoding='utf-8')
        self.report.write_text('not a valid report: must not be read for locked archives', encoding='utf-8')
        self.metadata = {'kind': 'offline_replay_archive', 'match_id': self.mid,
            'parsed_match_id': self.mid, 'source_unchanged_stat': True, 'parse_error': None,
            'truncated_at_tick': None, 'export_complete': True, 'stop_reason': 'replay_eof',
            'files': {'hero_samples.jsonl': {'bytes': self.samples.stat().st_size,
                'sha256': hashlib.sha256(self.samples.read_bytes()).hexdigest()}}}
        locks = json.loads((Path(__file__).resolve().parents[1] / 'data/validation-locks.json').read_text(encoding='utf-8'))
        self.locked = locks['heldout_matches'][0]
        self.save_manifest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.metadata), encoding='utf-8')

    def reject_payload_reads(self):
        original = Path.open
        forbidden = {self.samples, self.observations, self.report}
        def guarded(path, *args, **kwargs):
            if path in forbidden:
                raise AssertionError('payload read before admission')
            return original(path, *args, **kwargs)
        return patch.object(Path, 'open', guarded)

    def test_valid_archive_still_compares_all_nine_slots(self):
        result = economy.compare(self.observations, self.samples)
        self.assertEqual(result['counts']['same_item_id'], 1)
        self.assertEqual(result['counts']['same_empty'], 8)
        self.assertEqual(result['inventory_item_multiset_counts'], {'same_item_multiset': 1})

    def test_mismatched_parsed_identity_rejected_before_payload(self):
        self.metadata['parsed_match_id'] = '987654321'
        self.save_manifest()
        with self.reject_payload_reads(), self.assertRaisesRegex(ValueError, 'successful_source_bound'):
            economy.compare(self.observations, self.samples)

    def test_missing_parsed_identity_rejected_before_payload(self):
        del self.metadata['parsed_match_id']
        self.save_manifest()
        with self.reject_payload_reads(), self.assertRaisesRegex(ValueError, 'successful_source_bound'):
            economy.compare(self.observations, self.samples)

    def locked_check(self, operation, field):
        self.metadata[field] = self.locked
        self.save_manifest()
        with self.reject_payload_reads(), patch.object(visible, 'game_guard'), self.assertRaisesRegex(ValueError, '独立封存比赛'):
            operation()

    def test_economy_declared_lock_precedes_payload(self):
        self.locked_check(lambda: economy.compare(self.observations, self.samples), 'match_id')

    def test_economy_parsed_lock_precedes_payload(self):
        self.locked_check(lambda: economy.compare(self.observations, self.samples), 'parsed_match_id')

    def test_audit_both_identity_locks_precede_report_and_images(self):
        for field in ('match_id', 'parsed_match_id'):
            with self.subTest(field=field):
                self.metadata.update(match_id=self.mid, parsed_match_id=self.mid)
                self.locked_check(lambda: audit.prepare(self.report, self.observations, self.root), field)

    def test_last_visible_both_identity_locks_precede_review(self):
        for field in ('match_id', 'parsed_match_id'):
            with self.subTest(field=field):
                self.metadata.update(match_id=self.mid, parsed_match_id=self.mid)
                self.locked_check(lambda: visible.analyze(self.report, [self.root]), field)

    def test_last_visible_does_not_certify_missing_or_unknown_slots(self):
        observed = [{'slot_id': f'main_{n+1}' if n < 6 else f'backpack_{n-5}',
                     'status': 'empty'} for n in range(9)]
        inventory = [{'slot': n, 'status': 'empty'} for n in range(9)]
        self.assertTrue(visible.compare_slots(observed, {'inventory': inventory})['all_9_slot_item_multisets_equal'])
        for original, reference in (([], inventory), (observed[:8], inventory),
                (observed, inventory[:8]), (observed, inventory[:8] + [{'slot': 8, 'status': 'unknown'}]),
                (observed[:8] + [observed[0]], inventory), (observed, inventory + [inventory[0]])):
            with self.subTest(original_slots=len(original), reference_slots=len(reference)):
                self.assertIsNone(visible.compare_slots(original, {'inventory': reference})['all_9_slot_item_multisets_equal'])

    def rewrite_rows(self, mutate_observation=None, mutate_sample=None):
        for path, mutate in ((self.observations, mutate_observation), (self.samples, mutate_sample)):
            if mutate:
                row = json.loads(path.read_text(encoding='utf-8'))
                mutate(row)
                path.write_text(json.dumps(row) + '\n', encoding='utf-8')
        self.metadata['files']['hero_samples.jsonl'].update(bytes=self.samples.stat().st_size,
            sha256=hashlib.sha256(self.samples.read_bytes()).hexdigest())
        self.save_manifest()

    def test_missing_item_ids_cannot_agree(self):
        self.rewrite_rows(lambda r: r['attempt']['hero']['slots'][0].update(item_id=None),
                          lambda r: r['inventory'][0].update(item_key=None))
        result = economy.compare(self.observations, self.samples)
        self.assertEqual(result['counts'].get('same_item_id', 0), 0)
        self.assertEqual(result['counts']['recognition_unknown'], 1)
        self.assertEqual(result['inventory_item_multiset_counts'], {'incomplete_slots_not_compared': 1})

    def test_duplicate_replay_slot_cannot_be_overwritten_into_agreement(self):
        self.rewrite_rows(mutate_sample=lambda r: r['inventory'].insert(0,
            {'slot': 0, 'status': 'occupied', 'item_key': 'rapier'}))
        result = economy.compare(self.observations, self.samples)
        self.assertEqual(result['counts'].get('same_item_id', 0), 0)
        self.assertEqual(result['counts']['replay_duplicate_slot'], 1)
        self.assertEqual(result['inventory_item_multiset_counts'], {'incomplete_slots_not_compared': 1})

    def test_duplicate_observation_slot_is_not_counted_twice(self):
        self.rewrite_rows(mutate_observation=lambda r: r['attempt']['hero']['slots'].append(
            {'slot_id': 'main_1', 'status': 'known', 'item_id': 'blink'}))
        result = economy.compare(self.observations, self.samples)
        self.assertEqual(result['counts'].get('same_item_id', 0), 0)
        self.assertEqual(result['counts']['recognition_duplicate_slot'], 2)
        self.assertEqual(result['inventory_item_multiset_counts'], {'incomplete_slots_not_compared': 1})

    def test_reference_occupied_without_id_is_unknown(self):
        self.rewrite_rows(mutate_sample=lambda r: r['inventory'][0].pop('item_key'))
        result = economy.compare(self.observations, self.samples)
        self.assertEqual(result['counts']['replay_slot_unknown'], 1)
        self.assertEqual(result['inventory_item_multiset_counts'], {'incomplete_slots_not_compared': 1})

    def test_contradictory_empty_slot_does_not_agree(self):
        self.rewrite_rows(mutate_sample=lambda r: r['inventory'][1].update(item_key='rapier'))
        result = economy.compare(self.observations, self.samples)
        self.assertEqual(result['counts']['same_empty'], 7)
        self.assertEqual(result['counts']['replay_slot_unknown'], 1)

    def test_prefixed_item_id_is_normalized_for_last_visible(self):
        result = visible.compare_slots([{'slot_id': 'main_1', 'status': 'known', 'item_id': 'item_blink'}],
            {'inventory': [{'slot': 0, 'status': 'occupied', 'item_key': 'blink'}]})
        self.assertEqual(result['slot_counts'], {'same': 1})

    def test_last_visible_missing_ids_never_certify_same(self):
        result = visible.compare_slots([{'slot_id': 'main_1', 'status': 'known', 'item_id': None}],
            {'inventory': [{'slot': 0, 'status': 'occupied', 'item_key': None}]})
        self.assertEqual(result['slot_counts'].get('same', 0), 0)
        self.assertEqual(result['observed_unknown_slots'], 1)
        self.assertIsNone(result['all_9_slot_item_multisets_equal'])

    def test_bad_sample_checksum_still_rejected(self):
        self.metadata['files']['hero_samples.jsonl']['sha256'] = '0'*64
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'samples_do_not_match_manifest'):
            economy.compare(self.observations, self.samples)


if __name__ == '__main__':
    unittest.main()
