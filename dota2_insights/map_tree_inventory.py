"""Static tree origins from an explicit Source 2 Viewer entity-lump export.

No runtime tree IDs, cut/respawn state, collision radius or ward placement
requirements are inferred from compiled entity identities or proximity.
"""
import math
import re


MAX_TEXT_BYTES=32*1024**2
VALUES=re.compile(r'(?ms)^\t{4}values =\s*\r?\n\t{4}\{\r?\n(.*?)^\t{4}\}')
CLASS=re.compile(r'^\t{5}classname = "([a-zA-Z0-9_]+)"\s*$',re.M)
ORIGIN=re.compile(r'^\t{5}origin = \[ ([^\]\r\n]+) \]\s*$',re.M)


def extract(text,resource):
    if (not isinstance(text,str) or len(text.encode('utf-8'))>MAX_TEXT_BYTES
            or not isinstance(resource,str) or not resource.startswith('maps/')
            or not resource.endswith('.vents_c') or '..' in resource.split('/')
            or '\\' in resource or '\x00' in resource):
        raise ValueError('tree_entity_input_invalid')
    if '<!-- kv3 encoding:text:' not in text or '\tm_entityKeyValues = ' not in text:
        raise ValueError('unsupported_tree_entity_export')
    blocks=VALUES.findall(text)
    if len(blocks)>20000:raise ValueError('tree_entity_count_limit')
    trees=[];all_classes=[]
    for index,block in enumerate(blocks):
        classes=CLASS.findall(block)
        if len(classes)!=1:raise ValueError('tree_entity_class_ambiguous')
        name=classes[0];all_classes.append(name)
        if name!='ent_dota_tree':continue
        origins=ORIGIN.findall(block)
        if len(origins)!=1:raise ValueError('tree_entity_origin_missing_or_duplicate')
        try:xyz=[float(n.strip()) for n in origins[0].split(',')]
        except ValueError as exc:raise ValueError('tree_entity_origin_invalid') from exc
        if len(xyz)!=3 or any(not math.isfinite(n) or abs(n)>32768 for n in xyz):
            raise ValueError('tree_entity_origin_invalid')
        compile_id=re.findall(r'^\t{5}compile_source_id = ([0-9]+)\s*$',block,re.M)
        hammer=re.findall(r'^\t{5}hammerUniqueId = "([0-9]+)"\s*$',block,re.M)
        model=re.findall(r'^\t{5}model = resource_name:"([^"\r\n]*)"\s*$',block,re.M)
        if any(len(values)>1 for values in (compile_id,hammer,model)):
            raise ValueError('tree_entity_metadata_ambiguous')
        trees.append({'id':resource+':entity-'+str(index),'entity_ordinal':index,
            'classname':name,'origin_xyz':xyz,
            'compile_source_id':int(compile_id[0]) if compile_id else None,
            'hammer_unique_id':hammer[0] if hammer else None,
            'model':model[0] if model else None,'runtime_tree_id':None,
            'alive_at_ward_time':None,'requires_cutting_for_ward':None})
    # Refuse incomplete/changed indentation exports instead of reporting zero.
    declared=re.findall(r'^\s*classname = "ent_dota_tree"\s*$',text,re.M)
    if len(declared)!=len(trees):raise ValueError('tree_export_unparsed_entities')
    if not blocks and re.search(r'\t\tm_connections =',text):
        raise ValueError('tree_export_unparsed_entities')
    return {'schema_version':1,'kind':'static_map_tree_origins',
            'resource':resource,'coordinate_basis':'source2_map_origin_xyz_centered',
            'entity_count':len(blocks),'tree_count':len(trees),'trees':trees,
            'runtime_tree_id_mapping_verified':False,'map_compatibility_verified':False,
            'ward_clearance_verified':False}
