import unittest
from dota2_insights.map_tree_inventory import extract


def export(blocks):
    return '<!-- kv3 encoding:text:version{test} -->\n{\n\tm_entityKeyValues = \n\t[\n'+''.join(
        '\t\t{\n\t\t\tkeyValues3Data =\n\t\t\t{\n\t\t\t\tvalues = \n\t\t\t\t{\n'+b+'\n\t\t\t\t}\n\t\t\t}\n\t\t}\n'
        for b in blocks)+'\t]\n}'


TREE='\t\t\t\t\tclassname = "ent_dota_tree"\n\t\t\t\t\torigin = [ -2560.0, -8704.0, 128.0 ]\n\t\t\t\t\tcompile_source_id = 404\n\t\t\t\t\thammerUniqueId = "85097"'
RESOURCE='maps/dota/entities/default_ents.vents_c'


class TreeInventoryTests(unittest.TestCase):
    def test_only_tree_class_is_exported_and_compile_identity_is_not_runtime_id(self):
        other=TREE.replace('ent_dota_tree','prop_dynamic')
        result=extract(export([other,TREE]),RESOURCE)
        self.assertEqual(result['entity_count'],2);self.assertEqual(result['tree_count'],1)
        tree=result['trees'][0]
        self.assertEqual(tree['origin_xyz'],[-2560.,-8704.,128.])
        self.assertEqual(tree['entity_ordinal'],1)
        self.assertEqual(tree['compile_source_id'],404)
        for key in ('runtime_tree_id','alive_at_ward_time','requires_cutting_for_ward'):
            self.assertIsNone(tree[key])

    def test_origins_are_scoped_to_tree_not_neighbouring_entity(self):
        missing=TREE.replace('\t\t\t\t\torigin = [ -2560.0, -8704.0, 128.0 ]\n','')
        with self.assertRaises(ValueError):extract(export([missing,TREE]),RESOURCE)

    def test_nonfinite_wrong_dimensions_and_duplicate_origins_are_refused(self):
        for value in ('nan, 1, 2','1, 2','40000, 2, 3','inf, 2, 3'):
            with self.assertRaises(ValueError):extract(export([TREE.replace('-2560.0, -8704.0, 128.0',value)]),RESOURCE)
        with self.assertRaises(ValueError):extract(export([TREE+'\n\t\t\t\t\torigin = [ 1, 2, 3 ]']),RESOURCE)

    def test_format_change_does_not_silently_report_zero_trees(self):
        with self.assertRaises(ValueError):extract(export([TREE]).replace('\t','  '),RESOURCE)

    def test_duplicate_origins_keep_distinct_entity_evidence(self):
        result=extract(export([TREE,TREE]),RESOURCE)
        self.assertEqual(result['tree_count'],2)
        self.assertNotEqual(result['trees'][0]['id'],result['trees'][1]['id'])

    def test_empty_lump_and_resource_path_guards(self):
        self.assertEqual(extract(export([]),RESOURCE)['tree_count'],0)
        for resource in ('../default.vents_c','maps/../default.vents_c','maps/test.vwnod_c'):
            with self.assertRaises(ValueError):extract(export([TREE]),resource)
