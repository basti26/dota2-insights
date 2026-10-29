import hashlib
import json
import unittest

from dota2_insights.ward_reference_export import build


class WardReferenceExportTests(unittest.TestCase):
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
