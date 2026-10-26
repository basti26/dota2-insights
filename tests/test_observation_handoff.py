"""Equipment handoff fixtures made independently of the real-time repository."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.observation_handoff import open_rows, review, verify


class ObservationHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.session, self.game = 'run_' + 'a' * 32, 'game_' + 'b' * 32
        self.folder = self.root / 'matches/sessions' / self.session
        self.folder.mkdir(parents=True)
        self.source = self.root / 'matches/match_12345' / self.session / 'observations.jsonl'
        self.source.parent.mkdir(parents=True)
        self.row = {'record_version': 1, 'session_id': self.session, 'match_id': '12345',
            'captured_at_utc': '2026-10-26T00:00:00Z',
            'clock_context': {'game_session_id': self.game, 'record_session_id': self.session},
            'hud_image': 'hud-000001.png', 'hud_image_sha256': 'c' * 64,
            'attempt': {'captured_at_utc': '2026-10-26T00:00:00Z', 'status': 'updated', 'accepted': True,
                        'hero': {'hero_id': 'npc_dota_hero_axe', 'slots': [{'status': 'known', 'item_id': 'blink'}]}}}
        self.manifest = {'schema_version': 1, 'kind': 'foresight_observation_handoff', 'match_id': '12345',
            'game_session_id': self.game, 'record_session_id': self.session,
            'closed_at_utc': '2026-10-26T00:00:10Z', 'end_provider_at_utc': '2026-10-26T00:00:09Z',
            'end_state': 'DOTA_GAMERULES_STATE_POST_GAME', 'capture_source_epoch_at_end': 2,
            'scope': 'bound_match_observations_submitted_before_post_game_cutoff',
            'accepted_observations_at_cutoff': 1, 'cutoff_jobs_drained': True,
            'late_inflight_work': 'retired_by_source_epoch_not_added_after_cutoff',
            'earlier_unbound_observations': 'not_included_or_relabelled',
            'recording_integrity': 'complete', 'health_scope': 'writer_cumulative_at_publication_not_per_match',
            'health': {'error_present': False, 'dropped': 0, 'images_failed': 0, 'image_limit_reached': False},
            'images': 'journal_declared_sha256_requires_separate_file_verification',
            'other_channels': 'not_closed_by_this_manifest'}
        self.write_payload()

    def write_manifest(self):
        raw = json.dumps(self.manifest).encode()
        (self.folder / 'observation-handoff.json').write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        (self.folder / 'observation-handoff.COMPLETE').write_text(digest + '\n')
        return digest

    def write_payload(self, raw=None):
        raw = raw if raw is not None else (json.dumps(self.row) + '\n').encode()
        self.source.write_bytes(raw)
        self.manifest['payload'] = {'path': f'match_12345/{self.session}/observations.jsonl',
            'bytes': len(raw), 'rows': 1, 'sha256': hashlib.sha256(raw).hexdigest()}
        return self.write_manifest()

    def test_review_is_independent_of_whole_launcher_or_image_availability(self):
        result = review(self.root, self.session)
        self.assertEqual(result['rows'], 1)
        self.assertEqual(result['hero_observation_counts'], {'npc_dota_hero_axe': 1})
        self.assertEqual(result['slot_status_counts'], {'known': 1})
        self.assertFalse(result['accuracy_verified'])
        self.assertFalse(result['images_verified'])
        self.assertTrue(result['source_verified_before_and_after'])

    def test_missing_marker_or_undrained_cutoff_never_admitted(self):
        (self.folder / 'observation-handoff.COMPLETE').unlink()
        with self.assertRaises(OSError):
            verify(self.root, self.session)
        self.manifest['cutoff_jobs_drained'] = False
        self.write_manifest()
        with self.assertRaises(ValueError):
            verify(self.root, self.session)

    def test_empty_manifest_not_a_zero_observation_accuracy_claim(self):
        self.source.unlink()
        self.manifest.update(payload=None, accepted_observations_at_cutoff=0)
        self.write_manifest()
        result = review(self.root, self.session)
        self.assertFalse(result['channel_has_observations'])
        self.assertFalse(result['accuracy_verified'])
        self.source.write_bytes(b'{}\n')
        with self.assertRaisesRegex(ValueError, 'empty_manifest_disagrees'):
            verify(self.root, self.session)

    def test_cross_session_game_or_match_identity_refused_even_with_new_sha(self):
        for key, value in (('match_id', '99999'), ('session_id', 'run_' + 'd' * 32),
                           ('clock_context', {'game_session_id': 'game_' + 'd' * 32})):
            before = dict(self.row)
            self.row[key] = value
            self.write_payload()
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify(self.root, self.session)
            self.row = before

    def test_partial_tail_redirected_payload_or_frame_time_disagreement_refused(self):
        raw = (json.dumps(self.row)).encode()
        self.write_payload(raw)
        with self.assertRaises(ValueError):
            verify(self.root, self.session)
        self.write_payload()
        self.manifest['payload']['path'] = '../private.local.json'
        self.write_manifest()
        with self.assertRaises(ValueError):
            verify(self.root, self.session)
        self.row['attempt']['captured_at_utc'] = '2026-10-26T00:00:01Z'
        self.write_payload()
        with self.assertRaisesRegex(ValueError, 'frame_time_conflict'):
            verify(self.root, self.session)

    def test_health_gaps_and_scope_remain_explicit(self):
        self.manifest['health']['dropped'] = 1
        self.write_manifest()
        with self.assertRaises(ValueError):
            verify(self.root, self.session)
        self.manifest['recording_integrity'] = 'gaps'
        self.write_manifest()
        self.assertEqual(review(self.root, self.session)['recording_integrity'], 'gaps')

    def test_expected_receipt_and_after_analysis_source_change_refused(self):
        digest = self.write_manifest()
        self.manifest['capture_source_epoch_at_end'] = 3
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'index_source_changed'):
            verify(self.root, self.session, expected_package_id=digest)
        with self.assertRaises(ValueError):
            with open_rows(self.root, self.session) as (_, rows):
                next(rows)
                self.source.write_bytes(b'changed\n')

    def test_reader_cannot_resume_after_verified_context(self):
        with open_rows(self.root, self.session) as (_, rows):
            pass
        with self.assertRaisesRegex(ValueError, 'reader_closed'):
            next(rows)

    def test_sealed_id_refused_before_journal_body(self):
        self.manifest['match_id'] = '9056629627'
        self.write_manifest()
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name == 'observations.jsonl':
                raise AssertionError('sealed body must not open')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded), self.assertRaises(ValueError):
            verify(self.root, self.session)
