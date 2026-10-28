"""Synthetic helper records only; no real game, images, audio or helper code."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from dota2_insights import helper_handoff as handoff


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.history, self.insights = self.base / 'helper-history', self.base / 'insights'
        self.history.mkdir()
        self.insights.mkdir()
        self.roots = {'helper_history': self.history}
        self.registry = self.history / ('field-recording/run_' + 'a' * 32) / 'recorders.json'
        self.rid, self.sid, self.gid = 'c' * 32, 'run_' + 'b' * 32, 'game_' + 'd' * 32
        self.document = {'schema_version': 1, 'scope': 'launcher_recording_status_only', 'closed': True,
            'started_at_utc': '2026-10-25T09:00:00Z', 'closed_at_utc': '2026-10-25T10:00:00Z',
            'game_sessions': {self.gid: {'record_session_id': self.sid, 'match_id': '12345'}},
            'observed_match_ids': ['12345'], 'local_run_folders': {},
            'recorders': {'self_recording': {'run_id': self.rid, 'shutdown_complete': True}}}
        self.folder = self.history / 'self-economy' / ('self-economy-' + self.rid)
        self.summary = self.folder / 'recording-summary.json'
        self.journal = self.folder / 'samples.jsonl'
        self.closed = {'run_id': self.rid, 'closed_at_utc': '2026-10-25T09:59:59Z',
            'evidence_queue_drained': True, 'run_recording_status': {'pending': 0, 'inflight': 0, 'accepting': False}}
        self.row = {'match_id': '12345', 'record_session_id': self.sid, 'game_session_id': self.gid,
            'received_at_utc': '2026-10-25T09:59:00Z',
            'map_context': {'game_state': 'DOTA_GAMERULES_STATE_POST_GAME'},
            'self_economy': {'amount': 3000}, 'secret': 'MUST_NOT_BE_COPIED', 'screenshot_base64': 'PRIVATE_PIXELS'}
        self.write(self.registry, self.document)
        self.write(self.summary, self.closed)
        self.journal.write_text(json.dumps(self.row) + '\n', encoding='utf-8')

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def inspect(self):
        return handoff.inspect_registry('helper_history', self.history, self.registry, quiet_seconds=0)

    def test_first_context_bound_row_checks_holdout_without_top_level_match(self):
        self.journal.write_text(json.dumps({"game_context_binding": {
            "context": {"match_id": "12345"}}}) + "\n", encoding="utf-8")
        reader = handoff.Inputs("helper_history", self.history, quiet_seconds=0)
        with patch("dota2_insights.replay_holdout.refuse_locked_match") as guard:
            reader.read(self.journal, mode="jsonl")
        guard.assert_any_call("12345")
        reader = handoff.Inputs("helper_history", self.history, quiet_seconds=0)
        with patch("dota2_insights.replay_holdout.refuse_locked_match", side_effect=ValueError("sealed")):
            with self.assertRaisesRegex(ValueError, "sealed"):
                reader.read(self.journal, mode="jsonl")

    def test_closed_drained_sources_index_without_copying_payloads(self):
        result = self.inspect()
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['matches'][0]['match_end_evidence'], 'own_recorded_gsi_post_game')
        raw = json.dumps(result)
        self.assertNotIn('MUST_NOT_BE_COPIED', raw)
        self.assertNotIn('PRIVATE_PIXELS', raw)
        self.assertFalse(result['binary_assets_verified'])
        self.assertTrue(handoff.verify_package(result, self.roots))

    def test_recording_closed_does_not_prove_match_ended(self):
        self.row['map_context']['game_state'] = 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'
        self.journal.write_text(json.dumps(self.row) + '\n', encoding='utf-8')
        result = self.inspect()
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['matches'][0]['match_end_evidence'], 'not_observed')

    def test_live_registry_does_not_open_payloads(self):
        self.document['closed'] = False
        self.write(self.registry, self.document)
        self.journal.unlink()
        result = self.inspect()
        self.assertEqual(result['state'], 'waiting')
        self.assertEqual(len(result['sources']), 1)
        with self.assertRaisesRegex(ValueError, 'not_ready'):
            handoff.verify_package(result, self.roots)

    def test_false_drain_and_pending_or_accepting_writer_are_blocked(self):
        for change in ('drain', 'pending', 'accepting', 'boolean_pending'):
            value = copy.deepcopy(self.closed)
            if change == 'drain': value['evidence_queue_drained'] = False
            if change == 'pending': value['run_recording_status']['pending'] = 1
            if change == 'accepting': value['run_recording_status']['accepting'] = True
            if change == 'boolean_pending': value['run_recording_status']['pending'] = False
            self.write(self.summary, value)
            with self.subTest(change=change):
                self.assertEqual(self.inspect()['state'], 'blocked')

    def test_partial_tail_is_not_imported_as_complete(self):
        self.journal.write_text(json.dumps(self.row), encoding='utf-8')
        result = self.inspect()
        self.assertEqual(result['state'], 'blocked')
        self.assertIn('handoff_journal_partial_line', result['components'][0]['issues'])

    def test_wrong_self_run_and_foreign_match_are_blocked(self):
        self.closed['run_id'] = 'another_run'
        self.write(self.summary, self.closed)
        self.assertEqual(self.inspect()['state'], 'blocked')
        self.closed['run_id'] = self.rid
        self.write(self.summary, self.closed)
        self.row['match_id'] = '99999'
        self.journal.write_text(json.dumps(self.row) + '\n', encoding='utf-8')
        self.assertEqual(self.inspect()['state'], 'blocked')

    def test_known_match_with_a_different_game_session_or_run_is_refused(self):
        for key, value in [('game_session_id', 'game_' + 'f' * 32),
                           ('record_session_id', 'run_' + 'f' * 32), ('run_id', 'another')]:
            row = dict(self.row, **{key: value})
            self.journal.write_text(json.dumps(row) + '\n', encoding='utf-8')
            with self.subTest(key=key):
                self.assertEqual(self.inspect()['state'], 'blocked')

    def test_audio_requires_terminal_flags_even_if_launcher_acknowledges_shutdown(self):
        rid = 'run_' + 'e' * 32
        self.document['recorders']['audio_samples'] = {'run_id': rid, 'shutdown_complete': True}
        self.write(self.registry, self.document)
        folder = self.history / 'match-audio' / rid
        summary = {'run_id': rid, 'running': False, 'worker_alive': False, 'finalizing': False}
        self.write(folder / 'summary.json', summary)
        (folder / 'segments.jsonl').write_text('{}\n', encoding='utf-8')
        package = self.inspect()
        self.assertEqual(next(c for c in package['components'] if c['kind'] == 'audio')['state'], 'closed_verified')
        for flag in ('finalizing', 'worker_alive', 'running'):
            value = dict(summary, **{flag: True})
            self.write(folder / 'summary.json', value)
            with self.subTest(flag=flag):
                package = self.inspect()
                self.assertEqual(next(c for c in package['components'] if c['kind'] == 'audio')['state'], 'blocked')
        self.write(folder / 'summary.json', summary)
        self.document['recorders']['audio_samples']['shutdown_complete'] = False
        self.write(self.registry, self.document)
        package = self.inspect()
        self.assertEqual(next(c for c in package['components'] if c['kind'] == 'audio')['state'], 'blocked')

    def test_minimap_without_a_drained_image_queue_is_not_accepted(self):
        rid = 'run_' + 'e' * 32
        self.document['local_run_folders']['minimap'] = [rid]
        self.write(self.registry, self.document)
        folder = self.history / 'minimap-learning' / rid
        summary = {'run_id': rid[4:], 'closed': True, 'complete': True,
            'ended_at_utc': '2026-10-25T09:59:00Z', 'recording_evidence': {'pending_images': 0}}
        self.write(folder / 'session.json', summary)
        (folder / 'samples.jsonl').write_text('{}\n', encoding='utf-8')
        package = self.inspect()
        component = next(c for c in package['components'] if c['kind'] == 'minimap')
        self.assertEqual(component['state'], 'closed_verified')
        self.assertEqual(component['association'], 'launcher_scope_only')
        summary['recording_evidence']['pending_images'] = 1
        self.write(folder / 'session.json', summary)
        package = self.inspect()
        self.assertEqual(next(c for c in package['components'] if c['kind'] == 'minimap')['state'], 'blocked')

    def test_bp_session_identity_is_checked_and_not_guessed_from_time(self):
        sid = 'e' * 32
        self.document['recorders']['draft_recording'] = {'session_id': sid, 'closed': True}
        self.write(self.registry, self.document)
        folder = self.history / 'drafts' / ('session_' + sid)
        summary = {'session_id': sid, 'closed_at_utc': '2026-10-25T09:59:00Z', 'recording': {'closed': True}}
        self.write(folder / 'session-end.json', summary)
        (folder / 'events.jsonl').write_text(json.dumps({'snapshot': {'match_id': '12345'}}) + '\n', encoding='utf-8')
        self.assertEqual(next(c for c in self.inspect()['components'] if c['kind'] == 'bp')['state'], 'closed_verified')
        summary['session_id'] = 'f' * 32
        self.write(folder / 'session-end.json', summary)
        self.assertEqual(next(c for c in self.inspect()['components'] if c['kind'] == 'bp')['state'], 'blocked')

    def test_fps_csv_is_indexed_as_bytes_and_lines_not_as_fps_measurements(self):
        rid = 'e' * 32
        self.document['recorders']['presentmon'] = {'run_id': rid, 'shutdown_complete': True}
        self.write(self.registry, self.document)
        folder = self.history / 'game-frames' / ('run_' + rid)
        self.write(folder / 'capture.json', {'status': 'closed', 'shutdown_complete': True,
                                           'ended_at_utc': '2026-10-25T09:59:00Z'})
        (folder / 'frames.csv').write_text('header\n1,2\n', encoding='utf-8')
        package = self.inspect()
        stats = next(c for c in package['components'] if c['kind'] == 'fps')['journals'][0]['summary']
        self.assertEqual(stats['rows'], 2)
        self.assertIn('not_frame_count', stats['row_meaning'])

    def test_bot_game_keeps_local_identity_without_fabricating_a_match_number(self):
        self.document['game_sessions'][self.gid]['match_id'] = None
        self.document['observed_match_ids'] = []
        self.write(self.registry, self.document)
        self.row['match_id'] = None
        self.journal.write_text(json.dumps(self.row) + '\n', encoding='utf-8')
        result = self.inspect()
        self.assertIsNone(result['matches'][0]['match_id'])
        self.assertEqual(result['matches'][0]['game_session_id'], self.gid)
        self.assertEqual(result['components'][0]['journals'][0]['summary']['unbound_rows'], 1)

    def test_nested_quota_is_preserved_as_a_gap(self):
        self.closed['run_recording_status']['buff_samples'] = {'sessions': {'x': {'limit_reached': True}}}
        self.write(self.summary, self.closed)
        result = self.inspect()
        self.assertEqual(result['state'], 'ready_with_gaps')
        self.assertIn('limit_reached', result['components'][0]['warnings'])

    def test_registry_shutdown_failure_is_not_overruled_by_a_summary(self):
        self.document['recorders']['self_recording']['shutdown_complete'] = False
        self.write(self.registry, self.document)
        self.assertEqual(self.inspect()['state'], 'blocked')

    def test_changed_source_invalidates_a_previously_ready_package(self):
        package = self.inspect()
        self.journal.write_text('{}\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale'):
            handoff.verify_package(package, self.roots)

    def test_source_change_after_reader_finishes_is_detected(self):
        reader = handoff.Inputs('helper_history', self.history, quiet_seconds=0)
        reader.read(self.summary)
        self.summary.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed_after_read'):
            reader.verify()

    def test_unsafe_tokens_and_source_paths_are_refused(self):
        self.document['recorders']['self_recording']['run_id'] = '../escape'
        self.write(self.registry, self.document)
        with self.assertRaisesRegex(ValueError, 'token'):
            self.inspect()
        reader = handoff.Inputs('helper_history', self.history, quiet_seconds=0)
        with self.assertRaisesRegex(ValueError, 'outside_root'):
            reader.read(self.insights / 'foreign.json')

    def test_stored_package_cannot_redirect_reads_outside_its_root(self):
        result = self.inspect()
        result['sources'][0]['path'] = '../elsewhere'
        with self.assertRaisesRegex(ValueError, 'source_path_invalid'):
            handoff.verify_package(result, self.roots)

    def test_stored_package_cannot_add_an_unrelated_private_or_sealed_file(self):
        result = self.inspect()
        source = dict(result['sources'][0], path='ward-independent-validation/sealed-answers.json')
        result['sources'].append(source)
        with patch.object(handoff, '_sha', side_effect=AssertionError('must reject before any body hash')):
            with self.assertRaises(ValueError):
                handoff.verify_package(result, self.roots)

    def test_empty_source_list_and_duplicate_references_do_not_verify(self):
        original = self.inspect()
        for sources in ([], original['sources'] + [original['sources'][0]]):
            result = dict(original, sources=sources)
            with self.assertRaises(ValueError):
                handoff.verify_package(result, self.roots)

    def test_package_identity_and_boolean_match_ids_do_not_verify(self):
        original = self.inspect()
        result = copy.deepcopy(original)
        result['launcher_run_id'] = 'run_' + 'f' * 32
        with self.assertRaises(ValueError):
            handoff.verify_package(result, self.roots)
        result = copy.deepcopy(original)
        result['matches'][0]['match_id'] = True
        with self.assertRaises(ValueError):
            handoff.verify_package(result, self.roots)

    def test_schema_and_utc_validation_do_not_guess(self):
        for key, value in [('schema_version', True), ('started_at_utc', '2026-10-25T09:00:00')]:
            d = copy.deepcopy(self.document)
            d[key] = value
            self.write(self.registry, d)
            with self.assertRaises(ValueError):
                self.inspect()

    def test_malformed_recorder_and_folder_metadata_are_rejected(self):
        for key, value in [('recorders', {'self_recording': ['bad']}), ('local_run_folders', ['bad'])]:
            d = copy.deepcopy(self.document)
            d[key] = value
            self.write(self.registry, d)
            with self.assertRaises(ValueError):
                self.inspect()

    def test_sealed_match_is_refused_before_opening_journals(self):
        with patch('dota2_insights.replay_holdout.refuse_locked_match', side_effect=ValueError('sealed')), \
                patch.object(handoff.Inputs, 'verify', side_effect=AssertionError('should not get to journals')):
            with self.assertRaisesRegex(ValueError, 'sealed'):
                self.inspect()

    def test_repeat_sync_is_idempotent_and_does_not_change_helper_files(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in [self.registry, self.summary, self.journal]}
        first = handoff.sync(self.insights, self.roots, quiet_seconds=0)
        second = handoff.sync(self.insights, self.roots, quiet_seconds=0)
        self.assertFalse(first['runs'][0]['already_indexed'])
        self.assertTrue(second['runs'][0]['already_indexed'])
        self.assertEqual(first['runs'][0]['package_id'], second['runs'][0]['package_id'])
        self.assertEqual(len(list((self.insights / 'diagnostics/helper-handoff/packages').glob('*.json'))), 1)
        for p, value in before.items():
            self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), value)

    def test_new_open_state_replaces_ready_state_in_the_current_index(self):
        handoff.sync(self.insights, self.roots, quiet_seconds=0)
        self.document['closed'] = False
        self.write(self.registry, self.document)
        result = handoff.sync(self.insights, self.roots, quiet_seconds=0)
        self.assertEqual(result['runs'][0]['state'], 'waiting')

    def test_index_cannot_be_written_under_the_history_root(self):
        with self.assertRaises(ValueError):
            handoff.sync(self.history, self.roots, quiet_seconds=0)

    def test_recently_written_registry_waits_instead_of_reading_its_journals(self):
        with patch.object(time, 'time_ns', return_value=self.registry.stat().st_mtime_ns + 1):
            result = handoff.sync(self.insights, self.roots)
        self.assertEqual(result['runs'][0]['state'], 'waiting')

    def test_missing_journal_and_size_limit_are_explicit(self):
        self.journal.unlink()
        self.assertEqual(self.inspect()['state'], 'blocked')
        with patch.object(handoff, 'META_LIMIT', 1):
            with self.assertRaisesRegex(ValueError, 'size_limit'):
                self.inspect()

    def test_release_discovery_uses_only_known_app_layouts(self):
        releases = self.base / 'releases'
        target = releases / ('helper-test/Dota2Helper/diagnostics/field-recording/run_' + 'f' * 32) / 'recorders.json'
        self.write(target, self.document)
        found = handoff.discover({'helper_releases': releases})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][2], target)

    def test_unconfigured_or_missing_roots_are_not_reported_as_an_empty_history(self):
        for roots in ({}, {'helper_history': self.base / 'missing'}):
            with self.assertRaises(ValueError):
                handoff.discover(roots)

    def test_resolved_sealed_target_refused_before_hashing_body(self):
        result = self.inspect()
        target = self.history/'match_9056629627/samples.jsonl'
        target.parent.mkdir()
        target.write_text('SYNTHETIC_SENTINEL_NOT_A_REAL_MATCH',encoding='utf-8')
        original_path = handoff.Inputs.path
        original_sha = handoff._sha
        opened=[]
        def redirected(reader,value):
            return target if str(value).endswith('samples.jsonl') else original_path(reader,value)
        def tracked_sha(path):
            opened.append(Path(path))
            return original_sha(path)
        with patch.object(handoff.Inputs,'path',redirected),patch.object(handoff,'_sha',tracked_sha):
            with self.assertRaisesRegex(ValueError,'封存'):
                handoff.verify_package(result,self.roots)
        self.assertNotIn(target,opened)

    def test_reader_refuses_resolved_sealed_target_before_open(self):
        target = self.history/'match_9056629627/samples.jsonl'
        target.parent.mkdir()
        # The nonexistent target makes an accidental body access observable.
        with patch.object(handoff.Inputs,'path',return_value=target):
            with self.assertRaisesRegex(ValueError,'封存'):
                handoff.Inputs('helper_history',self.history,quiet_seconds=0).read(self.journal)
