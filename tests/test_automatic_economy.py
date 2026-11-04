"""Generated records/replays and synthetic scheduler only; no live processes."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from lock_fixtures import install_local_lock
from dota2_insights import automatic_economy as a
from dota2_insights.automatic_economy_service import Service, lease
import test_observation_handoff as equipment_fixture
import test_match_journals as gsi_fixture


class AutomaticReviewTests(unittest.TestCase):
    def setUp(self):
        install_local_lock(self)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'config').mkdir()
        self.history = self.root / 'history'; self.history.mkdir()
        a.atomic(self.root / 'config/history.local.json', {'schema_version': 1, 'read_only': True,
            'roots': {'helper_history': str(self.history)}})
        self.clock = 1000

    def endings(self, count=5):
        result = {}
        for i in range(count):
            mid = str(12345 + i)
            result[mid] = {'match_id': mid, 'base': str(self.history),
                'ended_at_utc': (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)).isoformat()}
        return result

    def test_bootstrap_recent_three_crash_resume_dedup_and_new_end(self):
        found = self.endings()
        runner = Mock(side_effect=lambda task, **_: {**task, 'state': 'waiting_replay'})
        with patch.object(a, 'endings', return_value=found):
            service = Service(self.root, runner=runner, check=lambda: None, now=lambda: self.clock)
            service.cycle()
            self.assertEqual(set(service.state['tasks']), {'12347', '12348', '12349'})
            reloaded = Service(self.root, runner=runner, check=lambda: None, now=lambda: self.clock)
            reloaded.cycle(); reloaded.cycle(); reloaded.cycle()
            self.assertEqual(runner.call_count, 3)
            found['12350'] = {**found['12349'], 'match_id': '12350'}
            reloaded.cycle()
            self.assertEqual(runner.call_count, 4)
            self.assertEqual(reloaded.state['tasks']['12350']['state'], 'waiting_replay')

    def test_game_gate_never_discovers_or_runs_and_failures_back_off(self):
        runner = Mock(side_effect=ValueError('bad_source'))
        service = Service(self.root, runner=runner, check=Mock(side_effect=InterruptedError()), now=lambda: self.clock)
        with patch.object(a, 'endings') as discover:
            service.cycle(); discover.assert_not_called(); runner.assert_not_called()
        service.check = lambda: None
        with patch.object(a, 'endings', return_value=self.endings(1)):
            service.cycle(); service.cycle()
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(service.state['tasks']['12345']['state'], 'review_failed')
        self.assertGreater(service.state['tasks']['12345']['next_check_epoch'], self.clock)

    def test_two_service_instances_cannot_own_same_queue(self):
        with lease(self.root) as first:
            self.assertTrue(first)
            with lease(self.root) as second:
                self.assertFalse(second)

    def test_real_closed_record_pipeline_waits_then_compares_replay(self):
        eq = equipment_fixture.ObservationHandoffTests(); eq.setUp(); self.addCleanup(eq.doCleanups)
        gs = gsi_fixture.MatchJournalTests(); gs.setUp(); self.addCleanup(gs.doCleanups)
        shutil.copytree(gs.root / 'self-economy', eq.root / 'self-economy')
        self.history = eq.root
        a.atomic(self.root / 'config/history.local.json', {'schema_version': 1, 'read_only': True,
            'roots': {'helper_history': str(eq.root)}})
        (self.root / 'data').mkdir()
        items = {'blink': {'cost': 2250}}
        raw = json.dumps(items).encode()
        (self.root / 'data/items.json').write_bytes(raw)
        a.atomic(self.root / 'data/items.meta.json', {'cache_sha256': hashlib.sha256(raw).hexdigest()})
        eq.row['clock_context'].update(clock_time_s=100, alignment_delta_s=.1, self_team='radiant')
        eq.row['attempt']['hero']['slots'] = [{'slot_id': f'main_{i+1}' if i < 6 else f'backpack_{i-5}',
            'status': 'known' if i == 0 else 'empty', 'item_id': 'blink' if i == 0 else None,
            'candidate_margin': .5} for i in range(9)]
        eq.write_payload()
        task = {'match_id': '12345', 'base': str(eq.root)}
        task = a.run_task(task, root=self.root, check=lambda: None)
        self.assertEqual(task['state'], 'waiting_replay')
        self.assertTrue(Path(task['report']).is_file())
        original = eq.source.read_bytes()
        # Identical inputs do not regenerate a report.
        with patch.object(a, 'record_review', side_effect=AssertionError('duplicate analysis')):
            self.assertEqual(a.run_task(task, root=self.root, check=lambda: None)['report'], task['report'])
        replay = eq.root / 'replay-downloads/12345.dem'; replay.parent.mkdir()
        replay.write_bytes(b'PBDEMS2\0' + b'generated only' * 4)
        with patch.object(a, 'parse_replay', side_effect=TimeoutError('generated timeout')):
            task = a.run_task(task, root=self.root, check=lambda: None)
        self.assertEqual(task['state'], 'replay_review_failed')
        self.assertTrue(Path(task['report']).is_file())  # Records report survives parser failure.

        def generated_parser(root, source, output, heartbeat, check):
            output.mkdir()
            inventory = [{'slot': i, 'status': 'occupied' if i == 0 else 'empty',
                'item_key': 'blink' if i == 0 else None} for i in range(9)]
            row = {'kind': 'hero_sample', 'match_id': '12345', 'hero_id': 'npc_dota_hero_axe',
                'game_time_s': 100, 'tick': 3000, 'inventory': inventory, 'paused': False}
            body = (json.dumps(row) + '\n').encode()
            (output / 'hero_samples.jsonl').write_bytes(body)
            a.atomic(output / 'manifest.json', {'kind': 'offline_replay_archive', 'match_id': '12345',
                'parsed_match_id': '12345', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'source_unchanged_stat': True, 'export_complete': True, 'parse_error': None,
                'truncated_at_tick': None, 'stop_reason': 'replay_eof',
                'files': {'hero_samples.jsonl': {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}}})
        with patch.object(a, 'parse_replay', side_effect=generated_parser):
            task = a.run_task(task, root=self.root, check=lambda: None)
        self.assertEqual(task['state'], 'review_complete_evidence_only')
        report = a.read_json(Path(task['report']).with_name('report.json'))
        self.assertFalse(report['accuracy_verified'])
        self.assertEqual(report['replay_comparisons'][0]['counts']['same_item_id'], 1)
        self.assertEqual(eq.source.read_bytes(), original)
        # Modified historical payload is never silently accepted as previous success.
        eq.source.write_bytes(original + b'{}\n')
        changed = a.run_task(task, root=self.root, check=lambda: None)
        self.assertEqual(changed['state'], 'waiting_sources_or_comparable_records')

    def test_new_game_terminates_parser_before_next_iteration(self):
        child = Mock(); child.poll.return_value = None
        check = Mock(side_effect=[None, InterruptedError('game_running')])
        with patch.object(a.subprocess, 'Popen', return_value=child), self.assertRaises(InterruptedError):
            a.parse_replay(self.root, self.root / '12345.dem', self.root / 'archive', lambda _: None, check)
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=3)

    def test_no_sources_never_publishes_a_pass(self):
        with patch.object(a, 'inputs', return_value={'gsi': [], 'equipment': []}):
            task = a.run_task({'match_id': '12345', 'base': str(self.history)}, root=self.root, check=lambda: None)
        self.assertEqual(task['state'], 'waiting_sources_or_comparable_records')
