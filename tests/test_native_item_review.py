"""Synthetic native journals and coloured PNGs; never gameplay truth labels."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from PIL import Image

from dota2_insights.item_review_evidence import prepare_observation
from dota2_insights.item_review_priorities import ItemReviewPriorities
from dota2_insights.observation_index import read_index
from tools.prepare_record_item_review import prepare_report
from tools.review_recordings import run


class NativeItemReviewTests(unittest.TestCase):
    def continuity_rows(self):
        first = deepcopy(self.f.row)
        first['attempt']['hero']['slots'][0].update(status='known', item_id='blink', candidate='blink')
        second = deepcopy(first)
        second['captured_at_utc'] = second['attempt']['captured_at_utc'] = '2026-10-26T00:00:01Z'
        second['attempt']['hero']['slots'][0].update(status='unknown', item_id=None, candidate=None)
        third = deepcopy(first)
        third['captured_at_utc'] = third['attempt']['captured_at_utc'] = '2026-10-26T00:00:02Z'
        raw = b''.join((json.dumps(r) + '\n').encode() for r in (first, second, third))
        self.f.write_payload(raw)
        self.f.manifest['accepted_observations_at_cutoff'] = 3
        self.f.manifest['payload']['rows'] = 3
        self.f.write_manifest()

    def test_continuity_cli_prepares_unknown_without_candidate_and_recovery(self):
        self.continuity_rows()
        path, _, _ = self.report()
        result = prepare_report(path, root=self.project, roots=self.roots, continuity=True)
        self.assertEqual(result['prepared'], 4)
        page = next(Path(result['output']).glob('continuity-*/index.html'))
        receipt = json.loads(page.with_name('evidence.json').read_text(encoding='utf8'))
        self.assertEqual({r['role'] for r in receipt['entries']},
                         {'known', 'first_unknown', 'last_unknown', 'next_known'})
        self.assertTrue(receipt['continuity']['episodes'][0]['next_known_same_item'])
        self.assertIsNone(next(r for r in receipt['entries'] if r['role']=='first_unknown')['candidate'])
        for r in receipt['entries']:
            with Image.open(page.parent / r['image']) as crop:
                self.assertEqual(crop.getpixel((0, 0)), (0, 0, 255))
        self.assertIn('屏幕实际显示未核验', page.read_text(encoding='utf8'))

    def test_continuity_recomputes_events_instead_of_trusting_edited_report(self):
        self.continuity_rows()
        path, _, _ = self.report()
        report = json.loads(path.read_text(encoding='utf8'))
        report['equipment_reports'][0]['equipment_continuity']['episodes'][0]['last_known_item'] = 'rapier'
        path.write_text(json.dumps(report), encoding='utf8')
        result = prepare_report(path, root=self.project, roots=self.roots, continuity=True)
        receipt = json.loads(next(Path(result['output']).glob('continuity-*/evidence.json')).read_text(encoding='utf8'))
        self.assertEqual(receipt['continuity']['episodes'][0]['last_known_item'], 'blink')

    def test_continuity_wrong_image_sha_is_unavailable_not_successful_crop(self):
        self.continuity_rows()
        path, _, _ = self.report()
        self.image.write_bytes(b'changed')
        result = prepare_report(path, root=self.project, roots=self.roots, continuity=True)
        self.assertEqual(result['prepared'], 0)
        self.assertEqual(result['unavailable'], 4)

    def test_continuity_source_changes_during_crop_publish_no_success_receipt(self):
        from unittest.mock import patch
        from dota2_insights.equipment_continuity_evidence import prepare
        from dota2_insights.item_review_evidence import slot_crop
        self.continuity_rows()
        _, _, entry = self.report()
        output = self.project / 'diagnostics/continuity-changed'
        changed = False
        def mutate(*args):
            nonlocal changed
            result = slot_crop(*args)
            if not changed:
                changed = True
                self.f.source.write_bytes(self.f.source.read_bytes() + b'\n')
            return result
        with patch('dota2_insights.equipment_continuity_evidence.slot_crop', side_effect=mutate):
            with self.assertRaises(ValueError):
                prepare(entry, self.roots, {'blink': {'cost': 2250}}, output)
        self.assertFalse((output / 'evidence.json').exists())
        self.assertFalse((output / 'index.html').exists())

    def setUp(self):
        from test_observation_index import EquipmentIndexTests
        self.index_fixture = EquipmentIndexTests()
        self.index_fixture.setUp()
        self.addCleanup(self.index_fixture.doCleanups)
        self.f = self.index_fixture.fixture
        self.project, self.roots = self.index_fixture.project, self.index_fixture.roots
        self.image = self.f.source.parent / 'hud-000001.png'
        image = Image.new('RGB', (80, 40), 'red')
        image.paste('blue', (10, 10, 30, 20))
        image.save(self.image)
        self.f.row.update(hud_image_sha256=hashlib.sha256(self.image.read_bytes()).hexdigest(),
            frame_geometry={'width': 80, 'height': 40, 'origin_left': 100, 'origin_top': 200,
                            'client_width': 300, 'client_height': 300})
        self.f.row['attempt']['hero']['slots'] = [{'slot_id': 'main_1', 'status': 'unknown',
            'item_id': None, 'candidate': 'item_blink', 'candidate_margin': .05, 'rect': [110, 210, 20, 10]}]
        self.f.write_payload()
        raw = json.dumps({'blink': {'cost': 2250}, 'branches': {'cost': 50}}).encode()
        (self.project / 'data/items.json').write_bytes(raw)
        (self.project / 'data/items.meta.json').write_text(json.dumps({'cache_sha256': hashlib.sha256(raw).hexdigest()}))

    def report(self):
        result = run(self.project, roots=self.roots)
        path = Path(result['report']).parent / 'report.json'
        report = json.loads(path.read_text(encoding='utf-8'))
        entry = read_index(self.project / 'diagnostics/equipment-handoff/index.json')['entries'][0]
        return path, report['equipment_reports'][0], entry

    def test_pipeline_emits_bound_priority_and_prepares_correct_colour_crop(self):
        path, report, _ = self.report()
        priorities = report['item_review_priorities']
        self.assertEqual(len(priorities), 1)
        self.assertEqual(priorities[0]['candidate_item'], 'blink')
        self.assertFalse(priorities[0]['identity_or_item_confirmed'])
        line = self.f.source.read_bytes()
        self.assertEqual(priorities[0]['first_evidence']['line_sha256'], hashlib.sha256(line).hexdigest())
        result = prepare_report(path, root=self.project, roots=self.roots)
        self.assertEqual(result['prepared'], 1)  # Same first/last row is not duplicated.
        self.assertEqual(result['equipment_packages'], 1)
        output = next(Path(result['output']).glob('equipment-*'))
        evidence = json.loads((output / 'evidence.json').read_text(encoding='utf-8'))
        entry = evidence['entries'][0]
        self.assertEqual(entry['crop_rect'], [10, 10, 20, 10])
        with Image.open(output / entry['image']) as crop:
            self.assertEqual(crop.size, (20, 10))
            self.assertEqual(crop.getpixel((0, 0)), (0, 0, 255))
        self.assertFalse(entry['image_semantics_verified'])
        self.assertFalse(evidence['visual_verdicts_entered'])
        self.assertTrue(evidence['source_verified_before_and_after'])

    def test_first_and_last_distinct_rows_keep_original_pngs(self):
        second = deepcopy(self.f.row)
        image = Image.new('RGB', (80, 40), 'green')
        second_path = self.image.with_name('hud-000002.png')
        image.save(second_path)
        second.update(hud_image=second_path.name, hud_image_sha256=hashlib.sha256(second_path.read_bytes()).hexdigest(),
                      captured_at_utc='2026-10-26T00:00:01Z')
        second['attempt']['captured_at_utc'] = second['captured_at_utc']
        raw = b''.join((json.dumps(row) + '\n').encode() for row in (self.f.row, second))
        self.f.write_payload(raw)
        self.f.manifest['accepted_observations_at_cutoff'] = 2
        self.f.manifest['payload']['rows'] = 2
        self.f.write_manifest()
        path, report, _ = self.report()
        self.assertEqual(report['item_review_priorities'][0]['observations'], 2)
        result = prepare_report(path, root=self.project, roots=self.roots)
        self.assertEqual(result['prepared'], 2)
        evidence_path = next(Path(result['output']).glob('equipment-*/evidence.json'))
        entries = json.loads(evidence_path.read_text(encoding='utf-8'))['entries']
        colours = []
        for entry in entries:
            with Image.open(evidence_path.parent / entry['image']) as crop:
                colours.append(crop.getpixel((0, 0)))
        self.assertEqual(colours, [(0, 0, 255), (0, 128, 0)])

    def test_changed_image_is_unavailable_and_never_replaced_with_another_frame(self):
        path, _, _ = self.report()
        Image.new('RGB', (80, 40), 'green').save(self.image)
        result = prepare_report(path, root=self.project, roots=self.roots)
        self.assertEqual(result['prepared'], 0)
        self.assertEqual(result['unavailable'], 1)
        evidence_path = next(Path(result['output']).glob('equipment-*/evidence.json'))
        evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
        self.assertEqual(evidence['entries'][0]['reason'], 'item_review_image_sha_mismatch')

    def test_missing_geometry_does_not_guess_origin(self):
        self.f.row['frame_geometry'].pop('origin_top')
        self.f.write_payload()
        path, _, _ = self.report()
        result = prepare_report(path, root=self.project, roots=self.roots)
        self.assertEqual(result['prepared'], 0)
        self.assertEqual(result['unavailable'], 1)

    def test_forged_line_hash_or_candidate_never_gets_success_manifest(self):
        _, report, entry = self.report()
        for mode in ('line', 'candidate', 'source'):
            priorities = deepcopy(report['item_review_priorities'])
            if mode == 'line':
                priorities[0]['first_evidence']['line_sha256'] = '0' * 64
            elif mode == 'candidate':
                priorities[0]['candidate_item'] = 'black_king_bar'
            else:
                priorities[0]['first_evidence']['source']['path'] = 'config/private.local.json'
            output = self.project / 'diagnostics' / ('bad-' + mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                prepare_observation(entry, self.roots, priorities, output)
            self.assertFalse((output / 'evidence.json').exists())

    def test_foreign_report_receipt_and_history_output_refused(self):
        path, report, entry = self.report()
        bad = json.loads(path.read_text(encoding='utf-8'))
        bad['equipment_reports'][0]['package_id'] = '0' * 64
        path.write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError, 'not_indexed_or_changed'):
            prepare_report(path, root=self.project, roots=self.roots)
        with self.assertRaisesRegex(ValueError, 'output_overlaps_history'):
            prepare_observation(entry, self.roots, report['item_review_priorities'], self.f.root / 'new-output')

    def test_cost_and_margin_rules_sample_for_review_not_confirm_identity(self):
        queue = ItemReviewPriorities({'blink': {'cost': 2250}, 'branches': {'cost': 50}})
        row = deepcopy(self.f.row)
        slot = row['attempt']['hero']['slots'][0]
        slot.update(status='known', item_id='item_blink', candidate_margin=.5)
        queue.add('12345', row, {'line': 1})
        slot.update(status='unknown', candidate='item_branches')
        queue.add('12345', row, {'line': 2})
        self.assertEqual(queue.result(), [])
        slot.update(status='known', item_id='item_blink', candidate_margin=.05)
        queue.add('12345', row, {'line': 3})
        self.assertEqual(queue.result()[0]['reason'], 'small_recorded_matcher_margin')
        self.assertFalse(queue.result()[0]['identity_or_item_confirmed'])
