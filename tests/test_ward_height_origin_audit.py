import unittest

import numpy as np

from dota2_insights.map_collision import CollisionMesh
from dota2_insights.replay_height import FIELDS, height_observation
from tools.audit_ward_height_origin import compare_placement


class WardHeightOriginAuditTests(unittest.TestCase):
    def mesh(self, z, ordinal, attribute=0):
        return CollisionMesh(ordinal, attribute, 0,
            np.array([[0.,0.,z],[10.,0.,z],[0.,10.,z]]), np.array([[0,1,2]]),
            (0,0,z-1), (10,10,z+1))

    def placement(self, raw_offset=0):
        observation = height_observation({FIELDS[0]:130, FIELDS[1]:raw_offset})
        return {'x':16386., 'y':16386., 'height_observation':observation,
                'height_association':'same_native_placement_event_and_entity_callback'}

    def test_raw_entity_z_distinguishes_one_compatible_layer_without_selecting_ground(self):
        result = compare_placement(self.placement(), [self.mesh(128,0),self.mesh(256,1),self.mesh(260,2,1)])
        self.assertEqual(result['candidate_local_xyz'], [2.,2.,256.])
        self.assertEqual(result['collision_z_candidates'], [128.,256.])
        self.assertEqual(result['surfaces_within_tolerance'], [256.])
        self.assertEqual(result['nearest_collision_distance'], 0.)
        self.assertIsNone(result['selected_ground_z'])

    def test_different_retained_entity_origin_does_not_auto_snap(self):
        result = compare_placement(self.placement(24), [self.mesh(256,0)])
        self.assertEqual(result['nearest_collision_distance'], 24.)
        self.assertEqual(result['surfaces_within_tolerance'], [])
        self.assertIsNone(result['selected_ground_z'])

    def test_missing_and_arithmetically_inconsistent_native_height(self):
        self.assertEqual(compare_placement({},[]), {'status':'raw_height_unavailable'})
        invalid = self.placement()
        invalid['height_observation']['raw_z'] += 1
        with self.assertRaisesRegex(ValueError,'ward_height_observation_math'):
            compare_placement(invalid,[])
