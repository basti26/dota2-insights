"""Strict observed map-root layouts; raw grid flags have no certified semantics.

These parsers are derived from bounded local bytes and cross-checked with
separate asset representations. They do not infer live visibility/navigation.
"""
from collections import Counter
import math
import struct

from .map_assets import compiled_blocks


def external_references(raw):
    metadata = compiled_blocks(raw)
    blocks = [b for b in metadata['blocks'] if b['tag'] == 'RERL']
    if len(blocks) != 1:
        raise ValueError('map_rerl_required')
    block = blocks[0]
    begin, end = block['offset'], block['offset']+block['bytes']
    if end-begin < 8:
        raise ValueError('map_rerl_header')
    relative, count = struct.unpack_from('<II', raw, begin)
    table = begin+relative
    if not 1 <= count <= 10000 or table < begin+8 or table+16*count > end:
        raise ValueError('map_rerl_table')
    refs, ids = [], set()
    for i in range(count):
        position = table+i*16
        identifier, relative_name = struct.unpack_from('<Qq', raw, position)
        name_start = position+8+relative_name
        if identifier in ids or not table+16*count <= name_start < end:
            raise ValueError('map_rerl_reference')
        ids.add(identifier)
        terminator = raw.find(b'\0', name_start, min(end, name_start+2049))
        if terminator < 0:
            raise ValueError('map_rerl_string')
        try: name = raw[name_start:terminator].decode('utf-8')
        except UnicodeDecodeError as exc: raise ValueError('map_rerl_string') from exc
        if not name or '\n' in name or '\r' in name:
            raise ValueError('map_rerl_string')
        refs.append({'id_hex': f'{identifier:016X}', 'resource': name})
    return refs


def grid(raw):
    import numpy as np
    if not isinstance(raw, bytes) or not 32 <= len(raw) <= 4*1024**2:
        raise ValueError('map_gnv_size')
    magic, cell, offset_x, offset_y, width, height, minimum_x, minimum_y = struct.unpack_from('<IfffIIii', raw)
    if (magic != 0xFADEBEAD or not all(math.isfinite(v) for v in (cell, offset_x, offset_y))
            or not 16 <= cell <= 1024 or offset_x != cell/2 or offset_y != cell/2
            or not 1 <= width <= 2048 or not 1 <= height <= 2048 or len(raw) != 32+width*height
            or max(abs(minimum_x*cell), abs(minimum_y*cell), abs((minimum_x+width)*cell), abs((minimum_y+height)*cell)) > 65536):
        raise ValueError('map_gnv_observed_layout_unsupported')
    values = np.frombuffer(raw, dtype='u1', offset=32).reshape(height, width)
    values.setflags(write=False)
    bounds = {'min_x': minimum_x*cell, 'min_y': minimum_y*cell,
        'max_x': (minimum_x+width)*cell, 'max_y': (minimum_y+height)*cell}
    return {'width': width, 'height': height, 'cell_candidate_units': cell,
        'cell_center_offsets': [offset_x, offset_y], 'minimum_cell_indices': [minimum_x, minimum_y],
        'xy_bounds_candidate': bounds, 'row_direction_candidate': 'increasing_world_y',
        'values': values, 'raw_byte_counts': {str(k): v for k,v in sorted(Counter(raw[32:]).items())},
        'cell_flag_semantics_verified': False, 'world_placement_verified': False}


def trm(raw):
    if not isinstance(raw, bytes) or not 64 <= len(raw) <= 4*1024**2:
        raise ValueError('map_trm_size')
    magic, version, names_offset, layer_count, count = struct.unpack_from('<4sIIII', raw)
    if (magic != b'trmp' or version != 1 or names_offset != 64 or not 1 <= layer_count <= 32
            or not 1 <= count <= 100000 or raw[20:64] != bytes(44)):
        raise ValueError('map_trm_observed_layout_unsupported')
    position, layers = names_offset, []
    for _ in range(layer_count):
        end = raw.find(b'\0', position, min(position+257, len(raw)))
        if end < 0: raise ValueError('map_trm_layer_names')
        try: name = raw[position:end].decode('ascii')
        except UnicodeDecodeError as exc: raise ValueError('map_trm_layer_names') from exc
        if (name and (not name.startswith('world_layer_') or not all(c.isalnum() or c=='_' for c in name))) or name in layers:
            raise ValueError('map_trm_layer_names')
        layers.append(name)
        position = end+1
    if len(raw)-position != count*12:
        raise ValueError('map_trm_record_bytes')
    points = []
    for i in range(count):
        x,y,layer = struct.unpack_from('<iii', raw, position+i*12)
        if not 0 <= layer < layer_count or max(abs(x),abs(y)) > 65536:
            raise ValueError('map_trm_record_invalid')
        points.append({'record_index': i, 'xy': [x,y], 'layer_index': layer})
    identities = [(p['xy'][0], p['xy'][1], p['layer_index']) for p in points]
    return {'kind': 'trm_integer_xy_layer_catalog', 'format_version': version, 'layers': layers,
        'points': points, 'record_count': count, 'unique_xy_layer_records': len(set(identities)),
        'duplicate_xy_layer_records': count-len(set(identities)),
        'runtime_tree_ids_verified': False, 'live_tree_states_available': False}


def sample_grid(candidate, x, y):
    """Raw value at candidate XY; outside remains unknown, never flag zero."""
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (x,y)):
        raise ValueError('map_gnv_query_invalid')
    bounds=candidate['xy_bounds_candidate']
    if not bounds['min_x'] <= x < bounds['max_x'] or not bounds['min_y'] <= y < bounds['max_y']:
        return None
    cell=candidate['cell_candidate_units']
    column=int(math.floor((x-bounds['min_x'])/cell))
    row=int(math.floor((y-bounds['min_y'])/cell))
    return {'column':column,'row':row,'raw_value':int(candidate['values'][row,column]),
        'semantics_verified':False,'xy_placement_verified':False}


def compare_trees(catalog, inventory):
    """Exact layer+XY multiset comparison. Preserve missing/extra/duplicates."""
    old = Counter()
    for t in inventory['trees']:
        layer = t['id'].split(':entity-')[0].rsplit('/',1)[1].removesuffix('.vents_c')
        layer = '' if layer == 'default_ents' else layer
        x,y,_ = t['origin_xyz']
        if not math.isfinite(x) or not math.isfinite(y): raise ValueError('map_tree_nonfinite')
        old[x,y,layer] += 1
    new = Counter((p['xy'][0], p['xy'][1], catalog['layers'][p['layer_index']]) for p in catalog['points'])
    def rows(counter):
        return [{'xy': [x,y], 'layer': layer, 'multiplicity': n} for (x,y,layer),n in sorted(counter.items())]
    return {'entity_tree_records': sum(old.values()), 'trm_records': sum(new.values()),
        'exact_xy_layer_matches': sum((old & new).values()),
        'entity_records_missing_from_trm': rows(old-new), 'trm_records_not_paired_to_entities': rows(new-old),
        'matching_scope': 'authored_static_XY_and_layer_only_not_live_tree_id_or_collision',
        'runtime_world_placement_verified': False}
