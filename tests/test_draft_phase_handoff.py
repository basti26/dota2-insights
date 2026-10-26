"""Independently generated BP prefix contracts; no real session or UI."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.draft_phase_handoff import STREAMS, open_rows, review, verify


class DraftPhaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sid = 'a' * 32
        self.folder = self.root / 'drafts' / ('session_' + self.sid)
        self.folder.mkdir(parents=True)
        snapshot = {'session_id': self.sid, 'match_id': '12345', 'phase': 'selection'}
        self.event = {'snapshot': snapshot, 'advice': {'candidates': [{'hero_id': 'npc_dota_hero_axe'}]}}
        self.receipt = {'session_id': self.sid, 'view_input': self.event}
        self.manifest = {'schema_version': 1, 'kind': 'foresight_draft_phase_prefix', 'session_id': self.sid,
            'match_id_at_cutoff': '12345', 'ending_phase': 'strategy',
            'cutoff_recorded_at_utc': '2026-10-26T00:00:10Z', 'last_observed_at_utc': '2026-10-26T00:00:09Z',
            'source_error_present': False, 'source_limit_reached': False,
            'scope': 'processed_logs_through_first_recorded_ending_phase',
            'later_tail': 'may_append_not_included_in_this_prefix', 'images': 'not_included_or_verified',
            'meaning': 'recorded_phase_and_log_prefix_not_complete_draft_or_display_quality_proof'}
        self.publish()

    def write_manifest(self):
        raw = json.dumps(self.manifest).encode()
        (self.folder / 'phase-handoff.json').write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        (self.folder / 'phase-handoff.COMPLETE').write_text(digest + '\n')
        return digest

    def publish(self, *, ui=True):
        rows = {'events.jsonl': self.event}
        if ui:
            rows['ui-presented.jsonl'] = self.receipt
        specs = []
        for name, row in rows.items():
            raw = (json.dumps(row) + '\n').encode()
            (self.folder / name).write_bytes(raw)
            specs.append({'file': name, 'bytes': len(raw), 'rows': 1, 'sha256': hashlib.sha256(raw).hexdigest()})
        self.manifest.update(prefixes=specs, missing_at_cutoff=[name for name in STREAMS if name not in rows])
        return self.write_manifest()

    def unbound(self):
        self.manifest['match_id_at_cutoff'] = None
        self.event['snapshot']['match_id'] = None
        self.publish()

    def bind(self, mid='12345', *, session=None, prefix_id=None):
        prefix_id = prefix_id or hashlib.sha256((self.folder / 'phase-handoff.json').read_bytes()).hexdigest()
        document = {'schema_version': 1, 'kind': 'foresight_draft_match_binding',
            'session_id': session or self.sid, 'match_id': mid, 'phase_prefix_package_id': prefix_id,
            'bound_recorded_at_utc': '2026-10-26T00:00:11Z',
            'basis': 'same_runtime_draft_session_explicit_match_binding'}
        raw = json.dumps(document).encode()
        (self.folder / 'phase-match-binding.json').write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        (self.folder / 'phase-match-binding.COMPLETE').write_text(digest + '\n')
        return digest

    def test_tails_can_grow_before_and_during_review_but_are_never_included(self):
        with (self.folder / 'events.jsonl').open('ab') as stream:
            stream.write(b'not-json-in-later-tail')
        with open_rows(self.root, self.sid) as (_, rows):
            records = list(rows)
            with (self.folder / 'ui-presented.jsonl').open('ab') as stream:
                stream.write(b'{"later":"in_game"}\n')
        self.assertEqual(len(records), 2)
        result = review(self.root, self.sid)
        self.assertEqual(result['selection_candidate_event_rows'], 1)
        self.assertEqual(result['selection_candidate_ui_receipt_rows'], 1)
        self.assertEqual(result['stream_rows']['events.jsonl'], 1)
        self.assertFalse(result['later_tail_included'])
        self.assertFalse(result['recommendation_quality_verified'])

    def test_modified_or_shrunk_prefix_is_refused(self):
        path = self.folder / 'events.jsonl'
        path.write_bytes(path.read_bytes().replace(b'axe', b'xxe'))
        with self.assertRaises(ValueError):
            verify(self.root, self.sid)
        self.publish()
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            verify(self.root, self.sid)

    def test_missing_receipt_stream_remains_unknown_not_zero(self):
        self.publish(ui=False)
        result = review(self.root, self.sid)
        self.assertIsNone(result['selection_candidate_ui_receipt_rows'])
        self.assertNotIn('ui-presented.jsonl', result['stream_rows'])
        self.assertIn('ui-presented.jsonl', result['missing_at_cutoff'])

    def test_calculated_advice_and_ui_application_receipts_are_not_conflated(self):
        self.receipt['view_input'] = {'snapshot': {'session_id': self.sid, 'match_id': '12345', 'phase': 'strategy'},
                                      'advice': {'candidates': [{'hero_id': 'npc_dota_hero_axe'}]}}
        self.publish()
        result = review(self.root, self.sid)
        self.assertEqual(result['selection_candidate_event_rows'], 1)
        self.assertEqual(result['selection_candidate_ui_receipt_rows'], 0)

    def test_foreign_nested_identity_even_with_new_sha_is_refused(self):
        self.receipt['view_input'] = {'snapshot': {'session_id': 'b' * 32, 'match_id': '12345'}}
        self.publish()
        with self.assertRaisesRegex(ValueError, 'session_conflict'):
            verify(self.root, self.sid)

    def test_reader_cannot_resume_and_receipt_cannot_be_silently_replaced(self):
        with open_rows(self.root, self.sid) as (_, rows):
            next(rows)
        with self.assertRaisesRegex(ValueError, 'reader_closed'):
            next(rows)
        _, receipt = verify(self.root, self.sid)
        self.manifest['ending_phase'] = 'in_game'
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'receipt_changed'):
            verify(self.root, self.sid, expected_package_id=receipt)

    def test_locked_match_is_refused_before_logs(self):
        self.manifest['match_id_at_cutoff'] = '9056629627'
        self.write_manifest()
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in STREAMS:
                raise AssertionError('locked log must not open')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded), self.assertRaises(ValueError):
            verify(self.root, self.sid)

    def test_roster_path_and_type_tampering_are_refused(self):
        original = self.manifest['prefixes'][0]['file']
        self.manifest['prefixes'][0]['file'] = '../private.local.json'
        self.write_manifest()
        with self.assertRaises(ValueError):
            verify(self.root, self.sid)
        self.manifest['prefixes'][0]['file'] = original
        self.manifest['prefixes'][0]['bytes'] = True
        self.write_manifest()
        with self.assertRaises(ValueError):
            verify(self.root, self.sid)

    def test_late_binding_does_not_rewrite_cutoff_identity_or_prefix(self):
        self.unbound()
        frozen = (self.folder / 'phase-handoff.json').read_bytes()
        self.assertIsNone(review(self.root, self.sid)['effective_match_id'])
        digest = self.bind()
        result = review(self.root, self.sid)
        self.assertIsNone(result['match_id_at_cutoff'])
        self.assertEqual(result['effective_match_id'], '12345')
        self.assertEqual(result['binding_package_id'], digest)
        self.assertEqual(frozen, (self.folder / 'phase-handoff.json').read_bytes())

    def test_binding_appearing_mid_analysis_invalidates_old_unbound_result(self):
        self.unbound()
        with self.assertRaisesRegex(ValueError, 'binding_receipt_changed'):
            with open_rows(self.root, self.sid) as (_, rows):
                next(rows)
                self.bind()

    def test_binding_cannot_cross_session_or_prefix(self):
        self.unbound()
        for values in ({'session': 'b' * 32}, {'prefix_id': '0' * 64}):
            self.bind(**values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                verify(self.root, self.sid)

    def test_locked_late_binding_is_refused_before_log_body(self):
        self.unbound()
        self.bind('9056629627')
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in STREAMS:
                raise AssertionError('locked log must not open')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded), self.assertRaises(ValueError):
            verify(self.root, self.sid)
