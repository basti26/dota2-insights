import unittest

from dota2_insights.audio_reference_coverage import annotate


def segment(start, duration=5, reason='five_second_segment', take='a'):
    return {'take_label': {'take_id': take}, 'take_audio_start_offset_s': start,
            'audio_seconds': duration, 'ended_by': reason}


def edge(offset, take='a', bank=True):
    return {'audio_offset_s': offset, 'take_id': take, 'covered_by_bank': bank}


class CoverageTests(unittest.TestCase):
    def test_contiguous_segments_and_half_open_end(self):
        report = annotate([edge(0), edge(5), edge(10)], [segment(0), segment(5)])
        self.assertEqual([e['pcm_coverage_status'] for e in report['edges']],
                         ['point_recorded_window_incomplete', 'continuous_window_recorded',
                          'point_not_recorded'])
        self.assertNotIn('pcm_coverage_status', edge(5))

    def test_pause_and_missing_pcm_do_not_bridge(self):
        for segments in ([segment(0, reason='pause'), segment(5)],
                         [segment(0), segment(6)]):
            report = annotate([edge(4), edge(5.5), edge(8)], segments)
            self.assertFalse(report['edges'][0]['pcm_reference_window_recorded'])
            self.assertTrue(report['edges'][2]['pcm_reference_window_recorded'])
        self.assertFalse(report['edges'][1]['pcm_point_recorded'])

    def test_take_isolation_and_bank_counts(self):
        report = annotate([edge(3), edge(3, take='b'), edge(3, bank=False)], [segment(0)])
        self.assertEqual(report['counts'], {'continuous_window_recorded': 2, 'point_not_recorded': 1})
        self.assertEqual(report['bank_covered_counts'],
                         {'continuous_window_recorded': 1, 'point_not_recorded': 1})

    def test_invalid_metadata_and_edges_refused(self):
        for segments in ([segment(0), segment(4)], [segment(5), segment(0)],
                         [segment(0, 0)], [segment(float('nan'))]):
            with self.assertRaises(ValueError):
                annotate([edge(3)], segments)
        for value in (True, -1, float('inf')):
            with self.assertRaises(ValueError):
                annotate([edge(value)], [segment(0)])


if __name__ == '__main__':
    unittest.main()
