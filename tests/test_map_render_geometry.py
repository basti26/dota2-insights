import unittest
from copy import deepcopy
import numpy as np
from dota2_insights.map_render_geometry import world_node,terrain_instances,select_instances,source_positions,SharedVertices


class MapRenderGeometryTests(unittest.TestCase):
    def test_single_zero_origin_world_node_and_explicit_model_identity_transform(self):
        text='\tm_worldNodes =\n\t[\n\t\t{\n\t\t\tm_vOrigin = [ 0.0, 0.0, 0.0 ]\n\t\t\tm_worldNodePrefix = "maps\\\\dota\\\\worldnodes\\\\n0"\n\t\t},\n\t]'
        world=world_node(text);self.assertEqual(world['prefix'],'maps/dota/worldnodes/n0')
        with self.assertRaisesRegex(ValueError,'origin_requires_adapter'):world_node(text.replace('0.0, 0.0, 0.0','1.0, 0.0, 0.0'))
        prefix=world['prefix']
        objects='\tm_sceneObjects =\n\t[\n\t\t{\n\t\t\tm_vTransform =\n\t\t\t[\n'+''.join('\t\t\t\t[ '+r+' ],\n' for r in ['1, 0, 0, 0','0, 1, 0, 0','0, 0, 1, 0'])+'\t\t\t]\n\t\t\tm_renderableModel = resource:"'+prefix+'_lr0_c10_s_cb_mesh_worldspace_texture_blend.vmdl"\n\t\t},\n\t]'
        self.assertEqual(terrain_instances(objects,prefix)[0]['cell'],10)
        with self.assertRaisesRegex(ValueError,'transform_requires_adapter'):terrain_instances(objects.replace('[ 1, 0, 0, 0 ]','[ 1, 0, 0, 100 ]'),prefix)

    def test_fixed_nine_sample_policy_is_numeric_not_outcome_selected(self):
        rows=[{'cell':v} for v in range(124)]
        self.assertEqual([v['cell'] for v in select_instances(rows)],[0,15,31,46,62,77,92,108,123])
        with self.assertRaises(ValueError):select_instances(rows[:8])

    def fixture(self):
        source=np.array([[1,2,3],[4,5,6]],float)
        exported=(source[:,[1,2,0]]*.0254).astype('<f4').tobytes()
        d={'asset':{'version':'2.0'},'buffers':[{'byteLength':len(exported),'uri':'mesh.bin'}],
           'bufferViews':[{'buffer':0,'byteLength':len(exported)}],
           'accessors':[{'bufferView':0,'componentType':5126,'type':'VEC3','count':2}],
           'nodes':[{'mesh':0}], 'meshes':[{'primitives':[{'attributes':{'POSITION':0}}]}]}
        mdat='m_vMinBounds = [ 1, 2, 3 ]\nm_vMaxBounds = [ 4, 5, 6 ]'
        return source,exported,d,mdat

    def test_explicit_axis_units_recipe_checked_against_six_declared_bounds(self):
        source,buffer,d,mdat=self.fixture();p,r=source_positions(d,buffer,mdat)
        np.testing.assert_allclose(p,source,atol=1e-5)
        self.assertLess(r['maximum_bounds_difference_world_units'],1e-5)
        with self.assertRaisesRegex(ValueError,'coordinate_recipe_disagrees'):source_positions(d,buffer,mdat.replace('[ 4, 5, 6 ]','[ 5, 5, 6 ]'))

    def test_buffer_extents_sparse_and_gltf_transforms_refused(self):
        source,buffer,d,mdat=self.fixture()
        cases=[]
        bad=deepcopy(d);bad['accessors'][0]['count']=3;cases.append(bad)
        bad=deepcopy(d);bad['accessors'][0]['sparse']={'count':1};cases.append(bad)
        bad=deepcopy(d);bad['accessors'][0]['bufferView']=-1;cases.append(bad)
        bad=deepcopy(d);bad['nodes'][0]['translation']=[1,0,0];cases.append(bad)
        for bad in cases:
            with self.assertRaises(ValueError):source_positions(bad,buffer,mdat)

    def test_shared_vertex_check_handles_bucket_edges_and_retains_unmatched(self):
        shared=SharedVertices(np.array([[.0199,2,3],[4,5,6]]))
        result=shared.compare([[.0201,2,3],[.0201,2,3],[9,9,9]])
        self.assertEqual(result['distinct_render_vertices'],2)
        self.assertEqual(result['shared_within_0_01_world_units'],1)
        self.assertEqual(result['unmatched_positions'],[[9,9,9]])
        self.assertAlmostEqual(result['shared_distance_maximum'],.0002)
        self.assertFalse(result['runtime_world_placement_verified'])
        with self.assertRaises(ValueError):shared.compare([[float('nan'),0,0]])

    def test_inside_mismatch_not_hidden_by_decorative_outside_bounds(self):
        result=SharedVertices([[0,0,0],[5,5,5]]).compare([[.001,0,0],[1,1,1],[9,9,9]])
        self.assertEqual(result['shared_within_0_01_world_units'],1)
        self.assertEqual(result['unmatched_inside_collision_bounds'],1)
        self.assertEqual(result['unmatched_outside_collision_bounds'],1)
        with self.assertRaises(ValueError):SharedVertices(np.zeros((0,3)))
