import json
from pathlib import Path
import tempfile
import time
import re
import unittest

from dota2_insights.ward_validation_gap_scan import resolve_gaps, scan_text


class WardValidationGapScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'large.json'

    def test_chunk_boundary_and_long_digit_strings(self):
        self.path.write_bytes(b'x' * 59 + b'9050000001 ' + b'1' * 150 + b' 9050000002 end')
        ids, sha = scan_text(self.path, time.monotonic() + 10, chunk_size=64)
        self.assertEqual(ids, {'9050000001', '9050000002'})
        self.assertEqual(len(sha), 64)

    def test_resolution_preserves_parent_and_does_not_certify(self):
        self.path.write_bytes(b'9050000003')
        parent = {'scan_issues': [{'path': str(self.path), 'reason': 'text_exceeds_scan_limit'}],
                  'history_sources': [], 'exposure': {}, 'local_replays': [{'match_id': '9050000003'}]}
        before = json.dumps(parent)
        result = resolve_gaps(parent)
        self.assertEqual(json.dumps(parent), before)
        self.assertEqual(result['stream_resolved_file_count'], 1)
        self.assertTrue(result['history_scan_complete'])
        self.assertFalse(result['eligibility_certified'])
        self.assertEqual(result['local_replays'][0]['eligibility'], 'excluded')

    def test_deadline_leaves_gap_open(self):
        self.path.write_bytes(b'9050000004')
        with self.assertRaises(TimeoutError):
            scan_text(self.path, time.monotonic() - 1)

    def test_final_overlap_does_not_invent_a_boundary_inside_long_number(self):
        self.path.write_bytes(b'1' * 100 + b' ' + b'x' * 53)
        ids, _ = scan_text(self.path, time.monotonic() + 10, chunk_size=64)
        self.assertEqual(ids, set())

    def test_all_chunk_offsets_agree_with_whole_text_search(self):
        for offset in range(65):
            body = (b'x' * offset + b'9050000001 ' + b'1' * 100 + b' ' +
                    b'9050000002 ' + b'2' * 11 + b' ' + b'9050000003')
            self.path.write_bytes(body)
            ids, _ = scan_text(self.path, time.monotonic() + 10, chunk_size=64)
            expected = {v.decode() for v in re.findall(rb'(?<!\d)(\d{10})(?!\d)', body)}
            self.assertEqual(ids, expected, offset)
