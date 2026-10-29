"""Bounded QPC examples from generated CSV, never ETW/device capture."""
import csv
import io
from pathlib import Path
import unittest

from dota2_insights.presentmon_metrics import _read_csv_rows


def read(rows, windows=((2000, 4000),)):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(('Application', 'ProcessID', 'SwapChainAddress', 'PresentRuntime', 'QPCTime',
                     'MsBetweenPresents', 'Dropped', 'MsBetweenDisplayChange', 'MsUntilDisplayed'))
    for row in rows:
        writer.writerow(('dota2.exe', 42, *row))
    stream.seek(0)
    return _read_csv_rows(stream, Path('generated.csv'), 'dota2.exe', 'a'*64, 1,
                          qpc_windows=windows, qpc_frequency_hz=1000 if windows is not None else None)


class LongIntervalTests(unittest.TestCase):
    def test_submitted_and_displayed_are_one_row_with_separate_QPC(self):
        r = read([('0x1', 'DXGI', 3000, 80, 0, 120, 100)])
        event, = r['long_interval_examples']
        self.assertEqual(event['csv_data_row'], 1)
        self.assertEqual(event['rank_ms'], 120)
        self.assertEqual([(x['metric'], x['qpc_start'], x['qpc_end']) for x in event['intervals']],
                         [('submitted', 2920, 3000), ('displayed', 2980, 3100)])
        self.assertFalse(r['long_interval_example_policy']['workload_causality_verified'])

    def test_top_five_across_streams_preserve_identity_and_stable_ties(self):
        rows = [('0x1' if i % 2 else '0x2', 'DXGI', 3000+i, ms, 1, ms+1000, 0)
                for i, ms in enumerate((50, 60, 80, 80, 70, 90, 100))]
        r = read(rows)
        self.assertEqual([e['csv_data_row'] for e in r['long_interval_examples']], [7, 6, 3, 4, 5])
        self.assertEqual(len(r['streams']), 2)
        self.assertEqual({e['swap_chain'] for e in r['long_interval_examples']}, {'0x1', '0x2'})
        self.assertTrue(all(len(e['intervals']) == 1 for e in r['long_interval_examples']))

    def test_scope_edges_and_unknown_display_are_not_retained(self):
        r = read([('0x1', 'DXGI', 2010, 100, 0, 100, 0),
                  ('0x1', 'DXGI', 3980, 30, 0, 80, 100),
                  ('0x1', 'DXGI', 3000, 20, 'NA', 120, 0)])
        self.assertEqual(r['long_interval_examples'], [])
        self.assertEqual(r['streams'][0]['boundary_excluded_metrics']['msbetweendisplaychange'], 2)

    def test_disjoint_context_windows_cannot_be_bridged(self):
        r = read([('0x1', 'DXGI', 3050, 120, 0, 40, 0)], windows=((2000, 2100), (3000, 3100)))
        event, = r['long_interval_examples']
        self.assertEqual(event['intervals'], [{'metric': 'displayed', 'interval_ms': 40,
                                             'qpc_start': 3010, 'qpc_end': 3050, 'context_window_index': 1}])

    def test_unscoped_metrics_cannot_invent_correlated_examples(self):
        r = read([('0x1', 'DXGI', 3000, 100, 0, 100, 0)], windows=None)
        self.assertEqual(r['long_interval_examples'], [])
        self.assertEqual(r['long_interval_example_policy']['status'], 'unavailable_without_validated_qpc_scope')
        self.assertEqual(r['streams'][0]['submitted_frames']['maximum_frame_ms'], 100)


if __name__ == '__main__':
    unittest.main()
