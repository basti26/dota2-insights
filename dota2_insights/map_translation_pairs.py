"""Named static-object replay/asset pairs, with no pixel or window fitting."""
from dataclasses import asdict
import re

from .map_evaluation import translation_review, vector
from .replay_archive import GEM_COMMIT, GEM_VERSION, COORDINATE_BASIS
from .virtual_map import MapIdentity


CLASSES = {'CDOTA_BaseNPC_Tower': 'npc_dota_tower', 'CDOTA_BaseNPC_Fort': 'npc_dota_fort',
           'CDOTA_BaseNPC_Watch_Tower': 'npc_dota_watch_tower'}


def canonical(name, classname):
    if not isinstance(name, str):
        raise ValueError('translation_object_name_required')
    value = name.removeprefix('[PR#]')
    patterns = {'npc_dota_tower': r'dota_(goodguys|badguys)_tower[1-4]_(top|mid|bot)',
                'npc_dota_fort': r'dota_(goodguys|badguys)_fort',
                'npc_dota_watch_tower': r'npc_dota_watch_tower_(top|bottom)'}
    if classname not in patterns or re.fullmatch(patterns[classname], value) is None:
        raise ValueError('translation_explicit_object_name_class_mismatch')
    return value


def pair(extraction, assets, identity):
    identity = MapIdentity(**identity)
    if (type(extraction.get('schema_version')) is not int or extraction['schema_version'] != 1
            or extraction.get('kind') != 'offline_replay_stationary_landmarks'
            or extraction.get('source_unchanged') is not True or extraction.get('parse_error') is not None
            or extraction.get('coordinate_basis') != COORDINATE_BASIS
            or extraction.get('parser', {}).get('version') != GEM_VERSION
            or extraction['parser'].get('upstream_commit') != GEM_COMMIT):
        raise ValueError('translation_replay_extraction_not_admitted')
    mid = extraction.get('match_id')
    if not isinstance(mid, str) or not mid.isascii() or not mid.isdigit() or not 1 <= len(mid) <= 20:
        raise ValueError('translation_match_identity_required')
    if (type(extraction.get('tick')) is not int or extraction['tick'] < 0
            or not 0 <= vector([extraction.get('game_time_s')], 1)[0] <= 1):
        raise ValueError('translation_initial_stationary_window_required')
    if not isinstance(assets, list) or not 1 <= len(assets) <= 256:
        raise ValueError('translation_asset_budget')
    by_name = {}
    for asset in assets:
        if asset['classname'] not in CLASSES.values():
            continue
        name = canonical(asset['targetname'], asset['classname'])
        if name in by_name:
            raise ValueError('translation_asset_name_ambiguous')
        vector(asset['origin_xyz'], 3)
        by_name[name] = asset
    rows = extraction['landmarks']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise ValueError('translation_replay_landmark_budget')
    names, entities, pairs, unmatched = set(), set(), [], []
    for row in rows:
        class_name = CLASSES.get(row['class_name'])
        if class_name is None:
            raise ValueError('translation_unsupported_replay_class')
        name = canonical(row['name'], class_name)
        key = row['entity_index'], row['entity_serial']
        if (any(type(v) is not int or v < 0 for v in key) or key in entities or name in names):
            raise ValueError('translation_replay_identity_ambiguous')
        names.add(name); entities.add(key)
        raw = vector([row['world_x'], row['world_y']], 2)
        if type(row['team']) is not int or row['team'] not in (2, 3):
            raise ValueError('translation_team_invalid')
        if (('goodguys' in name and row['team'] != 2) or ('badguys' in name and row['team'] != 3)):
            raise ValueError('translation_name_team_disagree')
        asset = by_name.get(name)
        if asset is None:
            unmatched.append({'name': name, 'reason': 'no_exact_asset_name'})
            continue
        if asset['classname'] != class_name:
            raise ValueError('translation_asset_replay_class_disagree')
        pairs.append({'object_id': name, 'raw_xy': raw, 'world_xy': asset['origin_xyz'][:2],
                      'raw_source': extraction['source_sha256']+':'+str(row['entity_index'])+':'+str(row['entity_serial']),
                      'target_source': 'asset_entity_ordinal:'+str(asset['entity_ordinal']),
                      'same_object_confirmed': True, 'prediction_guided': False,
                      'confirmation_source': 'exact_explicit_target_name_and_class_not_nearest_coordinate',
                      'asset_targetname_raw': asset['targetname'], 'world_xyz': asset['origin_xyz'],
                      'entity_index': row['entity_index'], 'entity_serial': row['entity_serial'], 'team_raw': row['team']})
    return {'schema_version': 1, 'map_identity': asdict(identity),
            'source_frame': 'gem_cell128_plus_vec_raw', 'target_frame': 'source2_map_centered',
            'match_id': mid, 'replay_map_identity': None, 'whole_map_version_alignment_verified': False,
            'rows': pairs, 'unmatched_replay_objects': unmatched,
            'unused_asset_objects': sorted(set(by_name)-names),
            'scope': 'same_named_observed_static_objects_only_not_entire_map_or_GSI'}


def compare(extraction, assets, identity):
    data = pair(extraction, assets, identity)
    report = translation_review(data, {'minus16384': [-16384, -16384], 'zero': [0, 0]})
    return {'dataset': data, 'comparison': report}
