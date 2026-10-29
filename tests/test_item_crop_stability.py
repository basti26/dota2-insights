from io import BytesIO
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from dota2_insights.item_crop_stability import compare, probe, duplication_summary


def png(array):
    stream=BytesIO();Image.fromarray(array.astype('uint8')).save(stream,format='PNG');return stream.getvalue()


def evidence(root):
    root.mkdir();(root/'crops').mkdir();rng=np.random.default_rng(42)
    a=rng.integers(0,255,(72,96,3),dtype=np.uint8)
    b=np.zeros_like(a);b[3:]=a[:-3]
    entries=[]
    for episode,role,item,pixels in ((0,'known','blink',a),(0,'first_unknown','blink',b),
                                   (0,'last_unknown','blink',b),(1,'known','holy_locket',rng.integers(0,255,a.shape,dtype=np.uint8))):
        raw=png(pixels);name=f'crops/{episode:03d}-{role}.png';(root/name).write_bytes(raw)
        entries.append({'episode_index':episode,'role':role,'hero_id':'npc_dota_hero_axe','slot_id':'main_1',
            'last_known_item':item,'item_id':item if role=='known' else None,'status':'prepared_for_visual_review',
            'image':name,'crop_sha256':hashlib.sha256(raw).hexdigest(),
            'evidence':{'line':episode+1,'line_sha256':'b'*64},'client_rect':[0,0,96,72]})
    p=root/'evidence.json';p.write_text(json.dumps({'schema_version':1,'kind':'equipment_continuity_evidence',
        'match_id':'9876543210','equipment_package_id':'a'*64,'source_verified_before_and_after':True,'entries':entries}))
    return p,a,b


class ItemCropStabilityTests(unittest.TestCase):
    def test_fixed_registration_recovers_known_shift_without_identity_claim(self):
        rng=np.random.default_rng(3);a=rng.integers(0,255,(72,96,3),dtype=np.uint8);b=np.zeros_like(a);b[3:]=a[:-3]
        r=compare(png(a),png(b))
        self.assertGreater(r['aligned_ncc'],.99)
        self.assertEqual(r['best_shift_xy'],[0,3])
        self.assertGreater(r['alignment_gain'],.8)

    def test_constant_images_do_not_get_spurious_perfect_match(self):
        r=compare(png(np.zeros((72,96,3))),png(np.zeros((72,96,3))))
        self.assertIsNone(r['aligned_ncc'])
        self.assertEqual(r['reason'],'insufficient_variance')

    def test_prepared_sha_controls_and_same_frame_roles_retained(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);p,_,_=evidence(root/'prepared')
            r=probe([p],root/'probe')
            self.assertEqual((r['unknown_role_crops'],r['episode_count']),(2,1))
            self.assertEqual(r['results'][0]['different_recorded_item_controls'],1)
            self.assertFalse(r['accuracy_verified'])
            self.assertFalse(r['original_sources_reverified_here'])
            self.assertEqual(r['results'][0]['query_crop_sha256'],r['results'][1]['query_crop_sha256'])
            self.assertEqual(r['evidence_units']['query_role_rows'],2)
            self.assertEqual(r['evidence_units']['distinct_source_rows'],1)
            self.assertEqual(r['evidence_units']['distinct_crop_bytes'],1)
            self.assertEqual(r['evidence_units']['same_episode_source_crop_role_aliases'][0]['roles'],
                             ['first_unknown','last_unknown'])

    def test_identical_pixels_in_different_source_rows_are_not_merged_observations(self):
        def row(line, role='first_unknown', reference='c'*64):
            return {'package_id':'a'*64,'query_evidence':{'line':line,'line_sha256':str(line)*64},
                    'query_crop_sha256':'b'*64,'reference_crop_sha256':reference,'episode':0,'role':role}
        r=duplication_summary([row(1),row(2,reference='d'*64)])
        self.assertEqual(r['distinct_source_rows'],2)
        self.assertEqual(r['distinct_crop_bytes'],1)
        self.assertEqual(r['distinct_reference_query_byte_pairs'],2)
        self.assertEqual(r['same_episode_source_crop_role_aliases'],[])
        self.assertEqual(r['same_crop_bytes_across_source_rows'][0]['source_rows'],2)
        self.assertFalse(r['independent_trials_verified'])

    def test_missing_or_invalid_source_row_identity_refused(self):
        for values in ({'line':True,'line_sha256':'b'*64},{'line':1,'line_sha256':'invalid'},{}):
            with TemporaryDirectory() as temp:
                root=Path(temp);p,_,_=evidence(root/'prepared');m=json.loads(p.read_text())
                m['entries'][1]['evidence']=values;p.write_text(json.dumps(m))
                with self.assertRaisesRegex(ValueError,'source_row_identity'):
                    probe([p],root/'probe')
                self.assertFalse((root/'probe').exists())

    def test_changed_crop_and_path_escape_refused(self):
        for mode in ('changed','escape'):
            with TemporaryDirectory() as temp:
                root=Path(temp);p,_,_=evidence(root/'prepared');m=json.loads(p.read_text())
                if mode=='changed':(p.parent/m['entries'][0]['image']).write_bytes(b'changed')
                else:m['entries'][0]['image']='../outside.png';p.write_text(json.dumps(m))
                with self.assertRaises(ValueError):probe([p],root/'probe')
                self.assertFalse((root/'probe').exists())

    def test_duplicate_role_or_changed_reference_label_refused(self):
        for mode in ('duplicate','identity'):
            with TemporaryDirectory() as temp:
                root=Path(temp);p,_,_=evidence(root/'prepared');m=json.loads(p.read_text())
                if mode=='duplicate':m['entries'].append(m['entries'][0])
                else:m['entries'][1]['hero_id']='npc_dota_hero_lion'
                p.write_text(json.dumps(m))
                with self.assertRaises(ValueError):probe([p],root/'probe')

    def test_holdout_checked_before_any_image_decode(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);p,_,_=evidence(root/'prepared')
            with patch('dota2_insights.item_crop_stability.refuse_locked_match',side_effect=ValueError('locked')), \
                 patch('PIL.Image.open') as opened:
                with self.assertRaises(ValueError):probe([p],root/'probe')
                opened.assert_not_called()

    def test_same_package_cannot_be_counted_twice(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);p,_,_=evidence(root/'prepared')
            with self.assertRaises(ValueError):probe([p,p],root/'probe')
