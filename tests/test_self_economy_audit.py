"""Independent arithmetic/provenance cases; no helper imports or game data."""
from copy import deepcopy
import unittest

from dota2_insights.self_economy_audit import SelfEconomyAudit


def row(*, amount=3000, status='partial', second=0):
    return {'match_id': '12345', 'game_session_id': 'game_' + 'a' * 32,
            'provider_timestamp': 100 + second, 'received_at_utc': f'2026-10-26T00:00:{second:02d}Z',
            'provider_at_utc': f'2026-10-26T00:00:{second:02d}Z',
            'self_economy': {'hero_id': 'npc_dota_hero_axe', 'amount': amount, 'gpm': 300,
                             'cash_value': 1000, 'item_value': 2000, 'upgrade_value': 0,
                             'economy_status': status, 'missing_reasons': ['unknown_items']},
            'display_economy': {'amount': amount, 'raw_amount': amount, 'valuation_provider_timestamp': 100 + second}}


def bear_row():
    value = row()
    value['self_economy']['hero_id'] = 'npc_dota_hero_lone_druid'
    own_stats_session = 'game_' + 'c' * 32
    value['display_economy'].update(amount=5500, amount_source='self_gsi_plus_bear',
        self_stats_game_session_id=own_stats_session,
        bear_supplement={'source': 'owner_confirmed_bear_hud', 'match_id': '12345',
            'game_session_id': own_stats_session, 'unit_session_id': 'd' * 32,
            'frame_sha256': 'e' * 64, 'owner_basis': 'manual_match_binding', 'owner_binding_id': 'f' * 32,
            'observed_at_utc': '2026-10-25T23:59:00Z', 'computed_at_utc': '2026-10-25T23:59:01Z',
            'main_value': 2000, 'backpack_value': 500, 'upgrade_value': 0, 'value': 2500,
            'known_count': 6, 'slot_count': 9})
    return value


class SelfEconomyAuditTests(unittest.TestCase):
    def audit(self, *rows):
        audit = SelfEconomyAudit()
        for index, value in enumerate(rows):
            audit.add(value, {'line': index + 1, 'line_sha256': str(index) * 64})
        return audit.result()

    def test_reported_total_not_compared_with_partial_components(self):
        result = self.audit(row(amount=9999, status='reported'))
        self.assertEqual(result['component_checks']['comparable_rows'], 0)
        self.assertEqual(result['display_checks']['normal_comparable_rows'], 1)
        self.assertEqual(result['display_checks']['amount_mismatches'], 0)
        self.assertEqual(result['last_reported_gsi']['amount'], 9999)

    def test_same_packet_raw_or_display_disagreement_is_flagged(self):
        for key in ('raw_amount', 'amount'):
            value = row()
            value['display_economy'][key] = 2999
            with self.subTest(key=key):
                result = self.audit(value)
                self.assertEqual(result['display_checks']['amount_mismatches'], 1)
                self.assertEqual(result['examples'][0]['evidence']['line'], 1)

    def test_different_packet_or_missing_clock_never_compared(self):
        value = row()
        value['display_economy'].update(valuation_provider_timestamp=99, amount=1)
        result = self.audit(value)
        self.assertEqual(result['display_checks']['uncomparable_reasons'], {'valuation_not_same_packet': 1})
        self.assertEqual(result['display_checks']['amount_mismatches'], 0)
        value['display_economy'].pop('valuation_provider_timestamp')
        self.assertEqual(self.audit(value)['display_checks']['uncomparable_reasons'], {'valuation_clock_missing': 1})

    def test_bear_sum_keeps_two_independent_sessions_and_all_provenance(self):
        value = bear_row()
        before = deepcopy(value)
        result = self.audit(value)
        self.assertEqual(value, before)
        self.assertEqual(result['display_checks']['bear_comparable_rows'], 1)
        self.assertEqual(result['display_checks']['amount_mismatches'], 0)
        self.assertEqual(result['display_checks']['bear_provenance_issues'], 0)
        self.assertFalse(result['display_pixels_verified'])

    def test_reported_net_worth_never_gets_bear_added_again(self):
        value = bear_row()
        value['self_economy']['economy_status'] = 'reported'
        result = self.audit(value)
        self.assertEqual(result['display_checks']['bear_provenance_issues'], 1)
        self.assertEqual(result['display_checks']['bear_comparable_rows'], 0)

    def test_foreign_incomplete_or_inconsistent_bear_provenance_not_trusted(self):
        changes = [('match_id', '12346'), ('game_session_id', 'game_' + 'b' * 32),
                   ('owner_binding_id', None), ('frame_sha256', None), ('value', 2600),
                   ('known_count', 10), ('main_value', True), ('upgrade_value', 1),
                   ('computed_at_utc', '2026-10-25T23:58:59Z')]
        for key, replacement in changes:
            value = bear_row()
            value['display_economy']['bear_supplement'][key] = replacement
            with self.subTest(key=key):
                self.assertEqual(self.audit(value)['display_checks']['bear_provenance_issues'], 1)

    def test_components_unknown_or_bool_not_zero_and_known_sum_mismatch_flagged(self):
        value = row(amount=2999)
        self.assertEqual(self.audit(value)['component_checks']['mismatches'], 1)
        for replacement in (None, True):
            value['self_economy']['cash_value'] = replacement
            self.assertEqual(self.audit(value)['component_checks']['comparable_rows'], 0)

    def test_reported_decrease_preserved_with_no_smoothing_or_cross_status_join(self):
        values = [row(status='reported'), row(amount=2000, status='reported', second=2)]
        result = self.audit(*values)
        self.assertEqual(result['large_decreases_within_five_seconds'], 1)
        self.assertEqual(result['last_reported_gsi']['amount'], 2000)
        self.assertEqual(result['display_checks']['amount_mismatches'], 0)
        values[-1]['self_economy']['economy_status'] = 'partial'
        self.assertEqual(self.audit(*values)['large_decreases_within_five_seconds'], 0)

    def test_different_match_or_long_gap_not_a_single_short_drop(self):
        for key, replacement in (('match_id', '12346'), ('game_session_id', 'game_' + 'b' * 32),
                                 ('received_at_utc', '2026-10-26T00:00:06Z')):
            value = row(amount=2000)
            value[key] = replacement
            with self.subTest(key=key):
                self.assertEqual(self.audit(row(), value)['large_decreases_within_five_seconds'], 0)

    def test_examples_bounded_missing_display_not_a_success(self):
        value = row()
        value['display_economy']['amount'] = 1
        result = self.audit(*[value for _ in range(40)])
        self.assertEqual(result['display_checks']['amount_mismatches'], 40)
        self.assertEqual(len(result['examples']), 20)
        value.pop('display_economy')
        self.assertEqual(self.audit(value)['display_checks']['uncomparable_reasons'], {'display_not_recorded': 1})

    def test_boolean_amount_or_clock_not_a_number(self):
        for key in ('amount', 'valuation_provider_timestamp', 'raw_amount'):
            value = row()
            value['display_economy'][key] = True
            with self.subTest(key=key):
                self.assertEqual(self.audit(value)['display_checks']['normal_comparable_rows'], 0)

    def test_many_normal_decreases_do_not_hide_later_display_bug_evidence(self):
        values = [row(amount=100000 - 1000 * i, status='reported', second=i) for i in range(40)]
        bad = row(amount=60000, status='reported', second=40)
        bad['display_economy']['amount'] = 1
        result = self.audit(*values, bad)
        self.assertEqual(result['large_decreases_within_five_seconds'], 40)
        self.assertEqual(result['display_checks']['amount_mismatches'], 1)
        self.assertEqual(len(result['examples']), 20)
        self.assertTrue(any(example['kind'] == 'same_packet_display_mismatch' for example in result['examples']))
