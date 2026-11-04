"""P0/P1: audit installed map metadata and emit explicitly unverified geometry."""
import argparse
import ast
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.history import checked_output
from dota2_insights.map_assets import inventory, compiled_blocks
from dota2_insights.valve_resources import directory_bytes, read_vpk_directory, _read_resource
from dota2_insights.virtual_map import MapIdentity, WorldBounds, VirtualMap


def gem_constants(path):
    raw = path.read_bytes()
    constants = {}
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple):
            names = [n.id for n in node.targets[0].elts if isinstance(n, ast.Name)]
            if names in (['MAP_XMIN', 'MAP_XMAX'], ['MAP_YMIN', 'MAP_YMAX'], ['MAP_IMAGE_WIDTH', 'MAP_IMAGE_HEIGHT']):
                constants.update(zip(names, ast.literal_eval(node.value)))
    if len(constants) != 6:
        raise ValueError('pinned_gem_map_constants_layout_changed')
    return constants, hashlib.sha256(raw).hexdigest()


def run(game, output, previous=None):
    from dota2_insights.replay_workbench import game_running
    if game_running():
        raise ValueError('close_game_before_offline_asset_audit')
    game = Path(game).resolve(strict=True)
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_audit_output_required')
    steam = (game / 'steam.inf').read_bytes()
    version = dict(line.split('=', 1) for line in steam.decode('utf-8-sig').splitlines() if '=' in line)
    build = int(version['ClientVersion'])
    source = game / 'maps/dota.vpk'
    raw = directory_bytes(source)
    sha = hashlib.sha256(raw).hexdigest()
    entries, embedded = read_vpk_directory(raw)
    assets = inventory(entries)
    assets.update(map_vpk_sha256=sha, map_vpk_bytes=len(raw), client_build=build,
        patch=None, patch_reason='steam.inf_reports_client_build_not_gameplay_patch',
        steam_inf_sha256=hashlib.sha256(steam).hexdigest())
    if previous:
        previous_raw = Path(previous).read_bytes()
        if len(previous_raw) > 2 * 1024**2:
            raise ValueError('previous_inventory_budget')
        prior = json.loads(previous_raw)
        assets['previous_inventory'] = {'sha256': hashlib.sha256(previous_raw).hexdigest(),
            'map_vpk_sha256': prior['source_sha256'], 'same_map_vpk_bytes': prior['source_sha256'] == sha}
    selected = []
    for family in ('worlds', 'world_nodes', 'physics', 'models'):
        selected += assets['families'][family][:1]
    selected += assets['physics_model_resources'][:1]
    selected += [name for name in assets['families']['models'] if '/worldnodes/' in name and '_s_mesh' in name][:1]
    samples = []
    for resource in selected:
        body, _ = _read_resource(source, entries[resource], embedded, 16 * 1024**2)
        samples.append({'resource': resource, 'crc_verified': True, **compiled_blocks(body)})
    assets['sample_resource_blocks'] = samples
    constants, constants_sha = gem_constants(ROOT / '.replay-deps/gem/catalog/map.py')
    identity = MapIdentity('dota', build, sha)
    # Explicit hypothesis only. Neither raw replay coordinates nor GSI are
    # silently identified with the map entity origin frame.
    offset = 16384
    legacy = VirtualMap(identity, WorldBounds(constants['MAP_XMIN'] - offset, constants['MAP_YMIN'] - offset,
        constants['MAP_XMAX'] - offset, constants['MAP_YMAX'] - offset), 'source2_map_centered', 'reference',
        'Pinned Gem 7.41 image window; centered via unverified -16384 origin hypothesis; source sha256=' + constants_sha)
    reference = VirtualMap(identity, WorldBounds(-10464, -10464, 10400, 10400),
        'source2_map_centered', 'reference',
        'User-supplied merged design cites leamare bounds; upstream and current-build applicability not independently checked')
    candidates = {'legacy_gem_window': legacy.to_dict(), 'plan_reference_window': reference.to_dict()}
    comparison = []
    for name, model in [('legacy_gem_window', legacy), ('plan_reference_window', reference)]:
        b = model.bounds
        comparison.append({'name': name, 'world_bounds': asdict(b), 'width': b.max_x-b.min_x,
            'height': b.max_y-b.min_y, 'center': [(b.min_x+b.max_x)/2, (b.min_y+b.max_y)/2],
            'model_id': model.model_id, 'independent_validation_passed': False})
    report = {'schema_version': 1, 'kind': 'virtual_map_p0_p1_audit', 'assets': assets,
        'coordinate_comparison': comparison, 'gem_constants': constants,
        'gem_constants_source_sha256': constants_sha,
        'baseline_landmark_error': {'median_reference_px': None, 'p95_reference_px': None,
            'reason': 'no_independent_non_tower_world_to_map_landmark_pairs_admitted_in_this_audit'},
        'origin_translation_candidate': [-offset, -offset], 'gsi_to_map_identity_verified': False,
        'p2_passed': False, 'production_projection_changed': False,
        'coordinate_note': 'different_image_crops_or_world_windows_can_have_different_bounds_without_a_constant_bug'}
    if directory_bytes(source) != raw or (game / 'steam.inf').read_bytes() != steam:
        raise ValueError('installed_map_changed_during_audit')
    output.mkdir(parents=True)
    (output / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'candidate-models.json').write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, required=True, help='installed game/dota directory')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous-inventory', type=Path)
    args = parser.parse_args()
    report = run(args.game, args.output, args.previous_inventory)
    print(json.dumps({'build': report['assets']['client_build'], 'resources': report['assets']['resource_count'],
        'extensions': report['assets']['extensions'], 'previous': report['assets'].get('previous_inventory'),
        'p2_passed': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
