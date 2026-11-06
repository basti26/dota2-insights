"""Compare explicitly named replay/asset objects from a bounded local input list."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_holdout import load, save, sha
from dota2_insights.map_landmarks import extract
from dota2_insights.map_prototype import read_complete
from dota2_insights.map_updates import read_revision
from dota2_insights.map_translation_pairs import compare
from dota2_insights.replay_archive import _footer_match_id
from dota2_insights.replay_holdout import refuse_locked_match


def run(manifest, landmarks, revision, output):
    manifest, landmarks, revision = [checked_input(ROOT, p) for p in (manifest, landmarks, revision)]
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_translation_output_required')
    manifest_sha = sha(manifest)
    jobs = load(manifest, 128*1024)
    if (type(jobs.get('schema_version')) is not int or jobs['schema_version'] != 1
            or jobs.get('role') != 'development' or not isinstance(jobs.get('matches'), list)
            or not 1 <= len(jobs['matches']) <= 8):
        raise ValueError('translation_explicit_bounded_matches_required')
    mids = [job['match_id'] for job in jobs['matches']]
    if len(set(mids)) != len(mids):
        raise ValueError('translation_duplicate_match')
    # Check every identity before reading any extraction or replay payload.
    for mid in mids:
        if not isinstance(mid, str) or not mid.isascii() or not mid.isdigit() or len(mid) > 20:
            raise ValueError('translation_match_identity_required')
        refuse_locked_match(mid)
    paths = [manifest, landmarks/'landmarks.json', landmarks/'COMPLETE', landmarks/'DATA.txt',
             landmarks/'default_ents.vents_c', revision/'COMPLETE', revision/'snapshot.json',
             revision/'source/maps/dota.vpk', revision/'source/steam.inf', Path(__file__),
             ROOT/'dota2_insights/map_translation_pairs.py', ROOT/'dota2_insights/map_evaluation.py',
             ROOT/'dota2_insights/map_landmarks.py', ROOT/'dota2_insights/replay_archive.py']
    before = {str(p): sha(p) for p in paths}
    if before[str(manifest)] != manifest_sha:
        raise ValueError('translation_manifest_changed')
    asset = read_complete(landmarks, 'landmarks.json')
    snapshot = read_revision(revision)
    identity = snapshot['identity']
    if (asset['map_asset_sha256'] != identity['map_asset_sha256'] or asset['client_build'] != identity['client_build']
            or asset['source_export_sha256'] != sha(landmarks/'DATA.txt')
            or asset['resource_sha256'] != sha(landmarks/'default_ents.vents_c')):
        raise ValueError('translation_asset_source_changed')
    resources = {r['resource']: r for r in snapshot['resources']}
    if resources[asset['source_resource']]['sha256'] != asset['resource_sha256']:
        raise ValueError('translation_asset_not_in_target_revision')
    assets = extract((landmarks/'DATA.txt').read_text(encoding='utf-8-sig'), include_towers=True)['landmarks']
    results, spatial = [], set()
    for job in jobs['matches']:
        path = checked_input(ROOT, job['extraction'])
        if sha(path) != job['extraction_sha256']:
            raise ValueError('translation_extraction_changed')
        before[str(path)] = job['extraction_sha256']
        record = load(path, 1024*1024)
        if record['match_id'] != job['match_id']:
            refuse_locked_match(str(record['match_id']))
            raise ValueError('translation_extraction_identity_disagree')
        replay = Path(job['replay'])
        if (not replay.is_absolute() or replay.suffix.lower() != '.dem' or replay.is_symlink()
                or replay.name != record['source_file'] or not 16 <= replay.stat().st_size <= 512*1024**2):
            raise ValueError('translation_original_replay_invalid')
        footer_id = _footer_match_id(replay)
        if footer_id is not None:
            refuse_locked_match(footer_id)
        if footer_id != job['match_id']:
            raise ValueError('translation_original_footer_disagree')
        if replay.stat().st_size != record['source_bytes'] or sha(replay) != record['source_sha256']:
            raise ValueError('translation_original_replay_changed')
        before[str(replay)] = record['source_sha256']
        result = compare(record, assets, identity)
        spatial.update((row['object_id'], *row['world_xy']) for row in result['dataset']['rows'])
        result['source_extraction_sha256'] = sha(path)
        results.append(result)
    read_complete(landmarks, 'landmarks.json'); read_revision(revision)
    for mid in mids:
        refuse_locked_match(mid)
    if any(sha(path) != fingerprint for path, fingerprint in before.items()):
        raise ValueError('translation_sources_changed_during_review')
    report = {'schema_version': 1, 'kind': 'named_static_object_translation_comparison',
              'target_identity': identity, 'matches': results, 'source_sha256': before,
              'pair_count': sum(len(r['dataset']['rows']) for r in results),
              'unique_target_objects': len(spatial), 'parameters_fitted': False,
              'original_replay_identity_and_sha_reverified': True,
              'replay_map_version_verified': False, 'gsi_transform_verified': False,
              'formal_p2_pass': False, 'formal_p3_pass': False, 'production_ready': False}
    output.mkdir(parents=True)
    save(output/'report.json', report)
    (output/'COMPLETE').write_text(sha(output/'report.json')+'\n', encoding='ascii')
    return report


def main():
    sys.path.insert(0, str(ROOT/'.replay-deps'))
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'landmarks', 'revision', 'output'):
        cli.add_argument('--'+name, type=Path, required=True)
    args = cli.parse_args()
    r = run(args.manifest, args.landmarks, args.revision, args.output)
    print({'pair_count': r['pair_count'], 'unique_target_objects': r['unique_target_objects'], 'production_ready': False})


if __name__ == '__main__':
    main()
