"""Independent minimap contract fixtures, no Foresight imports or capture."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.minimap_handoff import STREAM_LIMITS, open_rows, review, verify


class MinimapHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = 'a' * 32
        self.folder = self.root / 'minimap-learning' / ('run_' + self.run)
        self.folder.mkdir(parents=True)
        self.receipt = self.root / 'minimap-learning/closed-runs' / self.folder.name
        self.receipt.mkdir(parents=True)
        self.rows = [{'kind': 'sample', 'sequence': 1, 'captured_at_utc': '2026-10-26T00:00:01Z',
                      'image': 'frames/frame-000001.png', 'image_sha256': '0' * 64}]
        self.session = {'run_id': self.run, 'chain_id': None, 'match_id': None, 'closed': True,
            'complete': True, 'error': None, 'limited': False, 'ended_reason': 'user_stop',
            'frames_saved': 1, 'images_saved': 1, 'capture_attempts': 1, 'evidence_complete': False,
            'started_at_utc': '2026-10-26T00:00:00Z', 'ended_at_utc': '2026-10-26T00:00:02Z',
            'recording_evidence': {'pending_images': 0, 'queue_policy': 'synchronous_no_pending_image_queue',
                'channels': {key: {'closed': True} for key in ('attempts', 'context', 'rejected', 'markers')}}}
        self.publish()

    def publish(self):
        raw = b''.join((json.dumps(row) + '\n').encode() for row in self.rows)
        (self.folder / 'samples.jsonl').write_bytes(raw)
        session_raw = json.dumps(self.session).encode()
        (self.folder / 'session.json').write_bytes(session_raw)
        self.document = {'schema_version': 1, 'kind': 'foresight_closed_minimap_run',
            'run_id': self.run, 'chain_id': None, 'match_id': None,
            'started_at_utc': self.session['started_at_utc'], 'ended_at_utc': self.session['ended_at_utc'],
            'close_reason': 'user_stop', 'scope': 'closed_standalone_run_not_whole_match',
            'match_association': 'unknown_no_time_inference', 'images': 'declared_references_require_separate_verification',
            'session': {'file': 'session.json', 'bytes': len(session_raw), 'sha256': hashlib.sha256(session_raw).hexdigest()},
            'streams': [{'file': 'samples.jsonl', 'bytes': len(raw), 'rows': len(self.rows), 'sha256': hashlib.sha256(raw).hexdigest()}],
            'missing_at_close': [name for name in STREAM_LIMITS if name != 'samples.jsonl']}
        return self.write_receipt()

    def write_receipt(self):
        raw = json.dumps(self.document).encode()
        (self.receipt / 'record-handoff.json').write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        (self.receipt / 'record-handoff.COMPLETE').write_text(digest + '\n')
        return digest

    def test_review_does_not_wait_for_launcher_or_claim_match_images_or_identity(self):
        result = review(self.root, self.run)
        self.assertEqual(result['sample_rows'], 1)
        self.assertIsNone(result['match_id'])
        self.assertFalse(result['images_verified'])
        self.assertFalse(result['identity_verified'])
        self.assertFalse(result['whole_match_coverage_verified'])
        self.assertFalse(result['source_evidence_complete'])
        self.assertEqual(result['first_sample_at_utc'], self.rows[0]['captured_at_utc'])

    def test_running_and_limited_session_refused_even_with_valid_receipt(self):
        for key in ('closed', 'complete', 'limited'):
            self.session[key] = key == 'limited'
            self.publish()
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify(self.root, self.run)
            self.session[key] = key != 'limited'

    def test_changed_session_journal_and_missing_stream_appearing_are_refused(self):
        for name, content in (('session.json', b'{}'), ('samples.jsonl', b'{}\n'), ('context.jsonl', b'')):
            self.publish()
            (self.folder / name).write_bytes(content)
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify(self.root, self.run)

    def test_changed_source_during_context_and_escaped_iterator_refused(self):
        with self.assertRaises(ValueError):
            with open_rows(self.root, self.run) as (_, _, rows):
                next(rows)
                (self.folder / 'samples.jsonl').write_bytes(b'{}\n')
        self.publish()
        with open_rows(self.root, self.run) as (_, _, rows):
            pass
        with self.assertRaisesRegex(ValueError, 'reader_closed'):
            next(rows)

    def test_changed_receipt_after_index_cannot_be_used(self):
        digest = self.publish()
        self.document['chain_id'] = 'b' * 32
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, 'receipt_changed'):
            verify(self.root, self.run, expected_package_id=digest)

    def test_partial_lines_incomplete_marker_and_invalid_roster_refused(self):
        (self.receipt / 'record-handoff.COMPLETE').unlink()
        with self.assertRaises((OSError, ValueError)):
            verify(self.root, self.run)
        self.publish()
        self.document['streams'][0]['file'] = '../secret'
        self.write_receipt()
        with self.assertRaises(ValueError):
            verify(self.root, self.run)
        self.publish()
        (self.folder / 'samples.jsonl').write_bytes(b'{')
        with self.assertRaises(ValueError):
            verify(self.root, self.run)

    def test_locked_declared_match_refused_before_session_or_journal(self):
        self.document['match_id'] = '9056629627'
        self.write_receipt()
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in ('session.json', 'samples.jsonl'):
                raise AssertionError('sealed body must not open')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded), self.assertRaises(ValueError):
            verify(self.root, self.run)

    def test_pending_queue_or_wrong_sample_counter_cannot_be_called_closed(self):
        self.session['recording_evidence']['pending_images'] = 1
        self.publish()
        with self.assertRaises(ValueError):
            verify(self.root, self.run)
        self.session['recording_evidence']['pending_images'] = 0
        self.session['frames_saved'] = 2
        self.publish()
        with self.assertRaisesRegex(ValueError, 'count_disagrees'):
            verify(self.root, self.run)

    def test_wrong_run_in_rows_and_nonsequential_samples_refused(self):
        for values in ({'run_id': 'b' * 32}, {'sequence': 2}, {'match_id': '12345'}):
            self.rows[0].update(values)
            self.publish()
            with self.subTest(values=values), self.assertRaises(ValueError):
                verify(self.root, self.run)
            self.rows[0].pop('run_id', None)
            self.rows[0].pop('match_id', None)
            self.rows[0]['sequence'] = 1

    def test_partly_consumed_iterator_is_closed_before_it_can_read_another_line(self):
        import dota2_insights.minimap_handoff as module
        original = module._rows
        read = []
        def tracked(*args):
            for value in original(*args):
                read.append(value[0])
                yield value
        self.rows.append({**self.rows[0], 'sequence': 2})
        self.session['frames_saved'] = 2
        self.publish()
        with patch.object(module, '_rows', tracked):
            with open_rows(self.root, self.run) as (_, _, rows):
                next(rows)
            before = len(read)
            with self.assertRaisesRegex(ValueError, 'reader_closed'):
                next(rows)
            self.assertEqual(len(read), before)
