"""Native contract fixtures authored here; no live helper imports or records."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.match_journals import open_rows, scan, verify
from tools.review_match_journals import review


class MatchJournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = '11111111-1111-1111-1111-111111111111'
        self.mid = '12345'
        self.game, self.session = 'game_' + 'a' * 32, 'run_' + 'b' * 32
        self.folder = self.root / 'self-economy' / ('self-economy-' + self.run) / 'closed-matches' / ('match_' + self.mid) / self.game
        self.folder.mkdir(parents=True)
        self.rows = [self.row(1, False), self.row(2, True)]
        self.manifest = {'schema_version': 1, 'kind': 'foresight_match_journal', 'channel': 'self_economy',
            'match_id': self.mid, 'game_session_id': self.game, 'record_session_id': self.session, 'run_id': self.run,
            'scope': 'received_gsi_samples_until_first_post_game_not_whole_match_proof',
            'first_received_at_utc': self.rows[0]['received_at_utc'], 'last_received_at_utc': self.rows[-1]['received_at_utc'],
            'closed_at_utc': '2026-10-26T00:00:10+00:00', 'match_end_evidence': 'own_recorded_gsi_post_game',
            'recording_integrity': 'complete', 'upstream_dropped_at_close': 0, 'upstream_limited_at_close': False,
            'other_channels': 'not_closed_by_this_manifest'}
        self.publish()

    def row(self, seq, ended):
        return {'event_version': 1, 'event_kind': 'self_economy', 'run_id': self.run,
                'match_id': self.mid, 'game_session_id': self.game, 'record_session_id': self.session,
                'sequence': seq, 'received_at_utc': f'2026-10-26T00:00:0{seq}+00:00',
                'provider_at_utc': f'2026-10-26T00:00:0{seq}+00:00',
                'map_context': {'game_state': 'DOTA_GAMERULES_STATE_POST_GAME' if ended else 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'},
                'self_economy': {'hero_id': 'npc_dota_hero_axe', 'economy_status': 'reported', 'amount': 100 - seq, 'gpm': 300}}

    def publish(self):
        data = b''.join((json.dumps(row) + '\n').encode() for row in self.rows)
        (self.folder / 'samples.jsonl').write_bytes(data)
        self.manifest['payload'] = {'file': 'samples.jsonl', 'bytes': len(data), 'rows': len(self.rows), 'sha256': hashlib.sha256(data).hexdigest()}
        self.write_manifest()

    def write_manifest(self):
        raw = json.dumps(self.manifest).encode()
        (self.folder / 'manifest.json').write_bytes(raw)
        (self.folder / 'COMPLETE').write_text(hashlib.sha256(raw).hexdigest() + '\n')

    def test_accepts_match_without_closed_launcher_and_keeps_gsi_decrease(self):
        self.assertEqual(scan(self.root)[0]['state'], 'ready_with_gaps')
        self.assertFalse(list(self.root.rglob('recorders.json')))
        with open_rows(self.folder) as (_, rows):
            self.assertEqual([row['self_economy']['amount'] for row in rows], [99, 98])
        result = review(self.folder)
        self.assertEqual(result['last_reported_gsi']['amount'], 98)
        self.assertFalse(result['accuracy_verified'])
        self.assertEqual(result['other_channels'], 'not_closed_by_this_manifest')

    def test_missing_marker_and_changed_payload_are_refused(self):
        (self.folder / 'COMPLETE').unlink()
        self.assertEqual(scan(self.root)[0]['state'], 'blocked')
        self.publish()
        with (self.folder / 'samples.jsonl').open('ab') as stream:
            stream.write(b'{}\n')
        with self.assertRaises(ValueError):
            verify(self.folder)

    def test_foreign_identity_sequence_or_nonterminal_tail_refused_even_with_new_sha(self):
        for field, value in (('match_id', '54321'), ('sequence', 1),
                             ('map_context', {'game_state': 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'})):
            with self.subTest(field=field):
                self.rows[-1] = {**self.row(2, True), field: value}
                self.publish()
                with self.assertRaises(ValueError):
                    verify(self.folder)

    def test_partial_and_overlong_lines_refused(self):
        data = (self.folder / 'samples.jsonl').read_bytes()[:-1]
        (self.folder / 'samples.jsonl').write_bytes(data)
        self.manifest['payload'].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'partial_tail'):
            verify(self.folder)
        self.rows[-1]['padding'] = 'x' * 8192
        self.publish()
        with self.assertRaisesRegex(ValueError, 'line_limit'):
            verify(self.folder)

    def test_closed_reader_cannot_be_resumed_and_changes_during_read_rejected(self):
        with open_rows(self.folder) as (_, rows):
            next(rows)
        with self.assertRaisesRegex(ValueError, 'reader_closed'):
            next(rows)
        with self.assertRaises(ValueError):
            with open_rows(self.folder) as (_, rows):
                next(rows)
                (self.folder / 'samples.jsonl').write_bytes(b'changed\n')

    def test_locked_identity_refused_before_body_read(self):
        locked = self.folder.parent.with_name('match_9056629627') / self.game
        with patch('dota2_insights.match_journals._read', side_effect=AssertionError('body must not open')):
            with self.assertRaises(ValueError):
                verify(locked)

    def test_gaps_are_reported_and_cannot_be_labelled_complete(self):
        self.manifest['upstream_dropped_at_close'] = 2
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'health_disagrees'):
            verify(self.folder)
        self.manifest['recording_integrity'] = 'gaps'
        self.write_manifest()
        self.assertEqual(review(self.folder)['recording_integrity'], 'gaps')

    def test_manifest_cannot_redirect_to_other_file(self):
        self.manifest['payload']['file'] = '../private.json'
        self.write_manifest()
        with self.assertRaises(ValueError):
            verify(self.folder)

    def test_reader_never_yields_unsealed_appended_rows(self):
        with self.assertRaises(ValueError):
            with open_rows(self.folder) as (_, rows):
                first = next(rows)
                with (self.folder / 'samples.jsonl').open('ab') as stream:
                    stream.write((json.dumps(self.row(3, True)) + '\n').encode())
                self.assertEqual(first['sequence'], 1)
                self.assertEqual(next(rows)['sequence'], 2)
                with self.assertRaises(ValueError):
                    next(rows)
