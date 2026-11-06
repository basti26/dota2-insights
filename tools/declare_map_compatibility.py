"""Create a bounded, explicit development-only declaration from frozen receipts."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_holdout import load, save, sha
from dota2_insights.map_compatibility import create
from dota2_insights.map_prototype import open_scene
from dota2_insights.map_updates import read_revision
from tools.evaluate_map import admit_prototype_metadata


def run(prototype, revision, output):
    prototype, revision = [checked_input(ROOT, p) for p in (prototype, revision)]
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_compatibility_output_required')
    admit_prototype_metadata(prototype)
    tracked = [prototype/name for name in ('COMPLETE', 'manifest.json', 'scene.json')]
    tracked += [revision/name for name in ('COMPLETE', 'snapshot.json', 'source/maps/dota.vpk', 'source/steam.inf')]
    tracked += [Path(__file__), ROOT/'dota2_insights/map_compatibility.py', ROOT/'dota2_insights/map_updates.py',
                ROOT/'dota2_insights/map_prototype.py', ROOT/'tools/evaluate_map.py']
    before = {str(p): sha(p) for p in tracked}
    scene, snapshot = open_scene(prototype), read_revision(revision)
    if snapshot['identity'] != scene['virtual_map_model']['identity']:
        raise ValueError('compatibility_target_revision_mismatch')
    sources = scene['source_sha256']
    def bound(name):
        paths = [checked_input(ROOT, p) for p in sources if Path(p).name == name]
        if len(paths) != 1 or sha(paths[0]) != sources[str(paths[0])]:
            raise ValueError('compatibility_bound_source_missing_or_changed')
        before[str(paths[0])] = sources[str(paths[0])]
        return paths[0]
    plan_path, atlas_path, receipt_path = [bound(name) for name in ('plan.json', 'atlas.json', 'source-before.json')]
    plan, atlas, receipt = [load(p) for p in (plan_path, atlas_path, receipt_path)]
    if (str(plan['map_build']) != str(atlas['map_build'])
            or plan['source_hashes'].get(str(atlas_path)) != sha(atlas_path)):
        raise ValueError('compatibility_historical_plan_binding')
    atlas_sources = {row['file']: row['sha256'] for row in atlas['sources']}
    target = snapshot['identity']
    if (atlas_sources.get(str(receipt_path)) != sha(receipt_path)
            or receipt['map_vpk']['sha256'] != target['map_asset_sha256']
            or receipt['map_vpk']['bytes'] != snapshot['map_vpk_bytes']
            or target['map_asset_sha256'] not in [h for p, h in atlas_sources.items() if Path(p).name == 'dota.vpk']):
        raise ValueError('compatibility_map_archive_receipts_disagree')
    source = {**target, 'client_build': int(plan['map_build'])}
    result = create(source, target, before)
    open_scene(prototype); read_revision(revision)
    if any(sha(p) != h for p, h in before.items()):
        raise ValueError('compatibility_evidence_changed')
    output.mkdir(parents=True)
    save(output/'declaration.json', result)
    (output/'COMPLETE').write_text(sha(output/'declaration.json')+'\n', encoding='ascii')
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('prototype', 'revision', 'output'):
        cli.add_argument('--'+name, required=True, type=Path)
    args = cli.parse_args()
    result = run(args.prototype, args.revision, args.output)
    print({'scope': result['scope'], 'declaration_id': result['declaration_id'], 'production_ready': False})


if __name__ == '__main__':
    main()
