"""Generated glyphs and maps only; no real game assets or sealed matches."""
import copy
import unittest
from unittest.mock import patch
import numpy as np

from dota2_insights.map_glyph_probe import detect_glyph
from dota2_insights.map_glyph_probe import detect_building_silhouette
from dota2_insights import map_glyph_review as m


class GlyphTests(unittest.TestCase):
    def template(self):
        t = np.zeros((16,16,4), np.uint8)
        t[2:14,2:14,3] = 255
        t[4:12,6:10,:3] = 240
        t[7:10,4:13,:3] = 240
        return t

    def test_detection_searches_entire_image_without_world_coordinates(self):
        t = self.template()
        im = np.full((96,96,3),50,np.uint8)
        for x,y in ((12,12),(62,62)):
            tile = im[y:y+16,x:x+16]
            tile[t[:,:,3]>0] = t[:,:,:3][t[:,:,3]>0]
        r = detect_glyph(im,t,sizes=(16,))
        self.assertEqual(sorted(c['pixel_xy'] for c in r['candidates']), [[19.5,19.5],[69.5,69.5]])
        self.assertFalse(r['predictions_used'])
        self.assertFalse(r['identity_confirmed'])

    def test_flat_and_unbounded_inputs_rejected(self):
        for im,t in ((np.zeros((32,32,3),np.uint8), self.template()),
                     (np.zeros((96,96,3),np.uint8), np.zeros((16,16,4),np.uint8))):
            with self.assertRaises(ValueError):
                detect_glyph(im,t)

    def test_four_point_permutation_and_reference_pixel_scale(self):
        p = [[10,10],[90,10],[10,90],[90,90]]
        r = m.compare_set(p,p[::-1],crop_width=380)
        self.assertEqual(r['status'],'setwise_compatible')
        self.assertEqual(r['errors_reference_px'],[0]*4)
        shifted = [[x+2,y] for x,y in p]
        self.assertEqual(m.compare_set(shifted,p,crop_width=360)['status'],'setwise_mismatch')

    def test_twelve_point_assignment_is_exact_and_bounded(self):
        p=[[20+x*30,20+y*30] for x in range(4) for y in range(3)]
        shifted=[[x+.25,y] for x,y in p]
        r=m.compare_set(shifted,p[::-1],crop_width=360)
        self.assertEqual(r['status'],'setwise_compatible')
        self.assertTrue(all(abs(e-.25)<1e-9 for e in r['errors_reference_px']))

    def test_subset_assignment_agrees_with_exhaustive_small_sets(self):
        import itertools
        import math
        p=[[10,10],[40,15],[15,65],[70,70],[85,30]]
        q=[[9,12],[39,13],[20,62],[73,67],[80,29]]
        exact=min(sum(math.dist(a,q[j])**2 for a,j in zip(p,order)) for order in itertools.permutations(range(5)))
        r=m.compare_set(p,q,crop_width=360)
        self.assertAlmostEqual(sum(e**2 for e in r['errors_reference_px']),exact)

    def test_tinted_shape_detection_does_not_need_world_or_team_identity(self):
        t=self.template()
        foreground=(t[:,:,3]>16)&(t[:,:,:3].max(axis=2)>40)
        image=np.full((96,96,3),50,np.uint8)
        for (x,y),color in (((12,12),(220,0,0)),((62,62),(0,220,0))):
            tile=image[y:y+16,x:x+16];tile[foreground]=color
        r=detect_building_silhouette(image,t,sizes=(16,))
        self.assertEqual(sorted(p['pixel_xy'] for p in r['candidates']),[[19.5,19.5],[69.5,69.5]])
        self.assertFalse(r['identity_confirmed'])
        self.assertFalse(r['predictions_used'])

    def test_greyscale_background_cannot_be_tinted_building_evidence(self):
        r=detect_building_silhouette(np.full((96,96,3),150,np.uint8),self.template(),sizes=(16,))
        self.assertEqual(r['candidates'],[])

    def test_profile_mismatch_rejected_before_prediction_comparison(self):
        f,batch,obs=self.fixture()
        obs['icon_profile']='buildings_tint'
        self.freeze(f,batch,obs)
        with self.assertRaisesRegex(ValueError,'profile_invalid'):
            m.compare(f.package,batch,f.root/'bad')

    def test_missing_extra_and_overflow_never_become_match(self):
        for observed, expected, overflow in (([], [[1,1]], False),
            ([[1,1],[2,2]],[[1,1]],False), ([[1,1]],[[1,1]],True)):
            r = m.compare_set(observed,expected,crop_width=360,overflow=overflow)
            self.assertEqual(r['status'],'unjudgeable_count_or_overflow')
            self.assertIsNone(r['errors_reference_px'])

    def test_duplicate_expected_locations_are_ambiguous(self):
        r = m.compare_set([[10,10],[30,30]],[[20,20],[20,20]],crop_width=360)
        self.assertEqual(r['status'],'unjudgeable_assignment')

    def test_nonfinite_and_permutation_budget_rejected(self):
        for values in ([[float('nan'),1]], [[True,1]], [[1,2,3]], [[1,1]]*17):
            with self.assertRaises(ValueError):
                m.compare_set(values,[[1,1]],crop_width=360)

    def test_definitions_bound_to_correct_sheet(self):
        text = '"minimap_watcher" { "file" "materials/vgui/hud/minimap_sheet.vmat" "x" "128" "y" "0" "width" "64" "height" "64" }'
        self.assertEqual(m.icon_rect(text,'minimap_watcher'),[128,0,64,64])
        with self.assertRaises(ValueError):
            m.icon_rect(text.replace('minimap_sheet.vmat','other.vmat'),'minimap_watcher')

    def fixture(self):
        from test_map_holdout import MapHoldoutTests
        f = MapHoldoutTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        plan = f.prepare()
        rows = [{'case': frame['case'], 'frame_index': frame['frame_index'],
                 'icon': icon, 'image_sha256': frame['image_sha256'], 'crop_width': frame['size'][0],
                 'detection': {'candidates': [],'overflow':False}}
                 for frame in plan['frames'] for icon in m.ICONS]
        batch = f.root / 'batch'
        batch.mkdir()
        obs = {'map_build':plan['map_build'], 'package_sha256':m.sha(f.package/'package.json'),
               'predictions_read':False,'rows':rows}
        return f, batch, obs

    def freeze(self,f,batch,obs):
        m.save(batch/'observations.json',obs)
        m.save(batch/'freeze.json',{'observations_sha256':m.sha(batch/'observations.json'),
            'package_sha256':m.sha(f.package/'package.json'),'predictions_read':False})

    def test_comparison_of_missing_detections_does_not_certify(self):
        f,batch,obs = self.fixture()
        self.freeze(f,batch,obs)
        r = m.compare(f.package,batch,f.root/'compared')
        self.assertFalse(r['map_certified'])
        self.assertFalse(r['formal_p2_pass'])
        self.assertTrue(all(row['comparison']['errors_reference_px'] is None for row in r['rows']))

    def test_tampered_frozen_observations_rejected_before_predictions(self):
        f,batch,obs = self.fixture()
        self.freeze(f,batch,obs)
        with (batch/'observations.json').open('a') as stream:
            stream.write(' ')
        original = m.load
        def guard(path,*args):
            self.assertNotEqual(path.name,'predictions.json')
            return original(path,*args)
        with patch.object(m,'load',side_effect=guard):
            with self.assertRaisesRegex(ValueError,'frozen_sources_changed'):
                m.compare(f.package,batch,f.root/'bad')

    def test_duplicate_or_missing_frame_row_rejected(self):
        for mode in ('duplicate','missing','wrong_image'):
            f,batch,obs = self.fixture()
            if mode=='duplicate': obs['rows'].append(copy.deepcopy(obs['rows'][0]))
            if mode=='missing': obs['rows'].pop()
            if mode=='wrong_image': obs['rows'][0]['image_sha256']='wrong'
            self.freeze(f,batch,obs)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError,'full_frame_roster'):
                m.compare(f.package,batch,f.root/'bad')
