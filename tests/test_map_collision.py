import struct
import unittest
import numpy as np

from dota2_insights.map_collision import extract, summarize, vertical_hits


def export_mesh(vertices, triangles, attribute=0):
    def binary(name, raw):
        return '\t'*7 + name + ' = \n' + '\t'*7 + '#[\n' + '\t'*8 + raw.hex(' ').upper() + '\n' + '\t'*7 + ']\n'
    array = np.asarray(vertices, dtype='<f4')
    minimum, maximum = array.min(axis=0)-.04, array.max(axis=0)+.04
    def bounds(name, values):
        return '\t'*7 + name + ' = [ ' + ', '.join(map(str, values)) + ' ]\n'
    return ('\t'*6 + f'm_nCollisionAttributeIndex = {attribute}\n' + '\t'*6 + 'm_nSurfacePropertyIndex = 0\n'
        + '\t'*6 + 'm_Mesh = \n' + '\t'*6 + '{\n' + bounds('m_vMin', minimum) + bounds('m_vMax', maximum)
        + binary('m_Triangles', np.asarray(triangles, dtype='<u4').tobytes())
        + binary('m_Vertices', array.tobytes()) + '\t'*6 + '}\n')


def export(*meshes):
    text = ('--- Data for block "PHYS" ---\n<!-- kv3 encoding:text:generated -->\n{\n'
        '\tm_bindPose = [  ]\n\tm_boneNames = [  ]\n\t\t\tm_rnShape = \n')
    for field in ('m_spheres', 'm_capsules', 'm_hulls', 'm_compounds'):
        text += '\t'*4 + field + ' = [  ]\n'
    text += ''.join(meshes) + '\tm_collisionAttributes = \n\t[\n'
    for group, names in [('default', []), ('conditionallysolid', ['water'])]:
        text += '\t\t{\n' + '\t'*3 + f'm_CollisionGroupString = "{group}"\n'
        for field in ('m_InteractAsStrings', 'm_InteractWithStrings', 'm_InteractExcludeStrings'):
            values = names if field == 'm_InteractAsStrings' else []
            text += '\t'*3 + field + ' = [ ' + ', '.join('"'+v+'"' for v in values) + ' ]\n'
        text += '\t\t},\n'
    return text + '\t]\n}\n'


class CollisionTests(unittest.TestCase):
    def test_two_layers_preserved_without_choosing_ground(self):
        base = [[0,0,0],[1,0,0],[0,1,0]]
        high = [[0,0,128],[1,0,128],[0,1,128]]
        meshes, attrs = extract(export(export_mesh(base, [[0,1,2]]), export_mesh(high, [[0,1,2]], 1)))
        result = vertical_hits(meshes, .25, .25)
        self.assertEqual([r['height'] for r in result['hits']], [0,128])
        self.assertIsNone(result['ground_height'])
        self.assertEqual(attrs[1]['interact_as'], ['water'])
        self.assertEqual(len(vertical_hits(meshes, .25, .25, attribute_indices={0})['hits']), 1)
        self.assertEqual(vertical_hits(meshes, 2, 2)['hits'], [])
        self.assertFalse(summarize(meshes, attrs)['world_placement_verified'])

    def test_shared_edges_deduplicate_height_but_keep_hit_count(self):
        m, _ = extract(export(export_mesh([[0,0,5],[1,0,5],[0,1,5],[1,1,5]], [[0,1,2],[1,3,2]])))
        result = vertical_hits(m,.5,.5)
        self.assertEqual(len(result['hits']),1)
        self.assertEqual(result['hits'][0]['triangle_hits_at_height'],2)
        self.assertEqual(result['hits'][0]['height'],5)

    def test_vertical_face_is_not_a_height_surface(self):
        m, _ = extract(export(export_mesh([[0,0,0],[0,1,0],[0,1,5]], [[0,1,2]])))
        self.assertEqual(vertical_hits(m,0,.5)['hits'],[])

    def test_invalid_index_and_declared_bounds_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'index_outside'):
            extract(export(export_mesh([[0,0,0],[1,0,0],[0,1,0]], [[0,1,3]])))
        text = export(export_mesh([[0,0,0],[1,0,0],[0,1,0]], [[0,1,2]]))
        altered = text.replace('m_vMax = [ 1.04, 1.04, 0.04 ]', 'm_vMax = [ 0.1, 0.1, 0.04 ]')
        with self.assertRaisesRegex(ValueError,'declared_bounds'):
            extract(altered)

    def test_truncated_invalid_or_repeated_arrays_are_never_zero_geometry(self):
        text = export(export_mesh([[0,0,0],[1,0,0],[0,1,0]], [[0,1,2]]))
        with self.assertRaisesRegex(ValueError,'incomplete|attribute'):
            extract(text[:text.index('m_Vertices')])
        with self.assertRaisesRegex(ValueError,'hex_buffer'):
            extract(text.replace('00 00 00 00', '00 000 00 00',1))
        with self.assertRaisesRegex(ValueError,'duplicate'):
            extract(text.replace('\t'*6+'}', '\t'*7+'m_Vertices = \n'+'\t'*6+'}',1))
        with self.assertRaisesRegex(ValueError,'rigged'):
            extract(text.replace('m_bindPose = [  ]', 'm_bindPose = [ 1 ]'))

    def test_query_type_and_result_budget(self):
        m,_=extract(export(export_mesh([[0,0,0],[1,0,0],[0,1,0]],[[0,1,2]]),
            export_mesh([[0,0,1],[1,0,1],[0,1,1]],[[0,1,2]],1)))
        result=vertical_hits(m,.1,.1,maximum_results=1)
        self.assertEqual(result['omitted_distinct_hits'],1)
        for x in (True,float('nan'),float('inf')):
            with self.assertRaises(ValueError):vertical_hits(m,x,0)
