"""Metadata inventory of compiled map resources, not a decoded terrain model."""
from collections import Counter
import hashlib
import struct


def inventory(entries):
    root_names = {'maps/dota.vmap_c', 'maps/dota.gnv', 'maps/dota.vhcg', 'maps/dota.trm'}
    selected = [entry for name, entry in sorted(entries.items()) if name.startswith('maps/dota/') or name in root_names]
    return {'resource_count': len(selected),
        'map_folder_resource_count': sum(e.resource.startswith('maps/dota/') for e in selected),
        'map_root_resources': [e.resource for e in selected if e.resource in root_names],
        'extensions': dict(Counter(e.resource.rsplit('.', 1)[-1] for e in selected)),
        'families': {name: [e.resource for e in selected if e.resource.endswith(extension)]
            for name, extension in [('worlds', '.vwrld_c'), ('world_nodes', '.vwnod_c'),
                ('models', '.vmdl_c'), ('meshes', '.vmesh_c'), ('physics', '.vphys_c'),
                ('navigation', '.vnav_c'), ('entities', '.vents_c'), ('textures', '.vtex_c')]},
        'physics_model_resources': [e.resource for e in selected if e.resource.endswith('/world_physics.vmdl_c')],
        'navigation_helper_models': [e.resource for e in selected if e.resource.endswith('_dota_nav_walkable.vmdl_c')],
        'resources': [{'path': e.resource, 'compiled_bytes': e.length + len(e.preload),
            'crc32': f'{e.crc32:08x}', 'archive_index': e.archive_index} for e in selected],
        'terrain_geometry_decoded': False, 'walkable_height_grid_verified': False}


def compiled_blocks(raw):
    if not isinstance(raw, bytes) or not 16 <= len(raw) <= 32 * 1024**2:
        raise ValueError('compiled_map_resource_size')
    declared_size, header_version, resource_version, offset, count = struct.unpack_from('<IHHII', raw)
    start = 8 + offset
    if header_version != 12 or not 0 < count <= 128 or start < 16 or start + count * 12 > len(raw):
        raise ValueError('compiled_map_resource_header')
    blocks = []
    for index in range(count):
        position = start + index * 12
        tag, relative, size = struct.unpack_from('<4sII', raw, position)
        location = position + 4 + relative
        if not tag.isalnum() or location < 0 or location + size > len(raw):
            raise ValueError('compiled_map_resource_block')
        blocks.append({'tag': tag.decode('ascii'), 'offset': location, 'bytes': size})
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'actual_bytes': len(raw),
        'declared_resource_bytes': declared_size, 'header_version': header_version,
        'resource_version': resource_version, 'blocks': blocks, 'geometry_decoded': False}
