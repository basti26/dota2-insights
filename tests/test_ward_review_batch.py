"""Synthetic packages only. Never open project replay/ward/heldout artifacts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from dota2_insights import ward_review_batch as review
from dota2_insights.ward_observation_check import FIELDS, template
from dota2_insights.ward_validation_package import sha


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


class WardBatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / 'package'
        for directory in ('review', 'evidence', 'sealed-answers'):
            (self.package / directory).mkdir(parents=True)
        blind = [{'sample_id': f'generated-{i}', 'record_index': i,
                  'navigation_game_s': 10 - i, 'group': 'random' if i == 0 else 'special'}
                 for i in range(2)]
        answers = [{'sample_id': row['sample_id'], 'record_index': row['record_index'],
                    'match_id': '9050000001',
                    'record': {'ward_type': 'observer', 'team': 2, 'placed_game_time_s': 0}}
                   for row in blind]
        write(self.package / 'review/blind.json', blind)
        write(self.package / 'sealed-answers/answers.json', answers)
        (self.package / 'evidence/freeze.json').write_bytes(b'{}')
        self.manifest = {'status': 'samples_frozen_pending_visual_review', 'role': 'development',
                         'source_freeze_sha256': sha(b'{}'), 'cohort_key': 'generated-only',
                         'match_id': '9050000001',
                         'files': {name: sha((self.package / name).read_bytes()) for name in
                                   ('review/blind.json', 'sealed-answers/answers.json')}}
        self.bind_manifest()
        self.observed = template(self.package)
        self.observed.update(observer='synthetic', reviewed_at_utc='2026-10-23T00:00:00Z')
        image = self.root / 'frame.png'
        Image.new('RGB', (640, 360), 'black').save(image)
        self.observed['evidence'] = [{'evidence_id': 'frame', 'file': 'frame.png',
            'sha256': sha(image.read_bytes()), 'match_id': '9050000001',
            'clock_basis': 'client_game_clock', 'game_time_s': 0, 'clock_precision_s': 1,
            'captured_at_utc': '2026-10-23T00:00:00Z'}]
        self.observed['samples'][0].update(observed_type='observer', observed_team=2,
            field_evidence={'ward_type': ['frame'], 'team': ['frame']})
        self.observations = self.root / 'observations.json'
        write(self.observations, self.observed)
        self.batch = self.root / 'batch'

    def bind_manifest(self):
        write(self.package / 'package.json', self.manifest)
        (self.package / 'package.sha256').write_text(sha((self.package / 'package.json').read_bytes()))

    def freeze(self, **kwargs):
        return review.freeze_batch(self.package, self.observations, self.batch, **kwargs)

    def test_freeze_selects_reviewed_subset_preserving_blind_roster_and_exact_frames(self):
        answers = self.package / 'sealed-answers/answers.json'
        original = answers.read_bytes()
        answers.unlink()  # Freezing must not need answers at all.
        receipt = self.freeze(prior_exposure='blind_declared')
        self.assertEqual(receipt['sample_ids'], ['generated-0'])
        self.assertFalse(receipt['independent_validation_passed'])
        self.assertEqual((self.batch / 'observations.json').read_bytes(), self.observations.read_bytes())
        self.assertEqual((self.batch / 'frame.png').read_bytes(), (self.root / 'frame.png').read_bytes())
        self.assertEqual(len(review.load_batch(self.package, self.batch)[1]['samples']), 2)
        answers.write_bytes(original)
        result = review.compare_batch(self.package, self.batch)
        self.assertEqual(result['status'], 'partial_review_compared')
        self.assertEqual((result['sample_count'], result['remaining_samples']), (1, 1))
        self.assertEqual(result['counts'], {'pass': 2, 'pending': 6})
        self.assertFalse(result['independent_validation_passed'])
        self.assertFalse(result['map_calibration_verified'])

    def test_pending_duplicate_unknown_subsets_and_exposure_rejected(self):
        for ids in (['generated-1'], ['generated-0'] * 2, ['missing']):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.freeze(sample_ids=ids)
            self.assertFalse(self.batch.exists())
        with self.assertRaises(ValueError):
            self.freeze(prior_exposure='independent_passed')

    def test_frozen_receipt_observation_and_frame_tampering_rejected(self):
        self.freeze()
        for name in ('batch.json', 'observations.json', 'preflight.json', 'frame.png'):
            path = self.batch / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(name=name), self.assertRaises(ValueError):
                review.load_batch(self.package, self.batch)
            path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'new_batch'):
            self.freeze()

    def test_observation_failure_precedes_answer_access(self):
        self.freeze()
        (self.batch / 'observations.json').write_text('{}')
        (self.package / 'sealed-answers/answers.json').unlink()
        with self.assertRaisesRegex(ValueError, 'frozen_observations_changed'):
            review.compare_batch(self.package, self.batch)

    def test_changed_answers_rejected(self):
        self.freeze()
        write(self.package / 'sealed-answers/answers.json', [])
        with self.assertRaisesRegex(ValueError, 'answers_fingerprint'):
            review.compare_batch(self.package, self.batch)

    def test_queue_is_blind_chronological_and_rejects_duplicate_packages(self):
        (self.package / 'sealed-answers/answers.json').unlink()
        result = review.review_queue([self.package])
        self.assertEqual([r['sample_id'] for r in result['samples']], ['generated-1', 'generated-0'])
        self.assertTrue(all(r['end_navigation_game_s'] is None for r in result['samples']))
        self.assertNotIn('program_values', result['samples'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate_queue'):
            review.review_queue([self.package, self.package])

    def remaining_ledger(self):
        original = review.review_queue([self.package])
        remain = [dict(r, review_state='pending_client_observation') for r in original['samples'] if r['group']=='random']
        exposed = [dict(r, review_state='previously_compared_not_blind') for r in original['samples'] if r['group']=='special']
        ledger = {'kind':'existing_development_ward_review_ledger','schema_version':1,
            'answers_read':False,'new_sampling_performed':False,'development_total':2,
            'remaining':remain,'previously_compared':exposed,'remaining_counts':{'random':1},
            'sources':[{'file':str((self.package/'review/blind.json').resolve()),
                        'sha256':sha((self.package/'review/blind.json').read_bytes())}]}
        path=self.root/'remaining.json'
        write(path,ledger)
        return path,ledger

    def test_remaining_queue_uses_full_source_bound_ledger_without_answers(self):
        path,_=self.remaining_ledger()
        (self.package/'sealed-answers/answers.json').unlink()
        result=review.review_queue([self.package],remaining_ledger=path)
        self.assertEqual(result['sample_count'],1)
        self.assertEqual(result['samples'][0]['group'],'random')
        self.assertEqual(result['selection']['original_sample_count'],2)
        self.assertEqual(len(result['selection']['excluded_previously_compared']),1)
        self.assertFalse(result['independent_validation_passed'])
        from dota2_insights.recording_metadata import blind_queue
        self.assertEqual(blind_queue(result)['sample_count'],1)
        output=self.root/'remaining-page'
        output.mkdir()
        review.render_queue(result,output)
        self.assertIn('仅本次待观察清单',(output/'index.html').read_text(encoding='utf-8'))

    def test_remaining_queue_refuses_missing_roster_changed_navigation_and_source(self):
        path,ledger=self.remaining_ledger()
        for changed in ('roster','navigation','source','counts','exposure'):
            value=copy.deepcopy(ledger)
            if changed=='roster':value['previously_compared']=[]
            if changed=='navigation':value['remaining'][0]['navigation_game_s']+=1
            if changed=='source':value['sources'][0]['sha256']='0'*64
            if changed=='counts':value['remaining_counts']={'special':1}
            if changed=='exposure':value['previously_compared'][0]['review_state']='blind'
            write(path,value)
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                review.review_queue([self.package],remaining_ledger=path)

    def test_recorder_navigation_is_bound_to_one_match_at_start(self):
        from dota2_insights.recording_metadata import single_match_queue
        queue=review.review_queue([self.package])
        clean,mid=single_match_queue(queue,'9050000001')
        self.assertEqual(mid,'9050000001')
        self.assertEqual(clean['sample_count'],2)
        with self.assertRaisesRegex(ValueError,'比赛编号'):
            single_match_queue(queue,'9050000002')
        mixed=copy.deepcopy(queue)
        mixed['samples'][1]['match_id']='9050000002'
        with self.assertRaisesRegex(ValueError,'单场比赛'):
            single_match_queue(mixed)

    def test_report_copies_only_bound_images_without_claiming_certification(self):
        self.freeze()
        result = review.compare_batch(self.package, self.batch)
        output = self.root / 'report'
        review.render_comparison(result, self.batch, output)
        page = (output / 'index.html').read_text(encoding='utf-8')
        self.assertIn('comparison.json', page)
        images = list((output / 'frames').glob('*.png'))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].read_bytes(), (self.root / 'frame.png').read_bytes())
        with self.assertRaisesRegex(ValueError, 'new_report'):
            review.render_comparison(result, self.batch, output)

    def test_map_and_censored_ends_stay_unjudgeable(self):
        self.assertEqual(review.compare_field('map_position', {}, {})[0], 'unjudgeable')
        self.assertEqual(review.compare_field('world_position',
            {'world_position': [0, 0]}, {'x': 0, 'y': 0})[0], 'unjudgeable')
        self.assertEqual(review.compare_field('end_reason',
            {'end_reason': 'match_end_censored'}, {'end_kind': 'killed'})[0], 'unjudgeable')

    def test_enemy_deward_requires_independent_killer_and_team(self):
        row = {'end_reason': 'enemy_deward', 'observed_team': 2}
        answer = {'end_kind': 'killed', 'killer': 'generated_actor'}
        self.assertEqual(review.compare_field('end_reason', row, answer)[0], 'unjudgeable')
        row.update(observed_killer='generated_actor', observed_killer_team=3)
        self.assertEqual(review.compare_field('end_reason', row, answer)[0], 'pass')
        row['observed_killer_team'] = 2
        self.assertEqual(review.compare_field('end_reason', row, answer)[0], 'mismatch')
        row['end_reason'] = 'deny'
        self.assertEqual(review.compare_field('end_reason', row, answer)[0], 'pass')


if __name__ == '__main__':
    unittest.main()
