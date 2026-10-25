"""Bounded offline building-entity observations; no cross-replay map inference."""
from __future__ import annotations

import math
import re

SCHEMA = 'replay_building_entity_landmarks_v1'
CLASSES = ('CDOTA_BaseNPC_Tower', 'CDOTA_BaseNPC_Barracks',
           'CDOTA_BaseNPC_Fort', 'CDOTA_BaseNPC_Watch_Tower')
POSITION_FIELDS = ('CBodyComponent.m_cellX', 'CBodyComponent.m_cellY',
                   'CBodyComponent.m_vecX', 'CBodyComponent.m_vecY')


class LandmarkLimit(ValueError):
    pass


def entity_name_attributes(name):
    """Explicit entity-table names; keep four-tower top/bot as a locator, not a lane."""
    from .postgame_target_names import attributes
    canonical, family, locator = None, None, None
    if isinstance(name, str):
        tower = re.fullmatch(r'dota_(goodguys|badguys)_tower([1-4])_(top|mid|bot)', name)
        rax = re.fullmatch(r'(good|bad)_rax_(melee|range)_(top|mid|bot)', name)
        fort = re.fullmatch(r'dota_(goodguys|badguys)_fort', name)
        if tower:
            canonical = 'npc_dota_' + tower[1] + '_tower' + tower[2]
            if tower[2] == '4':
                locator = tower[3]
            else:
                canonical += '_' + tower[3]
            family = 'tower_destroyed'
        elif rax:
            canonical = 'npc_dota_' + ('goodguys' if rax[1] == 'good' else 'badguys')
            canonical += '_' + rax[2] + '_rax_' + rax[3]
            family = 'barracks_destroyed'
        elif fort:
            canonical, family = 'npc_' + name, 'ancient_destroyed'
    result = attributes(canonical, family)
    result.update(entity_name_raw=name, candidate_combat_target_name=canonical,
                  entity_locator_suffix=locator,
                  join_basis='explicit_entity_name_alias_candidates_not_combat_entity_identity')
    if canonical is None:
        result['status'] = 'unrecognized_entity_name'
    return result


class Collector:
    """Read completed packets, retaining raw name/position evidence and entity serials."""

    def __init__(self, parser):
        self.parser = parser
        self.pending = {}
        self.rows = []
        self.last = {}
        self.classes = set()
        self.deleted = 0
        parser._on_entity_filtered(self.entity, class_names=CLASSES)
        parser._on_packet_end(self.packet)

    def entity(self, entity, operation):
        from gem.state.entities import EntityOp
        name = entity.get_class_name()
        if name not in CLASSES:
            return
        self.classes.add(name)
        key = (entity.get_index(), entity.get_serial())
        if operation.has(EntityOp.DELETED):
            self.pending.pop(key, None)
            self.deleted += 1
        else:
            self.pending[key] = entity
        if len(self.pending) > 128 or len(self.classes) > 32:
            raise LandmarkLimit('building_entity_budget')

    def packet(self, *_args):
        from gem.extractors._snapshots import _hero_npc_name, _pos
        table = self.parser.string_tables.get_by_name('EntityNames')
        for key, entity in sorted(self.pending.items()):
            npc = _hero_npc_name(entity, table)
            # A class name is only a callback filter, not proof of a Dota building.
            parsed = entity_name_attributes(npc)
            raw = {name: entity.get(name) for name in POSITION_FIELDS}
            valid = all(type(v) in (int, float) and math.isfinite(v) for v in raw.values())
            pos = _pos(entity) if valid else None
            if pos is not None and not all(math.isfinite(v) for v in pos):
                raise LandmarkLimit('building_nonfinite_position')
            state = (npc, tuple(raw.values()), entity.get('m_iTeamNum'), entity.get('m_lifeState'))
            if self.last.get(key) == state:
                continue
            self.last[key] = state
            self.rows.append({'entity_index': key[0], 'entity_serial': key[1],
                             'class_name': entity.get_class_name(), 'unit_name': npc,
                             'name_indices_raw': {field: entity.get(field) for field in
                                 ('m_pEntity.m_nameStringTableIndex', 'm_pEntity.m_nameStringableIndex')},
                             'packet_tick': self.parser.tick,
                             'game_time_s': self.parser.game_clock.game_time_at(self.parser.tick),
                             'team_raw': state[2], 'life_state_raw': state[3],
                             'position_status': 'recorded' if pos is not None else 'missing_components',
                             'position_components': raw,
                             'world_x': pos[0] if pos is not None else None,
                             'world_y': pos[1] if pos is not None else None,
                             'coordinate_basis': 'gem_cell128_plus_vec_raw_coordinates',
                             'observation_basis': 'completed_packet_entity_state_not_combat_log_location',
                             'name_attributes': parsed})
            if len(self.rows) > 512:
                raise LandmarkLimit('building_record_budget')
