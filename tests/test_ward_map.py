import copy
from dataclasses import asdict
import unittest

from dota2_insights.virtual_map import MapIdentity, VirtualMap, WorldBounds, digest
from dota2_insights.ward_map import bind, status, visible_points, phase_of, check_installation, PHASES
from dota2_insights.map_prototype import ward_candidate_xy


def model(build=10,sha='a'*64,bounds=None):
    return VirtualMap(MapIdentity('dota',build,sha),bounds or WorldBounds(-9472,-9472,9472,9472),
        'source2_map_centered','asset_extracted','synthetic ward binding fixture')


def layer():
    return {'match_id':'1234567890','source_map_identity':None,'translation_candidate_xy':[-16384,-16384],
        'points':[{'id':str(i),'raw_world_xy':[16000+i,17000],'ward_type':'observer' if i%2 else 'sentry',
            'placed_game_time_s':t} for i,t in enumerate((-1,0,599.9,600,1199.9,1200,1799.9,1800,None))]}


def revision(target):
    identity=asdict(target.identity);steam='b'*64
    resources=[{'resource':'maps/dota/synthetic.raw','bytes':1,'sha256':'c'*64,'crc32':'00000000'}]
    return {'schema_version':1,'kind':'local_map_revision','identity':identity,'steam_inf_sha256':steam,
        'revision_id':digest({'identity':identity,'steam_inf_sha256':steam}),
        'resources':resources,'map_content_sha256':digest([{k:r[k] for k in ('resource','bytes','sha256')} for r in resources]),
        'probe':{'eligible_profile':None},'calibration_status':'unverified','production_ready':False,
        'external_dependencies_verified':False}


class WardMapTests(unittest.TestCase):
    def test_unknown_source_is_explicit_preview_and_never_calibrated(self):
        target=model();data=layer();before=copy.deepcopy(data)
        data['map_binding']=bind(data,target,revision(target))
        self.assertEqual(data['map_binding']['source_map_relation'],'unknown')
        self.assertEqual(visible_points(data,target),[])
        self.assertEqual(len(visible_points(data,target,allow_unverified=True)),9)
        self.assertEqual(data['points'],before['points'])
        self.assertFalse(data['map_binding']['production_ready'])
        self.assertFalse(data['map_binding']['origin_translation_verified'])
        self.assertEqual(ward_candidate_xy(data,data['points'][0],target),(-384,616))
        with self.assertRaisesRegex(ValueError,'requires_target_model'):
            ward_candidate_xy(data,data['points'][0])

    def test_explicit_source_mismatch_keeps_records_but_blocks_drawing(self):
        target=model();data=layer();data['source_map_identity']=asdict(model(sha='d'*64).identity)
        data['map_binding']=bind(data,target)
        self.assertEqual(status(data,target)['source_map_relation'],'mismatch')
        self.assertEqual(visible_points(data,target,allow_unverified=True),[])
        self.assertEqual(len(data['points']),9)
        with self.assertRaisesRegex(ValueError,'source_map_mismatch'):
            ward_candidate_xy(data,data['points'][0],target)

    def test_identity_agreement_does_not_certify_projection(self):
        target=model();data=layer();data['source_map_identity']=asdict(target.identity)
        data['map_binding']=bind(data,target)
        self.assertEqual(status(data,target)['source_map_relation'],'exact_identity')
        self.assertFalse(status(data,target)['projection_verified'])
        self.assertFalse(status(data,target)['ward_legality_verified'])

    def test_same_map_other_client_context_is_separate(self):
        target=model();data=layer();data['source_map_identity']=asdict(model(build=9).identity)
        data['map_binding']=bind(data,target)
        self.assertEqual(status(data,target)['source_map_relation'],'same_map_bytes_other_context')
        self.assertTrue(status(data,target)['preview_allowed'])
        self.assertFalse(status(data,target)['production_ready'])

    def test_cached_layer_cannot_silently_follow_new_version_or_bounds(self):
        target=model();data=layer();data['map_binding']=bind(data,target)
        for other in (model(build=11),model(sha='d'*64),model(bounds=WorldBounds(-10000,-10000,10000,10000))):
            with self.subTest(other=other.model_id),self.assertRaisesRegex(ValueError,'target_map_changed'):
                visible_points(data,other,allow_unverified=True)
            with self.assertRaisesRegex(ValueError,'target_map_changed'):bind(data,other)

    def test_phase_boundaries_and_unknown_times_do_not_become_zero(self):
        self.assertEqual([phase_of(v) for v in (-1,0,599.9,600,1199.9,1200,1799.9,1800,None)],
            ['赛前','0–10分钟','0–10分钟','10–20分钟','10–20分钟','20–30分钟','20–30分钟','30分钟后','时间未知'])
        for unknown in (False,True,float('nan'),float('inf'),'600'):
            self.assertEqual(phase_of(unknown),'时间未知')
        data=layer();target=model();data['map_binding']=bind(data,target)
        selected=[p['id'] for phase in PHASES[1:] for p in visible_points(data,target,phase,allow_unverified=True)]
        self.assertEqual(sorted(selected),sorted(p['id'] for p in data['points']))
        with self.assertRaises(ValueError):visible_points(data,target,'live_now',allow_unverified=True)

    def test_map_update_check_retains_historical_view_and_denies_live_reuse(self):
        target=model();data=layer();data['map_binding']=bind(data,target)
        result=check_installation(data,target,revision(model(build=11)))
        self.assertTrue(result['requires_new_binding'])
        self.assertFalse(result['target_matches_installation'])
        self.assertTrue(result['historical_preview_allowed'])
        self.assertFalse(result['reuse_for_current_game_allowed'])

    def test_fake_certification_source_relation_and_wrong_revision_refused(self):
        target=model();data=layer();data['map_binding']=bind(data,target)
        for key,value in (('projection_verified',True),('preview_allowed',False),('target_revision_id','../elsewhere')):
            edited=copy.deepcopy(data);edited['map_binding'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):status(edited,target)
        with self.assertRaisesRegex(ValueError,'revision_mismatch'):bind(layer(),target,revision(model(build=11)))
