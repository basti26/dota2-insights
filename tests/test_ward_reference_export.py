import hashlib
import json
import unittest

from dota2_insights.ward_reference_export import build


class WardReferenceExportTests(unittest.TestCase):
    def height_source(self):
        from dota2_insights.replay_height import height_observation,FIELDS
        manifest,raw=self.source();wards=json.loads(raw)
        manifest['height_adapter_sha256']='b'*64
        for index,ward in enumerate(wards):
            ward.update(height_observation=height_observation(dict(zip(FIELDS,(128,0 if index==0 else None)))),
                height_entity_index=index+1,height_entity_serial=5,
                height_association='same_native_placement_event_and_entity_callback')
        return manifest,wards

    def build_height(self,manifest,wards):
        raw=json.dumps(wards).encode()
        manifest['files']['wards.json']={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        return build(manifest,raw)

    def test_raw_height_and_unknown_with_original_identity_are_preserved(self):
        manifest,wards=self.height_source();packet=self.build_height(manifest,wards)
        self.assertEqual(packet['points'][0]['height_observation']['raw_z'],16384)
        self.assertIsNone(packet['points'][1]['height_observation']['raw_z'])
        self.assertEqual(packet['points'][0]['height_source']['ward_index'],0)
        self.assertEqual(packet['height_adapter_sha256'],'b'*64)
        self.assertEqual(packet['map_version'],'unknown')
        self.assertFalse(packet['world_projection_verified'])

    def test_mismatched_height_math_accuracy_claim_and_missing_source_refused(self):
        for field,value in [('raw_z',10),('ground_height_verified',True),('ground_height_verified',0)]:
            manifest,wards=self.height_source();wards[0]['height_observation'][field]=value
            with self.assertRaises(ValueError):self.build_height(manifest,wards)
        manifest,wards=self.height_source();manifest.pop('height_adapter_sha256')
        with self.assertRaisesRegex(ValueError,'height_source'):self.build_height(manifest,wards)
        manifest,wards=self.height_source()
        h=wards[0]['height_observation'];h['components']['CBodyComponent.m_cellZ']=0;h['raw_z']=False
        with self.assertRaises(ValueError):self.build_height(manifest,wards)

    def source(self):
        wards=[{'match_id':'9876543210','x':12345,'y':15000,'ward_type':t,'team':2,
                'placed_game_time_s':i*60,'after_match_end':False} for i,t in enumerate(('observer','sentry'))]
        raw=json.dumps(wards).encode()
        manifest={'match_id':'9876543210','parsed_match_id':'9876543210','complete':True,
            'independent_footer_matches_parser':True,'parse_error':None,'ward_count':2,
            'source_sha256':'a'*64,'files':{'wards.json':{'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}}}
        return manifest,raw

    def test_all_placements_kept_and_tree_requirement_not_guessed(self):
        r=build(*self.source())
        self.assertEqual(len(r['points']),2)
        self.assertTrue(all(p['tree_clearance']=='unknown' for p in r['points']))
        self.assertFalse(r['preference_statistics'])
        self.assertFalse(r['world_projection_verified'])

    def test_incomplete_parse_and_changed_wards_refused(self):
        m,r=self.source();m['complete']=False
        with self.assertRaises(ValueError):build(m,r)
        m['complete']=True
        with self.assertRaises(ValueError):build(m,r+b' ')

    def test_yellow_cross_requires_bound_review_with_explicit_evidence(self):
        m,r=self.source()
        review={'match_id':m['match_id'],'wards_sha256':m['files']['wards.json']['sha256'],
                'points':[{'id':'9876543210:0','clearance':'required','evidence':'independent terrain placement review'}]}
        self.assertEqual(build(m,r,tree_reviews=review)['points'][0]['tree_clearance'],'required')
        review['points'][0]['evidence']=''
        with self.assertRaises(ValueError):build(m,r,tree_reviews=review)

    def test_wrong_match_or_unknown_ward_tree_review_refused(self):
        m,r=self.source()
        review={'match_id':'9876543210','wards_sha256':m['files']['wards.json']['sha256'],
                'points':[{'id':'bad','clearance':'required','evidence':'x'}]}
        with self.assertRaises(ValueError):build(m,r,tree_reviews=review)
        review['match_id']='9876543211'
        with self.assertRaises(ValueError):build(m,r,tree_reviews=review)
