"""Audit fixed map windows using an existing frozen map-survey observation batch."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_holdout import load, read_plan, save, sha
from dota2_insights.map_window_review import review


def run(package, batch, atlas_path, source_before, landmark_dir, output):
    package, batch, atlas_path, source_before, landmark_dir = [checked_input(ROOT, p)
        for p in (package, batch, atlas_path, source_before, landmark_dir)]
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_map_window_output_required')
    tracked = [package/'package.json', package/'plan.json', batch/'freeze.json', batch/'observations.json',
        atlas_path, source_before, landmark_dir/'landmarks.json', landmark_dir/'COMPLETE',
        landmark_dir/'default_ents.vents_c', landmark_dir/'DATA.txt',
        Path(__file__), ROOT/'dota2_insights/map_window_review.py']
    fingerprints = {str(p): sha(p) for p in tracked}
    plan, _ = read_plan(package)
    if plan['source_hashes'].get(str(atlas_path)) != sha(atlas_path):
        raise ValueError('map_window_atlas_not_bound_to_plan')
    freeze = load(batch/'freeze.json')
    if (freeze.get('predictions_read') is not False or freeze['observations_sha256'] != sha(batch/'observations.json')
            or freeze['package_sha256'] != sha(package/'package.json')):
        raise ValueError('map_window_frozen_sources_changed')
    observations = load(batch/'observations.json')
    if observations['package_sha256'] != freeze['package_sha256']:
        raise ValueError('map_window_observation_package_changed')
    atlas, before = load(atlas_path), load(source_before)
    sources = {s['file']: s['sha256'] for s in atlas['sources']}
    if sources.get(str(source_before)) != sha(source_before):
        raise ValueError('map_window_source_receipt_not_bound_to_atlas')
    landmark = load(landmark_dir/'landmarks.json')
    if ((landmark_dir/'COMPLETE').read_text(encoding='ascii').strip() != sha(landmark_dir/'landmarks.json')
            or landmark['resource_sha256'] != sha(landmark_dir/'default_ents.vents_c')
            or landmark['source_export_sha256'] != sha(landmark_dir/'DATA.txt')):
        raise ValueError('map_window_landmark_sources_changed')
    historical_sha = before['map_vpk']['sha256']
    # Require the asset receipt and atlas's map-source claim to agree.
    if historical_sha not in [s['sha256'] for s in atlas['sources'] if Path(s['file']).name == 'dota.vpk']:
        raise ValueError('map_window_historical_asset_receipts_disagree')
    result = review(plan, observations, atlas, landmark, historical_sha)
    read_plan(package)
    if any(sha(p) != fingerprint for p, fingerprint in fingerprints.items()):
        raise ValueError('map_window_sources_changed_during_review')
    result['source_sha256'] = fingerprints
    result['sources_reverified'] = True
    output.mkdir(parents=True)
    save(output/'review.json', result)
    (output/'COMPLETE').write_text(sha(output/'review.json')+'\n', encoding='ascii')
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'batch', 'atlas', 'source-before', 'landmarks', 'output'):
        cli.add_argument('--'+name, type=Path, required=True)
    args = cli.parse_args()
    result = run(args.package, args.batch, args.atlas, args.source_before, args.landmarks, args.output)
    print({'output': str(args.output), 'rows': len(result['rows']),
        'matched_origins': result['authored_landmark_origins_exactly_matched'], 'formal_p2_pass': False})


if __name__ == '__main__':
    main()
