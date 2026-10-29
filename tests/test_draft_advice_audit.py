from copy import deepcopy
import unittest

from dota2_insights.draft_advice_audit import audit, render_markdown
from dota2_insights.draft_diagnostics import DraftDiagnostics


class DraftAdviceAuditTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'phase': 'selection', 'own_side': 'left', 'slots': [
            {'slot': 'left1', 'hero_id': 'axe', 'public': True},
            {'slot': 'right1', 'hero_id': 'lion', 'public': True},
            {'slot': 'right2', 'hero_id': 'pudge', 'public': False},
            {'slot': 'left2', 'hero_id': 'lina', 'public': True, 'conflict': True}]}
        self.advice = {'stage': 'round_one', 'cache_status': 'stale',
            'capability_rules': {'status': 'version_unverified', 'applies_to_game_version': None},
            'candidates': [{'hero_id': 'tiny', 'baseline_samples': 4000, 'relations': [
                {'kind': 'with', 'other_id': 'axe', 'match_count': 250, 'delta': 0.0},
                {'kind': 'against', 'other_id': 'lion', 'match_count': 20, 'delta': -.1}]}]}

    def test_only_public_nonconflicted_roster_and_zero_delta_is_valid(self):
        r = audit(self.snapshot, self.advice)
        c = r['candidates'][0]
        self.assertEqual(c['expected_relations'], 2)
        self.assertEqual(len(c['covered_relations']), 2)
        self.assertEqual(c['small_sample_relations'][0]['other_id'], 'lion')
        self.assertEqual(r['cache_status_recorded'], 'stale')
        self.assertIsNone(r['rules_game_version_recorded'])
        self.assertFalse(r['quality_verified'])

    def test_duplicate_invalid_or_missing_relationship_never_counted_covered(self):
        for relation in (None, {'kind': 'against', 'other_id': 'lion', 'match_count': True, 'delta': 0},
                         {'kind': 'against', 'other_id': 'lion', 'match_count': 20, 'delta': float('nan')}):
            advice = deepcopy(self.advice)
            advice['candidates'][0]['relations'] = [advice['candidates'][0]['relations'][0]]
            if relation is not None:
                advice['candidates'][0]['relations'].append(relation)
            self.assertEqual(len(audit(self.snapshot, advice)['candidates'][0]['missing_or_invalid_relations']), 1)
        self.advice['candidates'][0]['relations'].append(self.advice['candidates'][0]['relations'][0])
        c = audit(self.snapshot, self.advice)['candidates'][0]
        self.assertEqual(len(c['covered_relations']), 1)
        self.assertEqual(c['missing_or_invalid_relations'][0]['reason'], 'duplicate_relation')

    def test_unknown_side_or_duplicate_hero_keeps_denominator_unknown(self):
        self.snapshot['own_side'] = None
        self.assertIsNone(audit(self.snapshot, self.advice)['candidates'][0]['expected_relations'])
        self.snapshot['own_side'] = 'left'
        self.snapshot['slots'].append({'slot': 'right3', 'hero_id': 'axe', 'public': True})
        self.assertFalse(audit(self.snapshot, self.advice)['relation_scope_available'])

    def test_wrong_direction_and_unexpected_opponents_cannot_fill_coverage(self):
        self.advice['candidates'][0]['relations'][0]['kind'] = 'against'
        r = audit(self.snapshot, self.advice)['candidates'][0]
        self.assertEqual(len(r['covered_relations']), 1)
        self.assertEqual(r['unexpected_relation_rows'], 1)

    def test_each_event_uses_its_own_roster_without_final_array_backfill(self):
        d = DraftDiagnostics()
        row = {'snapshot': deepcopy(self.snapshot), 'advice': deepcopy(self.advice)}
        d.add('events.jsonl', 1, row, {'line_sha256': 'a'*64})
        row['snapshot']['slots'][2]['public'] = True
        d.add('events.jsonl', 2, row, {'line_sha256': 'b'*64})
        events = d.result()['events']
        self.assertEqual(events[0]['advice_data_health']['candidates'][0]['expected_relations'], 2)
        self.assertEqual(events[1]['advice_data_health']['candidates'][0]['expected_relations'], 3)
        text = render_markdown([{'session_id': 's', 'diagnostics': d.result()}])
        self.assertIn('stale', text)
        self.assertIn('version_unverified', text)
        self.assertEqual(text.count('### s / round_one'), 1)

    def test_missing_metadata_stays_unknown_and_candidate_audit_is_bounded(self):
        self.advice.pop('cache_status')
        self.advice['candidates'] *= 5
        r = audit(self.snapshot, self.advice)
        self.assertEqual(r['candidate_count'], 5)
        self.assertEqual(len(r['candidates']), 3)
        self.assertIsNone(r['cache_status_recorded'])
