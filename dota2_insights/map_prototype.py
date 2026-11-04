"""Offline map comparison prototype data and shared display transform.

World-space markers stay separate from image observations and view state.
No game APIs, capture, network, replay parsing or production calibration.
"""
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .map_holdout import load, local, read_plan, save, sha
from .virtual_map import number, WorldBounds, MapIdentity, VirtualMap, WorldPoint, NormalizedPoint


@dataclass(frozen=True)
class ViewTransform:
    center_x: float = 0
    center_y: float = 0
    scale: float = .03
    width: int = 720
    height: int = 720
    map_model: VirtualMap | None = None

    def __post_init__(self):
        for value in (self.center_x, self.center_y, self.scale):
            number(value)
        if not 1e-5 <= self.scale <= 20 or any(type(v) is not int or not 32 <= v <= 8192 for v in (self.width, self.height)):
            raise ValueError('prototype_view_invalid')
        if self.map_model is not None and not isinstance(self.map_model, VirtualMap):
            raise ValueError('prototype_view_map_required')

    def _norm(self, x, y):
        model = self.map_model
        return model.world_to_norm(WorldPoint(x, y, model.identity, model.coordinate_frame),
            allow_unverified=True, allow_outside=True)

    def pixel(self, x, y):
        if self.map_model is not None:
            p, center = self._norm(x, y), self._norm(self.center_x, self.center_y)
            b = self.map_model.bounds
            return ((p.u-center.u)*(b.max_x-b.min_x)*self.scale+self.width/2,
                    (p.v-center.v)*(b.max_y-b.min_y)*self.scale+self.height/2)
        return ((number(x)-self.center_x)*self.scale+self.width/2,
                (self.center_y-number(y))*self.scale+self.height/2)

    def world(self, x, y):
        if self.map_model is not None:
            center, model = self._norm(self.center_x, self.center_y), self.map_model
            b = model.bounds
            p = model.norm_to_world(NormalizedPoint(center.u+(number(x)-self.width/2)/(self.scale*(b.max_x-b.min_x)),
                center.v+(number(y)-self.height/2)/(self.scale*(b.max_y-b.min_y)), model.model_id),
                allow_unverified=True, allow_outside=True)
            return p.x, p.y
        return ((number(x)-self.width/2)/self.scale+self.center_x,
                self.center_y-(number(y)-self.height/2)/self.scale)

    def pan(self, dx, dy):
        return replace(self, center_x=self.center_x-number(dx)/self.scale,
                       center_y=self.center_y+number(dy)/self.scale)

    def zoom(self, factor, x, y):
        factor = number(factor)
        if factor <= 0:
            raise ValueError('prototype_zoom_invalid')
        wx, wy = self.world(x, y)
        scale = min(1, max(.005, self.scale*factor))
        return replace(self, scale=scale, center_x=wx-(x-self.width/2)/scale,
                       center_y=wy+(y-self.height/2)/scale)


def read_complete(directory, filename):
    directory = Path(directory)
    target = directory/filename
    if (directory/'COMPLETE').read_text(encoding='ascii').strip() != sha(target):
        raise ValueError('prototype_source_receipt_changed')
    return load(target)


def render_collision(archive):
    """Near-horizontal mean-Z painter; explicitly not a depth or ground map."""
    import cv2
    import numpy as np
    from PIL import Image
    with np.load(archive, allow_pickle=False) as arrays:
        v, t = arrays['vertices_0'], arrays['triangles_0']
        if (v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3
                or len(v) > 1000000 or len(t) > 2000000 or not np.isfinite(v).all()
                or t.dtype.kind not in 'ui' or not len(t) or t.min() < 0 or t.max() >= len(v)):
            raise ValueError('prototype_mesh_invalid')
        triangles = v[t].astype(float)
    normal = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    length = np.linalg.norm(normal, axis=1)
    triangles = triangles[(length > 1e-8) & (np.abs(normal[:, 2]) > .65*length)]
    z = triangles[:, :, 2].mean(axis=1)
    side, lower, upper = 1024, -10000., 10500.
    # Pixel centers represent exact local XY at both ends of the texture.
    points = np.empty(triangles[:, :, :2].shape, dtype=np.int32)
    points[:, :, 0] = np.rint((triangles[:, :, 0]-lower)/(upper-lower)*(side-1)).astype(np.int32)
    points[:, :, 1] = np.rint((upper-triangles[:, :, 1])/(upper-lower)*(side-1)).astype(np.int32)
    levels = np.clip((z+128)/896*255, 0, 255).astype(np.uint8)
    colors = cv2.applyColorMap(levels[:, None], cv2.COLORMAP_TURBO)[:, 0]
    canvas = np.full((side, side, 3), (29, 23, 17), dtype=np.uint8)
    for i in np.argsort(z, kind='stable'):
        cv2.fillConvexPoly(canvas, points[i], tuple(map(int, colors[i])), lineType=cv2.LINE_8)
    # World-space footprint of pixel edges; keeps centers aligned under zoom.
    half = (upper-lower)/(side-1)/2
    return Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)), {
        'min_x': lower-half, 'min_y': lower-half, 'max_x': upper+half, 'max_y': upper+half}, len(triangles)


def load_ward_candidates(snapshot_path, receipt_path, match_id):
    """One explicit completed match from an already collected local snapshot."""
    import base64
    import hashlib
    import json
    from .replay_holdout import refuse_locked_match
    from .ward_reference_export import build
    refuse_locked_match(str(match_id))
    receipt = load(receipt_path)
    for row in receipt['matches']:
        refuse_locked_match(str(row['match_id']))
    if sha(snapshot_path) != receipt['snapshot_sha256']:
        raise ValueError('prototype_ward_snapshot_changed')
    selected = [r for r in receipt['matches'] if str(r['match_id']) == str(match_id)]
    if len(selected) != 1:
        raise ValueError('prototype_ward_match_not_admitted')
    snapshot = load(snapshot_path)
    for row in snapshot['matches']:
        refuse_locked_match(str(row['match_id']))
    rows = [r for r in snapshot['matches'] if str(r['match_id']) == str(match_id)]
    if len(rows) != 1:
        raise ValueError('prototype_ward_match_not_unique')
    manifest = json.loads(base64.b64decode(rows[0]['manifest_base64'], validate=True))
    raw = base64.b64decode(rows[0]['wards_base64'], validate=True)
    if (hashlib.sha256(raw).hexdigest() != selected[0]['wards_sha256']
            or manifest.get('source_sha256') != selected[0]['replay_sha256']
            or manifest.get('source_unchanged_stat') is not True
            or manifest.get('stop_reason') != 'replay_eof' or str(manifest.get('match_id')) != str(match_id)):
        raise ValueError('prototype_ward_receipt_binding')
    reference = build(manifest, raw)
    source_identity = manifest.get('map_identity')
    if source_identity is not None:
        source_identity = asdict(MapIdentity(**source_identity))
    wards = json.loads(raw)
    if any(w.get('coordinate_basis') != 'gem_cell128_plus_vec_raw_coordinates' for w in wards):
        raise ValueError('prototype_ward_coordinate_basis_unsupported')
    return {'match_id': str(match_id), 'source_map_identity': source_identity,
        'points': [{'id': p['id'], 'raw_world_xy': [p['x'], p['y']],
        'ward_type': p['ward_type'], 'team': p['team'], 'placed_game_time_s': p['placed_game_time_s']}
        for p in reference['points']], 'unavailable': reference['unavailable'],
        'raw_coordinate_frame': 'gem_cell128_plus_vec_raw', 'translation_candidate_xy': [-16384, -16384],
        'map_version_alignment_verified': False, 'world_projection_verified': False,
        'meaning': 'historical_all_placements_not_live_visible_wards_or_same_scene',
        'source_wards_sha256': reference['source_wards_sha256']}


def ward_candidate_xy(layer, point, model=None):
    if layer.get('map_binding') is not None:
        if model is None:
            raise ValueError('ward_projection_requires_target_model')
        from .ward_map import status
        if not status(layer, model)['preview_allowed']:
            raise ValueError('ward_source_map_mismatch')
    return tuple(number(point['raw_world_xy'][i])+number(layer['translation_candidate_xy'][i]) for i in (0, 1))


def prepare(collision_dir, landmarks_dir, review_dir, package, output, *, ward_snapshot=None, ward_receipt=None, ward_match=None, root_assets=None, map_revision=None):
    """Use already verified outputs, recheck their sources, build local-only UI data."""
    from .history import checked_input, checked_output
    root = Path(__file__).resolve().parents[1]
    collision_dir, landmarks_dir, review_dir, package = [checked_input(root, p)
        for p in (collision_dir, landmarks_dir, review_dir, package)]
    output = checked_output(root, output)
    if output.exists():
        raise ValueError('new_prototype_output_required')
    collision = read_complete(collision_dir, 'collision.json')
    landmarks = read_complete(landmarks_dir, 'landmarks.json')
    review = read_complete(review_dir, 'review.json')
    plan, _ = read_plan(package)
    if (collision['map_asset_sha256'] != landmarks['map_asset_sha256']
            or collision['client_build'] != landmarks['client_build']
            or review['current_client_build'] != landmarks['client_build']
            or review['map_vpk_exact_bytes_compatible'] is not True
            or str(review['historical_client_build']) != str(plan['map_build'])):
        raise ValueError('prototype_map_identity_mismatch')
    sources = dict(review['source_sha256'])
    root_layer, grid_image, grid_raw = None, None, None
    if root_assets is not None:
        import numpy as np
        from PIL import Image
        from .map_root_assets import grid
        root_dir = checked_input(root, root_assets)
        root_data = read_complete(root_dir, 'root-assets.json')
        if root_data['map_asset_sha256'] != collision['map_asset_sha256'] or root_data['client_build'] != collision['client_build']:
            raise ValueError('prototype_root_map_identity_mismatch')
        for p in (root_dir/'COMPLETE', root_dir/'root-assets.json'):
            sources[str(p)] = sha(p)
        for resource in root_data['resources']:
            p = root_dir/Path(resource['resource']).name
            if sha(p) != resource['sha256']:
                raise ValueError('prototype_root_resource_changed')
            sources[str(p)] = resource['sha256']
        grid_raw = (root_dir/'dota.gnv').read_bytes()
        parsed = grid(grid_raw)
        values = sorted(map(int, np.unique(parsed['values'])))
        palette = [(27,48,65),(58,126,140),(94,80,117),(174,124,73),(82,99,68),
            (106,156,120),(155,90,104),(145,145,90),(103,142,180),(166,110,155)]
        rgb = np.zeros((*parsed['values'].shape,3),dtype='u1')
        legend=[]
        for index,value in enumerate(values):
            color=palette[index%len(palette)]
            rgb[parsed['values']==value]=color
            legend.append({'raw_value':value,'rgb':list(color)})
        grid_image=Image.fromarray(np.flipud(rgb).copy())
        root_layer={'grid_image':'gnv.png','grid_bytes':'gnv.bin',
            'grid_info':root_data['grid'],'grid_legend':legend,
            'trm':root_data['trm'],'tree_comparison':root_data['tree_comparison'],
            'physics_dependency_declared':root_data['world_physics_dependency_declared'],
            'world_placement_verified':False}
    ward_layer = None
    if any(p is not None for p in (ward_snapshot, ward_receipt, ward_match)):
        if any(p is None for p in (ward_snapshot, ward_receipt, ward_match)):
            raise ValueError('prototype_all_three_ward_inputs_required')
        ward_snapshot, ward_receipt = [checked_input(root, p) for p in (ward_snapshot, ward_receipt)]
        for p in (ward_snapshot, ward_receipt): sources[str(p)] = sha(p)
        ward_layer = load_ward_candidates(ward_snapshot, ward_receipt, ward_match)
    for directory, name in ((collision_dir, 'collision.json'), (landmarks_dir, 'landmarks.json'), (review_dir, 'review.json')):
        for p in (directory/name, directory/'COMPLETE'):
            sources[str(p)] = sha(p)
    if sources.get(str(package/'package.json')) != sha(package/'package.json'):
        raise ValueError('prototype_survey_package_not_bound')
    archive = collision_dir/'meshes.npz'
    sources[str(archive)] = sha(archive)
    if sources[str(archive)] != collision['mesh_archive_sha256']:
        raise ValueError('prototype_mesh_archive_changed')
    if any(sha(p) != digest for p, digest in sources.items()):
        raise ValueError('prototype_upstream_sources_changed')
    texture, footprint, count = render_collision(archive)
    atlas_paths = [p for p in sources if Path(p).name == 'atlas.json']
    if len(atlas_paths) != 1:
        raise ValueError('prototype_bound_atlas_required')
    matrix = load(atlas_paths[0])['calibration']['matrix_client']
    frames = []
    image_bytes = []
    for index, f in enumerate(plan['frames']):
        p = local(package, f['image'])
        data = p.read_bytes()
        import hashlib
        if hashlib.sha256(data).hexdigest() != f['image_sha256']:
            raise ValueError('prototype_survey_image_changed')
        name = f'frames/{index:02d}.png'
        image_bytes.append((name, data))
        frames.append({k: f[k] for k in ('case', 'frame_index', 'size', 'crop_origin_client_xy', 'image_sha256')} | {'image': name})
    markers = [{'id': str(p['entity_ordinal']), 'name': p['targetname'] or p['classname'],
        'classname': p['classname'], 'world_xyz': p['origin_xyz']} for p in landmarks['landmarks']]
    data = {'schema_version': 1, 'kind': 'offline_virtual_map_prototype',
        'client_build': landmarks['client_build'], 'map_asset_sha256': landmarks['map_asset_sha256'],
        'entity_frame': 'source2_map_entity_origin_xyz', 'collision_frame': 'physics_model_local_xyz',
        'collision_world_placement_verified': False, 'p2_pass': False,
        'bounds': landmarks['declared_minimap_window'], 'markers': markers,
        'collision_texture': {'image': 'collision.png', 'footprint': footprint, 'rendered_faces': count,
            'meaning': 'near_horizontal_mean_z_painter_not_ground'},
        'frames': frames, 'comparisons': review['rows'], 'summaries': review['summaries'],
        'historical_matrix_client': matrix,
        'ward_candidates': ward_layer,
        'root_assets': root_layer,
        'virtual_map_model': VirtualMap(MapIdentity('dota', landmarks['client_build'], landmarks['map_asset_sha256']),
            WorldBounds(**landmarks['declared_minimap_window']), 'source2_map_centered', 'asset_extracted',
            'Offline prototype / declared entity window / '+sha(landmarks_dir/'landmarks.json')).to_dict(),
        'source_sha256': sources, 'source_data_mutated': False}
    if ward_layer is not None:
        from .ward_map import bind
        target = VirtualMap.from_dict(data['virtual_map_model'])
        revision = None
        if map_revision is not None:
            from .map_updates import read_revision
            revision_path = checked_input(root, map_revision)
            revision = read_revision(revision_path)
            for p in (revision_path/'snapshot.json', revision_path/'COMPLETE'):
                sources[str(p)] = sha(p)
        ward_layer['map_binding'] = bind(ward_layer, target, revision)
        data['ward_preview_default'] = ward_layer['map_binding']['preview_allowed']
    read_plan(package)
    if any(sha(p) != digest for p, digest in sources.items()):
        raise ValueError('prototype_sources_changed_during_build')
    output.mkdir(parents=True)
    (output/'frames').mkdir()
    texture.save(output/'collision.png')
    if root_layer:
        grid_image.save(output/root_layer['grid_image'])
        (output/root_layer['grid_bytes']).write_bytes(grid_raw)
    for name, raw in image_bytes:
        (output/name).write_bytes(raw)
    save(output/'scene.json', data)
    files = ['scene.json', 'collision.png', *[f['image'] for f in frames]]
    if root_layer: files += [root_layer['grid_image'],root_layer['grid_bytes']]
    save(output/'manifest.json', {'ward_match_id': ward_layer['match_id'] if ward_layer else None,
        'files': {name: sha(output/name) for name in files}})
    (output/'COMPLETE').write_text(sha(output/'manifest.json')+'\n', encoding='ascii')
    return data


def open_scene(package):
    """Verify every local display file before using it; no external URLs or paths."""
    package = Path(package)
    manifest = read_complete(package, 'manifest.json')
    # The small public receipt is the identity gate, before scene/image hashes
    # or decoding any ward body. Do not infer identity from directory dates.
    if 'ward_match_id' not in manifest:
        raise ValueError('prototype_manifest_identity_required')
    declared_match = manifest['ward_match_id']
    if declared_match is not None:
        from .replay_holdout import refuse_locked_match
        refuse_locked_match(str(declared_match))
    files = manifest.get('files')
    if not isinstance(files, dict) or not 3 <= len(files) <= 103 or 'scene.json' not in files:
        raise ValueError('prototype_manifest_invalid')
    for name, digest in files.items():
        if sha(local(package, name)) != digest:
            raise ValueError('prototype_display_file_changed')
    scene = load(package/'scene.json')
    if (scene.get('kind') != 'offline_virtual_map_prototype' or scene.get('schema_version') != 1
            or scene.get('p2_pass') is not False or scene.get('collision_world_placement_verified') is not False):
        raise ValueError('prototype_scene_invalid')
    WorldBounds(**scene['bounds'])
    model = VirtualMap.from_dict(scene['virtual_map_model'])
    if (model.identity.client_build != scene['client_build'] or model.identity.map_asset_sha256 != scene['map_asset_sha256']
            or model.bounds != WorldBounds(**scene['bounds'])):
        raise ValueError('prototype_virtual_map_binding')
    if (not 1 <= len(scene['markers']) <= 256
            or len({p['id'] for p in scene['markers']}) != len(scene['markers'])
            or not 1 <= len(scene['frames']) <= 100):
        raise ValueError('prototype_scene_budget_or_duplicate')
    for point in scene['markers']:
        if len(point['world_xyz']) != 3:
            raise ValueError('prototype_marker_invalid')
        for value in point['world_xyz']: number(value)
    layer = scene.get('ward_candidates')
    if declared_match != (layer['match_id'] if layer is not None else None):
        raise ValueError('prototype_manifest_identity_mismatch')
    if layer is not None:
        from .replay_holdout import refuse_locked_match
        refuse_locked_match(str(layer['match_id']))
        if (layer.get('map_version_alignment_verified') is not False or layer.get('world_projection_verified') is not False
                or layer.get('raw_coordinate_frame') != 'gem_cell128_plus_vec_raw'
                or not 0 <= len(layer['points']) <= 5000 or len({p['id'] for p in layer['points']}) != len(layer['points'])):
            raise ValueError('prototype_ward_layer_invalid')
        for p in layer['points']:
            if p['ward_type'] not in ('observer', 'sentry') or len(p['raw_world_xy']) != 2:
                raise ValueError('prototype_ward_point_invalid')
            for value in p['raw_world_xy']: number(value)
        if layer.get('map_binding') is not None:
            from .ward_map import status
            status(layer, model)
    required = {'scene.json', scene['collision_texture']['image'], *[f['image'] for f in scene['frames']]}
    roots = scene.get('root_assets')
    if roots is not None:
        if roots.get('world_placement_verified') is not False:
            raise ValueError('prototype_root_certification_unsupported')
        required.update((roots['grid_image'],roots['grid_bytes']))
    if required != set(files):
        raise ValueError('prototype_manifest_roster_mismatch')
    return scene


def project_marker(scene, marker, frame, mode):
    """A diagnostic display adapter, never changes a marker's world coordinates."""
    x, y, _ = marker['world_xyz']
    if mode == 'asset_window':
        model = VirtualMap.from_dict(scene['virtual_map_model'])
        p = model.world_to_norm(WorldPoint(x, y, model.identity, model.coordinate_frame), allow_unverified=True, allow_outside=True)
        return [p.u*frame['size'][0], p.v*frame['size'][1]]
    if mode != 'historical_tower_model':
        raise ValueError('prototype_unknown_projection')
    matrix = scene['historical_matrix_client']
    return [x*matrix['scale_x']+matrix['offset_x']-frame['crop_origin_client_xy'][0],
            y*matrix['scale_y']+matrix['offset_y']-frame['crop_origin_client_xy'][1]]
