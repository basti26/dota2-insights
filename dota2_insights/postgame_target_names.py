"""Parse recorded target class names without inventing positions or entity identity."""
from __future__ import annotations

import re

VERSION = 'recorded_target_class_name_attributes_v1'


def attributes(unit, family):
    result = {'status': 'missing_name' if unit is None else 'unrecognized_name',
              'kind': None, 'defending_team': None, 'lane': None,
              'tower_tier': None, 'barracks_type': None,
              'basis': 'unit_class_name_only_not_unique_entity_or_world_position'}
    if not isinstance(unit, str):
        return result
    tower = re.fullmatch(r'npc_dota_(goodguys|badguys)_tower([1-4])(?:_(top|mid|bot))?', unit)
    barracks = re.fullmatch(r'npc_dota_(goodguys|badguys)_(melee|range)_rax_(top|mid|bot)', unit)
    ancient = re.fullmatch(r'npc_dota_(goodguys|badguys)_fort', unit)
    if tower:
        result.update(kind='tower', defending_team=2 if tower[1] == 'goodguys' else 3,
                      tower_tier=int(tower[2]), lane=tower[3])
    elif barracks:
        result.update(kind='barracks', defending_team=2 if barracks[1] == 'goodguys' else 3,
                      barracks_type=barracks[2], lane=barracks[3])
    elif ancient:
        result.update(kind='ancient', defending_team=2 if ancient[1] == 'goodguys' else 3)
    elif unit == 'npc_dota_roshan':
        result['kind'] = 'roshan'
    else:
        return result
    expected = {'tower': ('tower_destroyed',), 'barracks': ('barracks_destroyed',),
                'ancient': ('ancient_destroyed',), 'roshan': ('roshan_kill', 'roshan_death_record')}
    result['status'] = 'parsed_name_family_consistent' if family in expected[result['kind']] else 'parsed_name_family_conflict'
    return result
