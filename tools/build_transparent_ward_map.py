"""Build a full transparent terrain scene from a verified installed map asset."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_collision import vertical_hits
from dota2_insights.minimap.offline_io import checked_match, new_binary_output
from dota2_insights.valve_resources import directory_bytes, read_vpk_directory, _read_resource
from tools.audit_ward_height_origin import read_bounded, read_mesh
from tools.build_ward_surface_layer import digest, glb, patch_for
from tools.prepare_ward_world_layer import verified_replay_packet, ward_world_anchor


def installed_terrain(game):
    version = read_bounded(game / 'steam.inf', 64 * 1024)
    builds = [line.split('=', 1)[1] for line in version.decode('utf-8-sig').splitlines()
              if line.startswith('ClientVersion=')]
    if len(builds) != 1 or not builds[0].isascii() or not builds[0].isdecimal():
        raise ValueError('transparent_map_installed_build')
    source = game / 'maps/dota.vpk'
    package = directory_bytes(source)
    entries, start = read_vpk_directory(package)
    entry = entries['maps/dota/world_physics.vmdl_c']
    if entry.archive_index != 0x7FFF:
        raise ValueError('transparent_map_physics_must_be_embedded')
    resource, _ = _read_resource(source, entry, start, 16 * 1024**2)
    if directory_bytes(source) != package or read_bounded(game / 'steam.inf', 64 * 1024) != version:
        raise ValueError('transparent_map_source_changed')
    return {'client_build': int(builds[0]), 'map_asset_sha256': digest(package)}, digest(resource)


def replay_anchors(path, identity, mesh_sha):
    raw = read_bounded(path, 4 * 1024**2)
    if read_bounded(path.with_name('COMPLETE'), 256).decode('ascii').strip() != digest(raw):
        raise ValueError('transparent_map_surface_receipt')
    layer = json.loads(raw)
    source_match = checked_match(layer['source_match_id'])
    bound_identity = layer['map_identity']
    if (layer['kind'] != 'ward_static_terrain_review_scene' or bound_identity.get('map_name') != 'dota'
            or any(bound_identity.get(key) != value for key, value in identity.items())
            or layer['mesh_archive_sha256'] != mesh_sha or not 1 <= len(layer['anchors']) <= 5000):
        raise ValueError('transparent_map_surface_identity')
    return source_match, layer['anchors']


def demo_anchors(ground):
    """Spread fixed diagnostic points across the map without borrowing historical wards."""
    for y in (-5500, 0, 5500):
        for x in (-5500, 0, 5500):
            for dx, dy in ((0, 0), (160, 0), (0, 160), (-160, 0), (0, -160)):
                xy = [x + dx, y + dy]
                levels = {r['height'] for r in vertical_hits(ground, *xy)['hits']}
                if len(levels) != 1:
                    continue
                z = levels.pop()
                candidate = {'id': f'test_{x}_{y}', 'ward_type': 'observer', 'world_xy': xy,
                             'world_z_candidate': z, 'rasterized': True}
                if patch_for(candidate, ground)[1] is not None:
                    yield candidate
                    break


def prepare(game, collision, out, *, surface=None, demo=False, replay_build=None, ward_directory=None):
    if sum([surface is not None, demo, replay_build is not None]) > 1:
        raise ValueError('transparent_map_ambiguous_ward_source')
    if (replay_build is None) != (ward_directory is None):
        raise ValueError('transparent_map_replay_receipt_and_wards_required')
    identity, resource_sha = installed_terrain(game)
    meta, meshes = read_mesh(collision)
    if (identity['map_asset_sha256'] != meta['map_asset_sha256']
            or resource_sha != meta['resource_sha256']):
        raise ValueError('transparent_map_terrain_mismatch')
    ground = [mesh for mesh in meshes if mesh.attribute_index == 0]
    if not ground:
        raise ValueError('transparent_map_empty_terrain')

    source_match, anchors = None, []
    source = {}
    omitted = []
    if surface is not None:
        source_match, anchors = replay_anchors(surface, identity, meta['mesh_archive_sha256'])
    elif replay_build is not None:
        packet, receipt_sha = verified_replay_packet(replay_build,ward_directory,identity['client_build'])
        source_match = packet['match_id']
        anchors = [ward_world_anchor(point) for point in packet['points']]
        omitted = list(packet['unavailable'])
        source = {'source_replay_sha256': packet['source_replay_sha256'],
                  'source_wards_sha256': packet['source_wards_sha256'],
                  'source_build_receipt_sha256': receipt_sha,
                  'source_replay_placement_count': packet['source_ward_count']}
    elif demo:
        anchors = list(demo_anchors(ground))

    patches, admitted = [], []
    for anchor in anchors:
        if anchor['ward_type'] not in ('observer', 'sentry'):
            raise ValueError('transparent_map_ward_type')
        # Recompute ground attachment from the bound mesh, never from screen pixels.
        status, patch = patch_for({**anchor, 'rasterized': True}, ground)
        if patch is None:
            omitted.append({'id':anchor['id'],'reason':status})
            continue
        patches.append((anchor, patch))
        admitted.append({'id': anchor['id'], 'ward_type': anchor['ward_type'],
                         'world_xyz': patch['vertex_world_xyz'][4],
                         'footprint_world_xyz': patch['vertex_world_xyz'][:4]})
    if len({ward['id'] for ward in admitted}) != len(admitted):
        raise ValueError('transparent_map_duplicate_ward_ids')

    scene = glb(ground, patches, transparent_terrain=True)
    lower = np.min([mesh.vertices.min(0) for mesh in ground], axis=0).tolist()
    upper = np.max([mesh.vertices.max(0) for mesh in ground], axis=0).tolist()
    manifest = {'schema_version': 1, 'kind': 'transparent_ward_map', 'map_identity': identity,
                'geometry_extracted_from_client_build': meta['client_build'],
                'installed_physics_sha256': resource_sha, 'mesh_archive_sha256': meta['mesh_archive_sha256'],
                'geometry_coordinate_basis': meta['coordinate_basis'], 'scene_sha256': digest(scene),
                'scene_axes_from_map_xyz': ['x', 'z', '-y'], 'terrain_opacity': 0,
                'terrain_bounds_xyz': [lower, upper],
                'terrain_triangles': sum(len(mesh.triangles) for mesh in ground),
                'ward_source': 'synthetic_test_points' if demo else ('exact_build_replay' if source_match else 'none'),
                'source_match_id': source_match, 'input_anchor_count': len(anchors), 'anchors': admitted,
                'unavailable': omitted, **source,
                'runtime_world_placement_verified': False, 'runtime_camera_alignment_verified': False,
                'live_usable': False}
    out.mkdir(parents=True, exist_ok=False)
    receipt = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    for name, body in (('transparent-ward-map.glb', scene), ('map.json', receipt),
                       ('COMPLETE', (hashlib.sha256(receipt).hexdigest() + '\n').encode('ascii'))):
        with new_binary_output(out / name) as stream:
            stream.write(body)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('game', 'collision', 'output'):
        parser.add_argument(f'--{name}', type=Path, required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--surface', type=Path)
    source.add_argument('--demo', action='store_true')
    source.add_argument('--replay-build', type=Path)
    parser.add_argument('--ward-directory',type=Path)
    args = parser.parse_args()
    if (args.replay_build is None) != (args.ward_directory is None):
        parser.error('--replay-build and --ward-directory must be supplied together')
    result = prepare(args.game.resolve(strict=True), checked_input(ROOT, args.collision),
                     checked_output(ROOT, args.output),
                     surface=checked_input(ROOT, args.surface) if args.surface else None, demo=args.demo,
                     replay_build=checked_input(ROOT,args.replay_build) if args.replay_build else None,
                     ward_directory=checked_input(ROOT,args.ward_directory) if args.ward_directory else None)
    print(json.dumps({'map_identity': result['map_identity'], 'terrain_triangles': result['terrain_triangles'],
                      'wards': len(result['anchors']), 'ward_source': result['ward_source']}))


if __name__ == '__main__':
    main()
