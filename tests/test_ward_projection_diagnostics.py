import unittest
from dota2_insights.ward_projection_diagnostics import ProjectionDiagnostics


class ProjectionDiagnosticsTests(unittest.TestCase):
    def anchor_trial_row(self):
        row=self.row();row['self_position_age_s']=.1
        row['self_screen_anchor']={'x':20,'y':30}
        row.update(width=1920,height=1080)
        row['anchor_plane_candidate']={'version':'ward_three_edge_self_anchor_v1','status':'conditional_candidate',
            'reason':'three_lines_plus_self_anchor','raw_world_to_screen':[[1,0,-16384],[0,1,-16384],[0,0,1]],
            'source_world_anchor':[16404,16414],'source_screen_anchor':[20,30],
            'anchor_fit_residual_px':0,'residual_is_training_fit_not_accuracy':True,
            'coordinate_accuracy_verified':False,'full_viewport_verified':False,'coplanarity_verified':False}
        return row

    def test_shadow_anchor_fit_is_kept_separate_and_not_a_display_or_accuracy_claim(self):
        d=ProjectionDiagnostics();d.add(self.anchor_trial_row())
        s=d.summary()['anchor_plane_diagnostics']
        self.assertEqual(s['reason_counts'],{'three_lines_plus_self_anchor':1})
        self.assertFalse(s['live_display_uses_candidate']);self.assertFalse(s['fit_residual_is_accuracy'])
        self.assertEqual(s['camera_recovery_counts'],{'affine_plane_does_not_identify_focal_length':1})

    def test_shadow_anchor_rejects_changed_pose_stale_context_and_false_verification(self):
        for case in ('pose','age','claim','residual'):
            row=self.anchor_trial_row()
            if case=='pose':row['context']['own_hero_pose']['xpos']=21
            if case=='age':row['self_position_age_s']=3
            if case=='claim':row['anchor_plane_candidate']['full_viewport_verified']=True
            if case=='residual':row['anchor_plane_candidate']['raw_world_to_screen'][0][2]+=100
            with self.subTest(case=case),self.assertRaises(ValueError):ProjectionDiagnostics().add(row)

    def row(self):
        matrix = [[1, 0, -16384], [0, 1, -16384], [0, 0, 1]]
        return {'camera_method': 'ward_terrain_trial_v1', 'world_to_screen': matrix,
                'self_world_xy': [20, 30], 'self_screen_anchor': {'x': 23, 'y': 34},
                'self_position_age_s': .5,
                'context': {'own_hero_pose': {'coordinate_basis': 'gsi_centered_world_xy',
                            'alive': True, 'xpos': 20, 'ypos': 30}},
                'projection_stability': {'version': 'ward_motion_confirmation_v1',
                    'accepted': True, 'reason': 'motion_compensated_projection',
                    'coordinate_accuracy_verified': False, 'raw_world_to_screen': matrix,
                    'previous_to_current': [[1,0,0],[0,1,0],[0,0,1]], 'correction_max_px': 2}}

    def test_raw_and_applied_residual_preserves_centered_world_convention(self):
        d=ProjectionDiagnostics();d.add(self.row());s=d.summary()
        self.assertEqual(s['self_anchor_disagreement_px']['terrain_vs_visual_anchor_candidate:applied']['median'],5)
        self.assertEqual(s['counts']['accepted'],1)
        self.assertFalse(s['complete_overlap_verified'])

    def test_hidden_jump_retains_large_raw_anchor_error_not_zero(self):
        row=self.row();row['world_to_screen']=None
        row['projection_stability'].update(accepted=False,reason='projection_jump_confirming',correction_max_px=100)
        d=ProjectionDiagnostics();d.add(row);s=d.summary()
        self.assertEqual(s['counts']['hidden'],1)
        self.assertNotIn('terrain_vs_visual_anchor_candidate:applied',s['self_anchor_disagreement_px'])
        self.assertEqual(s['model_correction_px']['maximum'],100)

    def test_anchor_dependent_fallback_is_not_independent_evidence(self):
        row=self.row();row['camera_method']='self_nearby_ward_estimate_v1'
        d=ProjectionDiagnostics();d.add(row)
        self.assertIn('anchor_dependent_not_accuracy:raw',d.summary()['self_anchor_disagreement_px'])

    def test_missing_stale_or_inconsistent_pose_has_no_error_distribution(self):
        cases=[{},self.row(),self.row(),self.row()]
        cases[1]['self_position_age_s']=5
        cases[2]['context']['own_hero_pose']['xpos']=21
        cases[3]['context']['own_hero_pose']['coordinate_basis']='unknown'
        for row in cases:
            d=ProjectionDiagnostics();d.add(row)
            self.assertEqual(d.summary()['self_anchor_disagreement_px'],{})

    def test_malformed_claims_and_nonfinite_metrics_refused(self):
        for key,value in [('accepted',False),('coordinate_accuracy_verified',True),
                          ('correction_max_px',float('nan')),('raw_world_to_screen',[[1]])]:
            row=self.row();row['projection_stability'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):ProjectionDiagnostics().add(row)

    def test_empty_or_legacy_diagnostics_do_not_certify_zero_error(self):
        d=ProjectionDiagnostics();d.add({});s=d.summary()
        self.assertIsNone(s['model_correction_px']['maximum'])
        self.assertEqual(s['counts']['legacy_without_stability'],1)
