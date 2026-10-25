import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.replay_workbench import ReplayQueue, export_event_timeline, source_identity


class Child:
    pid = 123
    code = None
    killed = False

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True
        self.code = -1


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.child, self.now, self.busy, self.calls = Child(), 0, False, []
        self.lock = patch('dota2_insights.replay_holdout.refuse_locked_match')
        self.lock.start()
        self.addCleanup(self.lock.stop)
        self.ready = patch('dota2_insights.replay_workbench.dependency_status', return_value=None)
        self.ready.start()
        self.addCleanup(self.ready.stop)

    def source(self, mid='123'):
        p = self.root / (mid + '.dem')
        p.write_bytes(b'PBDEMS2\0' + b'x' * 16)
        return p

    def queue(self):
        def spawn(*args, **kwargs):
            self.calls.append((args, kwargs))
            return self.child
        return ReplayQueue(self.root, probe=lambda: self.busy, spawn=spawn, clock=lambda: self.now,
                           pid_probe=lambda pid: False)

    def finish(self, q, *, complete=True, mid='123', code=0):
        (q.folder / q.active['id'] / 'result.json').write_text(json.dumps(
            {'complete': complete, 'match_id': mid, 'stop_reason': 'replay_eof'}), encoding='utf-8')
        self.child.code = code
        q.poll()

    def test_explicit_start_and_duplicate_queue(self):
        q = self.queue()
        p = self.source()
        self.assertTrue(q.add(p))
        self.assertFalse(q.add(p))
        q.poll()
        self.assertFalse(self.calls)
        q.start()
        q.poll()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(q.jobs[0]['state'], 'running')
        self.finish(q)

    def test_game_wait_and_game_start_cancel_then_owned_kill(self):
        q = self.queue()
        q.add(self.source())
        q.start()
        self.busy = True
        q.poll()
        self.assertFalse(self.calls)
        self.busy = False
        q.poll()
        self.busy = True
        q.poll()
        self.assertEqual(q.jobs[0]['state'], 'stopping')
        self.assertTrue((q.folder / q.active['id'] / 'cancel.request').exists())
        self.now = 9
        q.poll()
        self.assertTrue(self.child.killed)
        q.poll()
        self.assertEqual(q.jobs[0]['state'], 'interrupted')
        self.assertFalse(q.enabled)

    def test_missing_or_wrong_receipt_never_completed(self):
        for bad_match in ('999', None):
            with self.subTest(bad_match=bad_match):
                q = self.queue()
                q.add(self.source())
                q.start()
                q.poll()
                self.finish(q, mid=bad_match)
                self.assertNotEqual(q.jobs[-1]['state'], 'completed')
                self.child.code = None

    def test_partial_exit_keeps_task_incomplete(self):
        q = self.queue()
        q.add(self.source())
        q.start()
        q.poll()
        self.finish(q, complete=False, code=2)
        self.assertEqual(q.jobs[0]['state'], 'incomplete')

    def test_source_change_and_missing_environment_do_not_spawn(self):
        q = self.queue()
        p = self.source()
        q.add(p)
        p.write_bytes(b'not_a_demo' * 3)
        q.start()
        q.poll()
        self.assertEqual(q.jobs[0]['state'], 'failed')
        self.assertFalse(self.calls)
        with patch('dota2_insights.replay_workbench.dependency_status', return_value='missing'):
            with self.assertRaisesRegex(ValueError, 'missing'):
                q.start()

    def test_process_probe_error_is_fail_closed(self):
        q = self.queue()
        q.add(self.source())
        q.start()
        q.probe = lambda: (_ for _ in ()).throw(OSError())
        q.poll()
        self.assertFalse(self.calls)

    def test_shutdown_marks_interrupted_even_if_result_arrives(self):
        q = self.queue()
        q.add(self.source())
        q.start()
        q.poll()
        q.stop()
        self.finish(q)
        self.assertEqual(q.jobs[0]['state'], 'interrupted')

    def test_restart_never_auto_retries_active_jobs(self):
        q = self.queue()
        q.add(self.source())
        q.start()
        q.poll()
        restored = self.queue()
        self.assertEqual(restored.jobs[0]['state'], 'interrupted')
        restored.poll()
        self.assertEqual(len(self.calls), 1)
        restored.pid_probe = lambda pid: True
        with self.assertRaisesRegex(ValueError, '尚未确认退出'):
            restored.start()
        restored.pid_probe = lambda pid: False
        restored.start()
        self.finish(q)

    def test_sealed_identity_refused_before_file_read(self):
        path = self.source()
        with patch('dota2_insights.replay_holdout.refuse_locked_match', side_effect=ValueError('sealed')):
            with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
                with self.assertRaisesRegex(ValueError, 'sealed'):
                    source_identity(path)

    def archive(self, events):
        d = self.root / 'archive'
        d.mkdir()
        source = d / 'events.jsonl'
        source.write_bytes(b''.join(json.dumps(r).encode() + b'\n' for r in events))
        (d / 'manifest.json').write_text(json.dumps({'schema_version': 1,
            'kind': 'offline_replay_archive', 'match_id': '123', 'source_sha256': 'a' * 64,
            'export_complete': True, 'stop_reason': 'replay_eof', 'parse_error': None,
            'source_unchanged_stat': True,
            'files': {'events.jsonl': {'bytes': source.stat().st_size,
                                      'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}}))
        return d

    def event(self, tick, clock):
        return {'kind': 'combat_event', 'match_id': '123', 'tick': tick, 'game_time_s': clock,
                'game_time_basis': 'packet_clock', 'event_type': 'ABILITY',
                'attacker': 'npc_dota_hero_sven', 'inflictor': 'sven_storm_bolt'}

    def test_timeline_preserves_clock_regression_and_refs(self):
        d = self.archive([self.event(10, 3), self.event(11, 2), self.event(12, 4)])
        output = self.root / 'timeline.jsonl'
        result = export_event_timeline(d, output)
        rows = [json.loads(l) for l in output.read_text().splitlines()]
        self.assertEqual([r['game_time_s'] for r in rows], [3, 2, 4])
        self.assertEqual([r['tick_epoch'] for r in rows], [0, 1, 1])
        self.assertEqual(rows[1]['source_ref'], 'events.jsonl:2')
        self.assertEqual(result['rows'], 3)
        self.assertNotIn('damage', rows[0]['details'])
        with self.assertRaises(FileExistsError):
            export_event_timeline(d, output)

    def test_timeline_rejects_changed_source_or_mixed_match(self):
        d = self.archive([dict(self.event(1, 1), match_id='456')])
        with self.assertRaisesRegex(ValueError, 'identity'):
            export_event_timeline(d, self.root / 'mixed.jsonl')
        with (d / 'events.jsonl').open('ab') as s:
            s.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            export_event_timeline(d, self.root / 'changed.jsonl')

    def test_timeline_keeps_presence_raw_visibility_and_two_source_clocks_separate(self):
        event = {**self.event(6516, .133), 'event_tick': 6516, 'packet_tick': 6516,
            'event_game_time_s': 0, 'target_is_hero': False, 'target_is_hero_present': False,
            'target_is_illusion': False, 'target_is_illusion_present': True,
            'visible_radiant_raw': True, 'visible_dire_raw': None}
        d = self.archive([event])
        result = export_event_timeline(d, self.root / 'presence.jsonl')
        row = json.loads((self.root / 'presence.jsonl').read_text())
        self.assertEqual((result['schema_version'], row['schema_version']), (2, 2))
        self.assertIsNone(row['details']['target_is_hero'])
        self.assertIs(row['source_flags']['target_is_hero'], False)
        self.assertIs(row['source_flags']['target_is_hero_present'], False)
        self.assertIs(row['details']['target_is_illusion'], False)
        self.assertEqual((row['game_time_s'], row['source_clocks']['event_game_time_s']), (.133, 0))
        self.assertEqual(row['replay_visibility_raw'], {'radiant': True, 'dire': None})

    def test_legacy_or_invalid_presence_never_becomes_negative_hero_evidence(self):
        events = [{**self.event(1, 1), 'target_is_hero': False},
                  {**self.event(2, 2), 'target_is_hero': True, 'target_is_hero_present': 1},
                  {**self.event(3, 3), 'target_is_hero': True, 'target_is_hero_present': True}]
        d = self.archive(events)
        export_event_timeline(d, self.root / 'legacy-flags.jsonl')
        rows = [json.loads(line) for line in (self.root / 'legacy-flags.jsonl').read_text().splitlines()]
        self.assertEqual([row['details']['target_is_hero'] for row in rows], [None, None, True])

    def test_timeline_cancellation_preserves_source(self):
        d = self.archive([self.event(1, 1)])
        before = (d / 'events.jsonl').read_bytes()
        with self.assertRaisesRegex(ValueError, 'cancel'):
            export_event_timeline(d, self.root / 'cancel.jsonl',
                guard=lambda: (_ for _ in ()).throw(ValueError('cancel')))
        self.assertEqual(before, (d / 'events.jsonl').read_bytes())

    def test_reincarnation_presence_survives_timeline_without_inventing_legacy_flags(self):
        events = [dict(self.event(1, 1), event_type='DEATH'),
                  dict(self.event(2, 2), event_type='DEATH', will_reincarnate=True,
                       will_reincarnate_present=True, will_reincarnate_decoder_value=True,
                       will_reincarnate_basis='explicit_s2_protobuf_true'),
                  dict(self.event(3, 3), event_type='DEATH', will_reincarnate=None,
                       will_reincarnate_present=False, will_reincarnate_decoder_value=False,
                       will_reincarnate_basis='absent_s2_protobuf_field')]
        d = self.archive(events)
        output = self.root / 'reincarnation.jsonl'
        export_event_timeline(d, output)
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual([r['details']['will_reincarnate'] for r in rows], [None, True, None])
        self.assertEqual([r['source_flags']['will_reincarnate_present'] for r in rows], [None, True, False])
        self.assertEqual([r['source_flags']['will_reincarnate_decoder_value'] for r in rows], [None, True, False])
        self.assertEqual(rows[2]['details']['will_reincarnate_basis'], 'absent_s2_protobuf_field')

    def worker_job(self):
        from tools import run_replay_job
        self.job = self.root / 'diagnostics/replay-workbench' / ('a' * 32)
        self.job.mkdir(parents=True)
        (self.root / '.replay-deps/gem').mkdir(parents=True)
        self.root_patch = patch.object(run_replay_job, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(lambda: __import__('sys').path.remove(str(self.root / '.replay-deps'))
                        if str(self.root / '.replay-deps') in __import__('sys').path else None)
        return run_replay_job

    def test_worker_checks_game_and_footer_before_full_parser(self):
        worker = self.worker_job()
        source = self.source()
        with patch('dota2_insights.replay_archive.parse_archive') as parse, \
                patch('dota2_insights.replay_archive._footer_match_id', return_value='456') as footer:
            with patch('dota2_insights.replay_workbench.game_running', return_value=True):
                with self.assertRaisesRegex(ValueError, 'game_running'):
                    worker.run(source, self.job)
                footer.assert_not_called()
            with patch('dota2_insights.replay_workbench.game_running', return_value=False):
                with self.assertRaisesRegex(ValueError, 'declared_match'):
                    worker.run(source, self.job)
            parse.assert_not_called()

    def test_worker_only_delivers_complete_receipt_after_timeline(self):
        worker = self.worker_job()
        source = self.source()
        d = self.archive([self.event(1, 1)])
        d.rename(self.job / 'archive')
        manifest = {'export_complete': True, 'stop_reason': 'replay_eof', 'parse_error': None,
                    'match_id': '123', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        archive_manifest = self.job / 'archive' / 'manifest.json'
        archive_manifest.write_text(json.dumps({**json.loads(archive_manifest.read_text()), **manifest}))
        with patch('dota2_insights.replay_workbench.game_running', return_value=False), \
                patch('dota2_insights.replay_archive._footer_match_id', return_value='123'), \
                patch('dota2_insights.replay_archive.parse_archive', return_value=manifest):
            self.assertEqual(worker.run(source, self.job), 0)
        result = json.loads((self.job / 'result.json').read_text())
        self.assertTrue(result['complete'])
        self.assertEqual(result['timeline']['rows'], 1)
        self.assertIsNotNone(result['fact_review'])
        self.assertTrue((self.job / 'fact-review/report.html').is_file())

    def test_worker_partial_export_has_no_complete_timeline(self):
        worker = self.worker_job()
        manifest = {'export_complete': False, 'stop_reason': 'output_size_limit',
                    'match_id': '123', 'source_sha256': 'a' * 64}
        with patch('dota2_insights.replay_workbench.game_running', return_value=False), \
                patch('dota2_insights.replay_archive._footer_match_id', return_value='123'), \
                patch('dota2_insights.replay_archive.parse_archive', return_value=manifest):
            self.assertEqual(worker.run(self.source(), self.job), 2)
        result = json.loads((self.job / 'result.json').read_text())
        self.assertFalse(result['complete'])
        self.assertIsNone(result['timeline'])
        self.assertIsNone(result['fact_review'])

    def test_worker_review_failure_never_writes_a_complete_receipt(self):
        worker = self.worker_job()
        manifest = {'export_complete': True, 'stop_reason': 'replay_eof', 'parse_error': None,
                    'match_id': '123', 'source_sha256': 'a' * 64}
        with patch('dota2_insights.replay_workbench.game_running', return_value=False), \
                patch('dota2_insights.replay_archive._footer_match_id', return_value='123'), \
                patch('dota2_insights.replay_archive.parse_archive', return_value=manifest), \
                patch('dota2_insights.replay_workbench.export_event_timeline', return_value={'rows': 1}), \
                patch('dota2_insights.replay_fact_review.review_archive', side_effect=ValueError('review_failed')):
            with self.assertRaisesRegex(ValueError, 'review_failed'):
                worker.run(self.source(), self.job)
        self.assertFalse((self.job / 'result.json').exists())


if __name__ == '__main__':
    unittest.main()
