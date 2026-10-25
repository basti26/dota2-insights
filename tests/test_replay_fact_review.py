"""Generated bounded archives only; never open a DEM or use a network/game API."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights import replay_fact_review as review
from dota2_insights.replay_phase_evidence import PhaseTracker


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def event(clock, **changes):
    value = {'kind': 'combat_event', 'match_id': '123', 'tick': int(clock * 10),
             'game_time_s': clock, 'event_type': 'DEATH',
             'target': 'npc_dota_hero_skeleton_king',
             'target_is_hero': True, 'target_is_hero_present': True,
             'target_is_illusion': False, 'target_is_illusion_present': True}
    value.update(changes)
    return value


def hero(clock, **changes):
    value = {'kind': 'hero_sample', 'match_id': '123', 'tick': int(clock * 10),
             'game_time_s': clock, 'hero_id': 'npc_dota_hero_skeleton_king',
             'hero_name': 'npc_dota_hero_skeleton_king', 'hero_numeric_id': 42,
             'team': 2, 'level': None, 'lane_metrics': {'net_worth': None, 'last_hits': 0},
             'inventory': [{'slot': 0, 'status': 'occupied', 'item_id': None},
                           {'slot': 9, 'status': 'occupied', 'item_id': 1}],
             'private_extra': 'DO_NOT_COPY_HERO_EXTRA'}
    value.update(changes)
    return value


class FactReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive, self.output = self.root / 'archive', self.root / 'review'
        self.archive.mkdir()
        self.lock = patch('dota2_insights.replay_holdout.refuse_locked_match')
        self.refuse = self.lock.start()
        self.addCleanup(self.lock.stop)

    def fixture(self, events=None, heroes=None, *, include_events=True, include_heroes=True,
                native=True, complete=True):
        files, counts = {}, {}
        for name, rows, include, count_key in (
                ('events.jsonl', [event(1)] if events is None else events, include_events, 'events'),
                ('hero_samples.jsonl', [hero(1)] if heroes is None else heroes, include_heroes, 'hero_samples')):
            if include:
                raw = b''.join(encoded(row) for row in rows)
                (self.archive / name).write_bytes(raw)
                files[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                counts[count_key] = len(rows)
        self.manifest = {'schema_version': 1, 'kind': 'offline_replay_archive',
                         'match_id': '123', 'parsed_match_id': '123', 'source_file': '123.dem',
                         'source_sha256': 'a' * 64, 'source_bytes': 100,
                         'source_unchanged_stat': True, 'files': files, 'counts': counts,
                         'export_complete': complete, 'parse_error': None,
                         'truncated_at_tick': None, 'stop_reason': 'replay_eof' if complete else 'cancel_requested'}
        if native:
            tracker = PhaseTracker()
            for values in [(1, 0, 5), (2, 9, 5), (3, 10, 6), (4, 11, 6)]:
                tracker.observe(*values)
            self.manifest['phase_evidence'] = tracker.manifest(observation_complete=complete)
        self.save_manifest()

    def save_manifest(self):
        (self.archive / 'manifest.json').write_bytes(encoded(self.manifest))

    def replace_stream(self, name, raw):
        (self.archive / name).write_bytes(raw)
        self.manifest['files'][name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.save_manifest()

    def load_report(self):
        return json.loads((self.output / 'report.json').read_bytes())

    def assert_failed(self):
        self.assertFalse((self.output / 'report.json').exists())
        self.assertFalse((self.output / 'report.html').exists())
        self.assertIs(json.loads((self.output / 'FAILED.json').read_bytes())['complete'], False)

    def api(self, duration=5):
        facts = self.root / 'facts.json'
        facts.write_bytes(encoded({'match_id': 123, 'duration': duration}))
        receipt = self.root / 'request.json'
        receipt.write_bytes(encoded({'match_id': '123', 'method': 'GET', 'http_status': 200,
                                    'status': 'downloaded_sanitized',
                                    'url': 'https://api.opendota.com/api/matches/123',
                                    'facts_sha256': hashlib.sha256(facts.read_bytes()).hexdigest()}))
        return facts, receipt

    def test_native_periods_hero_selectors_unknowns_html_and_source_unchanged(self):
        self.fixture(events=[event(-1), event(1, inflictor='<script>alert(1)</script>'),
                             event(9), event(10), event(11), event(2)],
                     heroes=[hero(1), hero(9), hero(10), hero(11), hero(12)])
        before = {p.name: p.read_bytes() for p in self.archive.iterdir()}
        summary = review.review_archive(self.archive, self.output)
        report = self.load_report()
        self.assertTrue(summary['complete'])
        self.assertTrue(summary['source_complete'])
        self.assertEqual(report['streams']['events']['period_counts'],
                         {'pregame': 1, 'in_match': 2, 'end_boundary_uncertain': 2,
                          'post_match_tail': 1, 'end_unknown': 0, 'clock_unknown': 0})
        self.assertEqual(report['streams']['events']['clock_order_regressions'], 1)
        self.assertEqual(report['death_semantics']['counts']['raw_death_events'], 6)
        self.assertEqual(report['death_semantics']['counts']['unresolved_real_hero_death_events'], 6)
        snapshots = report['hero_facts']['heroes']['2:npc_dota_hero_skeleton_king']
        self.assertEqual(snapshots['latest_in_match']['source_ref'], 'hero_samples.jsonl:1')
        self.assertEqual(snapshots['latest_boundary']['source_ref'], 'hero_samples.jsonl:3')
        self.assertEqual(snapshots['first_tail']['source_ref'], 'hero_samples.jsonl:4')
        self.assertEqual(snapshots['latest_any']['source_ref'], 'hero_samples.jsonl:5')
        latest = snapshots['latest_any']
        self.assertIsNone(latest['level'])
        self.assertIsNone(latest['lane_metrics']['net_worth'])
        self.assertEqual(latest['lane_metrics']['last_hits'], 0)
        self.assertEqual([item['slot'] for item in latest['inventory']], [0])
        self.assertNotIn('DO_NOT_COPY_HERO_EXTRA', json.dumps(report))
        rendered = (self.output / 'report.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script src=', rendered)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.archive.iterdir()})

    def test_explicit_api_priority_and_independent_boundary(self):
        self.fixture(events=[event(5), event(6), event(6.001)], heroes=[])
        facts, receipt = self.api()
        review.review_archive(self.archive, self.output, facts=facts, receipt=receipt)
        report = self.load_report()
        self.assertEqual(report['end_evidence']['source_kind'], 'external_opendota_final_summary')
        self.assertEqual(report['streams']['events']['period_counts']['end_boundary_uncertain'], 2)
        self.assertEqual(report['streams']['events']['period_counts']['post_match_tail'], 1)

    def test_partial_archive_missing_streams_and_empty_are_distinct(self):
        self.fixture(events=[], include_heroes=False, native=False, complete=False)
        review.review_archive(self.archive, self.output)
        report = self.load_report()
        self.assertTrue(report['complete'])
        self.assertFalse(report['source_status']['complete'])
        self.assertEqual(report['streams']['events']['status'], 'present_empty')
        self.assertEqual(report['death_semantics']['counts']['raw_death_events'], 0)
        self.assertEqual(report['streams']['hero_samples']['status'], 'missing')
        self.assertIsNone(report['streams']['hero_samples']['rows'])
        other = self.root / 'other'
        self.manifest['files'] = {}
        self.manifest['counts'] = {}
        self.save_manifest()
        review.review_archive(self.archive, other)
        second = json.loads((other / 'report.json').read_bytes())
        self.assertEqual(second['death_semantics']['status'], 'missing')
        self.assertIsNone(second['death_semantics']['counts'])

    def test_hash_bytes_or_row_identity_pollution_fail_closed(self):
        self.fixture()
        (self.archive / 'events.jsonl').write_bytes(encoded(event(2)))
        with self.assertRaisesRegex(ValueError, 'source_bytes_or_sha256_mismatch'):
            review.review_archive(self.archive, self.output)
        self.assert_failed()
        for change in ({'match_id': '124'}, {'kind': 'hero_sample'}, {'event_type': None}):
            with self.subTest(change=change):
                self.replace_stream('events.jsonl', encoded(event(1, **change)))
                target = self.root / ('bad_' + next(iter(change)))
                with self.assertRaises(ValueError):
                    review.review_archive(self.archive, target)
                self.assertTrue((target / 'FAILED.json').exists())

    def test_duplicate_nonfinite_and_overflow_json_rejected_everywhere(self):
        self.fixture()
        raw_rows = [b'{"match_id":"123","match_id":"123"}\n',
                    encoded(event(1)).replace(b'"game_time_s": 1', b'"game_time_s": NaN'),
                    encoded(event(1)).replace(b'"game_time_s": 1', b'"game_time_s": 1e999')]
        for index, raw in enumerate(raw_rows):
            with self.subTest(raw=raw):
                self.replace_stream('events.jsonl', raw)
                with self.assertRaises(ValueError):
                    review.review_archive(self.archive, self.root / f'bad_json_{index}')
        facts, receipt = self.api()
        facts.write_bytes(b'{"match_id":123,"duration":5,"duration":6}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            review.review_archive(self.archive, self.root / 'bad_facts', facts=facts, receipt=receipt)
        (self.archive / 'manifest.json').write_bytes(b'{"match_id":"123","match_id":"124"}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            review.review_archive(self.archive, self.root / 'bad_manifest')

    def test_guard_exception_and_post_write_failure_remove_success_report(self):
        self.fixture()
        def cancelled():
            if self.output.exists():
                raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            review.review_archive(self.archive, self.output, guard=cancelled)
        self.assert_failed()
        original = review._Outputs.write
        def late_failure(writer, name, data):
            original(writer, name, data)
            if name == 'report.json':
                raise RuntimeError('disk_failure_after_report_write')
        self.output = self.root / 'late'
        with patch.object(review._Outputs, 'write', late_failure), self.assertRaises(RuntimeError):
            review.review_archive(self.archive, self.output)
        self.assert_failed()

    def test_source_mutation_during_read_is_detected(self):
        self.fixture(events=[event(1), event(2)])
        changed = []
        def mutate():
            if (self.output / 'event-periods.jsonl').exists() and not changed:
                changed.append(True)
                with (self.archive / 'events.jsonl').open('ab') as stream:
                    stream.write(encoded(event(3)))
        with self.assertRaises(ValueError):
            review.review_archive(self.archive, self.output, guard=mutate)
        self.assert_failed()

    def test_budgets_source_line_rows_total_and_output(self):
        self.fixture(events=[event(1), event(2)])
        for index, (name, limit) in enumerate((('MAX_FILE_BYTES', 10),
                                             ('MAX_TOTAL_SOURCE_BYTES', 10),
                                             ('MAX_LINE_BYTES', 100),
                                             ('MAX_STREAM_ROWS', 1),
                                             ('MAX_TOTAL_ROWS', 1),
                                             ('MAX_OUTPUT_BYTES', 256))):
            with self.subTest(limit=name), patch.object(review, name, limit):
                target = self.root / f'budget_{index}'
                with self.assertRaises(ValueError):
                    review.review_archive(self.archive, target)
                self.assertFalse((target / 'report.json').exists())
                if target.exists():
                    self.assertTrue((target / 'FAILED.json').exists())

    def test_sealed_guard_precedes_data_and_api_reads_and_output_creation(self):
        self.fixture()
        facts, receipt = self.api()
        reads = []
        original = review._Inputs.read
        def tracked(reader, path, **options):
            reads.append(Path(path).name)
            return original(reader, path, **options)
        self.refuse.side_effect = ValueError('sealed')
        with patch.object(review._Inputs, 'read', tracked), self.assertRaisesRegex(ValueError, 'sealed'):
            review.review_archive(self.archive, self.output, facts=facts, receipt=receipt)
        self.assertEqual(reads, ['manifest.json'])
        self.assertFalse(self.output.exists())

    def test_existing_output_is_never_overwritten(self):
        self.fixture()
        review.review_archive(self.archive, self.output)
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        with self.assertRaises(FileExistsError):
            review.review_archive(self.archive, self.output)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_links_and_manifest_traversal_are_refused(self):
        self.fixture()
        self.manifest['files']['../outside.jsonl'] = {'bytes': 0, 'sha256': 'b' * 64}
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'manifest_files'):
            review.review_archive(self.archive, self.output)
        self.manifest['files'].pop('../outside.jsonl')
        self.save_manifest()
        link = self.root / 'linked_archive'
        try:
            os.symlink(self.archive, link, target_is_directory=True)
        except OSError:
            return  # Windows may deny symlink creation; reparse checks remain in code.
        with self.assertRaisesRegex(ValueError, 'link_path'):
            review.review_archive(link, self.root / 'linked_output')

    def test_hero_identity_limit_and_whitelist_pollution(self):
        self.fixture(heroes=[hero(1, hero_id=f'npc_dota_hero_generated_{i}',
                                 hero_name=f'npc_dota_hero_generated_{i}') for i in range(11)])
        with self.assertRaisesRegex(ValueError, 'hero_identity_limit'):
            review.review_archive(self.archive, self.output)
        self.assert_failed()
        self.replace_stream('hero_samples.jsonl', encoded(hero(1, inventory=[{'slot': 0, 'item_id': {'bad': 1}}])))
        self.manifest['counts']['hero_samples'] = 1
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'inventory_value'):
            review.review_archive(self.archive, self.root / 'bad_inventory')

    def test_cli_uses_existing_files_and_returns_compact_summary(self):
        self.fixture()
        from tools.review_replay_facts import main
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ['--archive', str(self.archive), '--output', str(self.output)]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(args), 0)
        self.assertTrue(json.loads(stdout.getvalue())['complete'])
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            self.assertEqual(main(args), 2)


if __name__ == '__main__':
    unittest.main()
