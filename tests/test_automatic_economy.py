"""Generated records/replays and synthetic scheduler only; no live processes."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from lock_fixtures import install_local_lock
from dota2_insights import automatic_economy as a
from dota2_insights.automatic_economy_service import Service, lease, retry_match
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

    def test_retry_match_only_updates_existing_target_after_service_lease(self):
        service = Service(self.root)
        target, other = self.endings(2).values()
        target.update(state='review_failed', next_check_epoch=10**10, attempts=2,
                      failures=2, retry_failure_streak=2, error='FileExistsError')
        other.update(state='queued', next_check_epoch=0)
        report = service.folder / 'matches/12345/old/index.html'
        report.parent.mkdir(parents=True)
        report.write_bytes(b'previous report')
        target['report'] = str(report)
        service.state['tasks'] = {'12345': target, '12346': other}
        service.state['known_matches'] = ['12345', '12346']
        service.save()
        before = service.state_path.read_bytes()
        runner = Mock(side_effect=lambda task, **_: {**task, 'state': 'waiting_replay', 'error': None})

        def make_service(root):
            return Service(root, runner=runner, check=lambda: None, now=lambda: self.clock)

        with patch('dota2_insights.automatic_economy_service.Service', side_effect=make_service) as open_service, \
                patch.object(Service, 'discover', side_effect=AssertionError('unexpected discovery')):
            with lease(self.root):
                self.assertEqual(retry_match(self.root, '12345'), 'service_busy')
            open_service.assert_not_called()
            self.assertEqual(service.state_path.read_bytes(), before)
            self.assertEqual(retry_match(self.root, '12347'), 'not_queued')
            self.assertEqual(service.state_path.read_bytes(), before)
            self.assertEqual(retry_match(self.root, '12345'), 'waiting_replay')

        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0]['match_id'], '12345')
        after = a.read_json(service.state_path)
        self.assertEqual(after['known_matches'], ['12345', '12346'])
        self.assertEqual(after['tasks']['12346'], other)
        recovered = after['tasks']['12345']
        self.assertEqual(recovered['failures'], 2)
        self.assertEqual(recovered['retry_failure_streak'], 0)
        self.assertEqual(recovered['attempts'], 3)
        self.assertEqual(report.read_bytes(), b'previous report')

    def test_live_source_index_lock_waits_briefly_without_counting_another_failure(self):
        from dota2_insights.match_journal_index import index_destination
        def runner(task, **_):
            with index_destination(self.root, {}, 'equipment-handoff'):
                return {**task, 'state':'waiting_sources_or_comparable_records', 'error':None}
        service = Service(self.root, runner=runner, check=lambda:None, now=lambda:self.clock)
        found = self.endings(1)
        with patch.object(a, 'endings', return_value=found):
            service.discover()
            task = service.state['tasks']['12345']
            task.update(failures=3, retry_failure_streak=2, error='ValueError', report=str(
                service.folder/'matches/12345/previous/index.html'))
            previous_report = task['report']
            with index_destination(self.root, {}, 'equipment-handoff'):
                service.cycle()
                self.assertEqual(task['state'], 'waiting_source_index')
                self.assertEqual(task['failures'], 3)
                self.assertEqual(task['retry_failure_streak'], 2)
                self.assertEqual(task['next_check_epoch']-self.clock, 30)
                self.assertEqual(task['report'], previous_report)
                self.assertEqual(task['error'], 'ValueError')
                self.assertEqual(a.read_json(service.folder/'queue.json')['tasks']['12345'], task)
                self.assertIn('来源索引正被占用', (service.folder/'index.html').read_text(encoding='utf-8'))
            self.clock += 30
            service.cycle()
            recovered = service.state['tasks']['12345']
            self.assertEqual(recovered['state'], 'waiting_sources_or_comparable_records')
            self.assertEqual(recovered['failures'], 3)
            self.assertEqual(recovered['retry_failure_streak'], 0)
            self.assertIsNone(recovered['error'])

    def test_unrelated_file_collision_remains_a_real_failure(self):
        service = Service(self.root, runner=Mock(side_effect=FileExistsError('output_collision')),
                          check=lambda:None, now=lambda:self.clock)
        with patch.object(a, 'endings', return_value=self.endings(1)):
            service.cycle()
        task = service.state['tasks']['12345']
        self.assertEqual(task['state'], 'review_failed')
        self.assertEqual(task['error'], 'FileExistsError')
        self.assertEqual(task['failures'], 1)
        self.assertEqual(task['next_check_epoch']-self.clock, 600)

    def test_completed_reviews_recheck_hourly_without_delaying_pending_replays(self):
        found = self.endings(2)
        def run(task, **_):
            state = 'review_complete_evidence_only' if task['match_id'] == '12345' else 'waiting_replay'
            return {**task, 'state': state, 'error': None}

        runner = Mock(side_effect=run)
        service = Service(self.root, runner=runner, check=lambda: None, now=lambda: self.clock)
        with patch.object(a, 'endings', return_value=found):
            service.cycle()
            service.cycle()
            self.assertEqual(service.state['tasks']['12345']['next_check_epoch'], self.clock + 3600)
            self.assertEqual(service.state['tasks']['12346']['next_check_epoch'], self.clock + 600)
            self.clock += 600
            service.cycle()
            self.assertEqual([call.args[0]['match_id'] for call in runner.call_args_list],
                             ['12345', '12346', '12346'])
            self.clock += 3000
            service.cycle()
            service.cycle()
            self.assertEqual([call.args[0]['match_id'] for call in runner.call_args_list[-2:]],
                             ['12346', '12345'])

    def test_recovery_resets_retry_streak_but_preserves_cumulative_failure_history(self):
        calls = [ValueError('first'), ValueError('second'), None, ValueError('new')]
        def runner(task, **_):
            value = calls.pop(0)
            if value:
                raise value
            return {**task,'state':'waiting_sources_or_comparable_records','error':None}
        service = Service(self.root,runner=runner,check=lambda:None,now=lambda:self.clock)
        with patch.object(a,'endings',return_value=self.endings(1)):
            service.cycle()
            task = service.state['tasks']['12345']
            self.assertEqual(task['retry_failure_streak'],1)
            self.clock = task['next_check_epoch']
            service.cycle()
            task = service.state['tasks']['12345']
            self.assertEqual(task['retry_failure_streak'],2)
            self.clock = task['next_check_epoch']
            service.cycle()
            task = service.state['tasks']['12345']
            self.assertEqual(task['failures'],2)
            self.assertEqual(task['retry_failure_streak'],0)
            self.clock = task['next_check_epoch']
            service.cycle()
            task = service.state['tasks']['12345']
            self.assertEqual(task['failures'],3)
            self.assertEqual(task['retry_failure_streak'],1)
            self.assertEqual(task['next_check_epoch']-self.clock,600)

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

        first = Path(task['archive'])
        alternative = shutil.copytree(first, self.root / 'diagnostics/alternate-archive')
        samples = first / 'hero_samples.jsonl'
        raw = samples.read_bytes()
        damaged = b'?' + raw[1:]
        samples.write_bytes(damaged)
        config = {'archive_directories': [str(alternative)]}
        a.archive_fingerprints(alternative.resolve(), '12345', hashlib.sha256(replay.read_bytes()).hexdigest(),
                               lambda: None)
        with patch.object(a, 'parse_replay', side_effect=AssertionError('valid alternative exists')):
            second = a.run_task(task, root=self.root, check=lambda: None, config=config)
            self.assertEqual(Path(second['archive']), alternative)
            self.assertEqual(second['state'], 'review_complete_evidence_only')
        self.assertEqual(samples.read_bytes(), damaged)
        (alternative / 'hero_samples.jsonl').unlink()
        with patch.object(a, 'parse_replay', side_effect=generated_parser) as parse:
            task = a.run_task(second, root=self.root, check=lambda: None,
                              config={'archive_directories': [str(first)]})
        parse.assert_called_once()
        self.assertEqual(task['state'], 'review_complete_evidence_only')
        self.assertNotIn(Path(task['archive']), (first, alternative))
        self.assertEqual(samples.read_bytes(), damaged)
        self.assertTrue((alternative / 'manifest.json').is_file())

        with patch.object(a, 'record_review', side_effect=AssertionError('intact report was regenerated')):
            self.assertEqual(a.run_task(task, root=self.root, check=lambda: None)['report'], task['report'])

        # Mutate the actual source while the hashing stage is in progress.
        stable_payload = replay.read_bytes()
        calls = 0
        def change_source():
            nonlocal calls
            calls += 1
            if calls == 2:
                replay.write_bytes(stable_payload + b'changed')
        with patch.object(a, 'parse_replay', side_effect=AssertionError('unstable source was parsed')):
            unstable = a.run_task(task, root=self.root, check=change_source)
        self.assertEqual(unstable['state'], 'replay_review_failed')
        unstable_report = a.read_json(Path(unstable['report']).with_name('report.json'))
        self.assertEqual(unstable_report['replay_comparisons'], [])
        self.assertTrue(unstable_report['records']['equipment'])
        replay.write_bytes(stable_payload)

        stat = replay.stat()
        replay.write_bytes(stable_payload[:-1] + b'?')
        os.utime(replay, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(replay.stat().st_size, stat.st_size)
        self.assertEqual(replay.stat().st_mtime_ns, stat.st_mtime_ns)
        with patch.object(a, 'parse_replay', side_effect=generated_parser) as parse:
            replaced = a.run_task(task, root=self.root, check=lambda: None)
        parse.assert_called_once()
        self.assertEqual(replaced['state'], 'review_complete_evidence_only')
        self.assertNotEqual(replaced['input_signature'], task['input_signature'])

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

    def test_same_inputs_do_not_cache_a_later_failure_or_game_pause_forever(self):
        with patch.object(a, 'inputs', return_value={'gsi': [], 'equipment': []}):
            good = a.run_task({'match_id':'12345','base':str(self.history)}, root=self.root, check=lambda:None)
            for state, error in (('review_failed','FileExistsError'), ('paused_game_running',None),
                                 ('replay_review_failed','ValueError')):
                failed = {**good,'state':state,'error':error}
                with patch.object(a, 'record_review', wraps=a.record_review) as record:
                    retried = a.run_task(failed, root=self.root, check=lambda:None)
                self.assertEqual(record.call_count,1)
                self.assertEqual(retried['state'],'waiting_sources_or_comparable_records')
                self.assertIsNone(retried['error'])
                self.assertNotEqual(retried['report'],good['report'])

    def test_missing_cached_report_is_regenerated_from_current_sources(self):
        with patch.object(a, 'inputs', return_value={'gsi': [], 'equipment': []}):
            task = a.run_task({'match_id':'12345','base':str(self.history)}, root=self.root, check=lambda:None)
            Path(task['report']).unlink()
            with patch.object(a, 'record_review', wraps=a.record_review) as record:
                retried = a.run_task(task, root=self.root, check=lambda:None)
            self.assertEqual(record.call_count,1)
            self.assertTrue(Path(retried['report']).is_file())

    def test_incomplete_or_modified_report_is_not_reused(self):
        with patch.object(a, 'inputs', return_value={'gsi': [], 'equipment': []}):
            task = a.run_task({'match_id': '12345', 'base': str(self.history)}, root=self.root, check=lambda: None)
            for filename in ('report.json', 'REPORT.md', 'index.html'):
                with self.subTest(filename=filename):
                    path = Path(task['report']).with_name(filename)
                    if filename == 'index.html':
                        raw = path.read_bytes()
                        path.write_bytes(b'?' + raw[1:])  # Same byte length.
                    else:
                        path.unlink()
                    previous = task['report']
                    task = a.run_task(task, root=self.root, check=lambda: None)
                    self.assertNotEqual(task['report'], previous)
                    self.assertEqual(task['state'], 'waiting_sources_or_comparable_records')
                    for name in ('report.json', 'REPORT.md', 'index.html'):
                        self.assertTrue(Path(task['report']).with_name(name).is_file())
            with patch.object(a, 'record_review', side_effect=AssertionError('intact report was regenerated')):
                self.assertEqual(a.run_task(task, root=self.root, check=lambda: None)['report'], task['report'])
