import unittest

from dota2_insights.equipment_continuity import EquipmentContinuity


def row(second, status='known', item='blade_mail', *, hero='axe', slot='main_1', candidate=None):
    return {'captured_at_utc': f'2026-10-28T00:{second // 60:02d}:{second % 60:02d}Z',
            'attempt': {'hero': {'hero_id': 'npc_dota_hero_' + hero,
                'slots': [{'slot_id': slot, 'status': status,
                           'item_id': item if status == 'known' else None, 'candidate': candidate}]}}}


class EquipmentContinuityTests(unittest.TestCase):
    def tracker(self, **kwargs):
        return EquipmentContinuity({'blade_mail': {'cost': 2300}, 'blink': {'cost': 2250},
                                    'branches': {'cost': 50}}, **kwargs)

    def feed(self, tracker, *rows):
        for i, value in enumerate(rows, 1):
            tracker.add(value, {'line': i, 'line_sha256': str(i) * 64})
        return tracker.result()

    def test_loss_episode_retains_original_candidates_and_selection_not_gold_difference(self):
        lost = row(2, 'unknown', candidate='manta')
        lost['attempt']['economy_selection'] = {'selected_value': 8000, 'current_observed_value': 5701,
                                               'policy': 'retain', 'selected_captured_at_utc': 'original'}
        result = self.feed(self.tracker(), row(1), lost, row(3, 'unknown'), row(4))
        event = result['episodes'][0]
        self.assertEqual(event['unknown_rows'], 2)
        self.assertEqual(event['known']['evidence']['line'], 1)
        self.assertEqual(event['first_unknown']['candidate'], 'manta')
        self.assertEqual(event['first_unknown']['economy_selection']['selected_value'], 8000)
        self.assertEqual(event['last_unknown']['evidence']['line'], 3)
        self.assertTrue(event['next_known_same_item'])
        self.assertFalse(result['ui_value_verified'])
        self.assertNotIn('missed_gold', event)

    def test_other_hero_does_not_break_or_supply_slot_history(self):
        r = self.feed(self.tracker(), row(1), row(2, 'unknown', hero='lina'), row(3, 'unknown'))
        self.assertEqual(len(r['episodes']), 1)
        self.assertEqual(r['episodes'][0]['hero_id'], 'npc_dota_hero_axe')

    def test_empty_missing_and_duplicate_slots_break_history(self):
        for slots in ([], [{'slot_id': 'main_1', 'status': 'empty'}],
                      [{'slot_id': 'main_1', 'status': 'known', 'item_id': 'blade_mail'}] * 2):
            middle = row(2)
            middle['attempt']['hero']['slots'] = slots
            with self.subTest(slots=slots):
                self.assertFalse(self.feed(self.tracker(), row(1), middle, row(3, 'unknown'))['episodes'])

    def test_gap_equal_time_and_out_of_order_cannot_bridge(self):
        for sec in (0, 1, 182):
            with self.subTest(second=sec):
                self.assertFalse(self.feed(self.tracker(), row(1), row(sec, 'unknown'))['episodes'])

    def test_invalid_time_resets_and_naive_time_not_assumed_utc(self):
        for stamp in (None, 'invalid', '2026-10-28T00:00:02'):
            invalid = row(2, 'unknown')
            invalid['captured_at_utc'] = stamp
            self.assertFalse(self.feed(self.tracker(), row(1), invalid, row(3, 'unknown'))['episodes'])

    def test_total_chain_age_not_extended_forever_by_unknown_rows(self):
        r = self.feed(self.tracker(), row(0), row(100, 'unknown'), row(200, 'unknown'), row(201))
        self.assertEqual(r['episodes'][0]['unknown_rows'], 1)
        self.assertIsNone(r['episodes'][0]['next_known'])

    def test_different_next_item_not_called_recovery(self):
        r = self.feed(self.tracker(), row(1), row(2, 'unknown'), row(3, item='blink'))
        self.assertFalse(r['episodes'][0]['next_known_same_item'])
        self.assertEqual(r['counts']['next_known_different_item'], 1)

    def test_unknown_candidate_or_cheap_known_cannot_seed_large_item(self):
        for first in (row(1, 'unknown', candidate='blade_mail'), row(1, item='branches')):
            self.assertFalse(self.feed(self.tracker(), first, row(2, 'unknown'))['episodes'])
        self.assertFalse(self.feed(EquipmentContinuity(), row(1), row(2, 'unknown'))['catalogue_available'])

    def test_example_cap_keeps_full_episode_counts(self):
        r = self.feed(self.tracker(example_limit=1), row(1), row(2, 'unknown'), row(3), row(4, 'unknown'), row(5))
        self.assertEqual(len(r['episodes']), 1)
        self.assertEqual(r['counts']['known_to_unknown_episodes'], 2)
        self.assertEqual(r['counts']['episodes_not_retained_due_to_limit'], 1)
        self.assertEqual(r['counts']['next_known_same_item'], 2)
