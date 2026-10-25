"""Read-only release readiness. Does not open heldout replay, wards or answers."""
from datetime import datetime, timezone
from pathlib import Path

from dota2_insights.ward_review_batch import compare_batch, read_json
from dota2_insights.ward_validation_package import admitted_development, sha


REVISION_FILES = (
    'tools/extract_replay_wards.py', '.replay-deps/gem/parser.py',
    '.replay-deps/gem/extractors/wards.py',
    'dota2_insights/ward_observation_check.py', 'dota2_insights/ward_review_batch.py',
    'dota2_insights/ward_release_readiness.py', 'dota2_insights/ward_validation_package.py',
    'dota2_insights/ward_validation_sampling.py', 'tools/ward_review.py',
    'tools/check_ward_release.py',
)


def revision_snapshot(root, freeze_path, label):
    frozen, freeze_body = read_json(freeze_path)
    dev = next(r['match_id'] for r in frozen['matches'] if r['role'] == 'development')
    admitted_development(freeze_path, dev)
    if not isinstance(label, str) or not label.strip():
        raise ValueError('revision_label_required')
    return {'schema_version': 1, 'kind': 'ward_validation_revision', 'label': label,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_freeze_sha256': sha(freeze_body),
        'files': {name: sha((Path(root) / name).read_bytes()) for name in REVISION_FILES},
        'heldout_released': False, 'independent_validation_passed': False}


def release_readiness(root, freeze_path, revision_path, reviews):
    root = Path(root)
    frozen, freeze_body = read_json(freeze_path)
    developers = {r['match_id']: r for r in frozen['matches'] if r['role'] == 'development'}
    admitted_development(freeze_path, next(iter(developers)))
    revision, revision_body = read_json(revision_path)
    reasons, receipts, seen = [], [], set()
    if (revision.get('kind') != 'ward_validation_revision' or revision.get('schema_version') != 1
            or revision.get('source_freeze_sha256') != sha(freeze_body)
            or set(revision.get('files', {})) != set(REVISION_FILES)):
        reasons.append('bound_complete_revision_manifest_required')
    else:
        for name, fingerprint in revision['files'].items():
            if sha((root / name).read_bytes()) != fingerprint:
                reasons.append('revision_changed:' + name)
    coverage = {mid: set() for mid in developers}
    package_ids = {}
    for entry in reviews:
        # Recompute from hash-bound observations instead of trusting report counts.
        result = compare_batch(entry['package'], entry['batch'])
        mid = result['match_id']
        if mid not in developers or result['role'] != 'development':
            reasons.append('only_admitted_development_reviews_allowed')
            continue
        if (result['source_freeze_sha256'] != sha(freeze_body)
                or result['cohort_key'] != developers[mid]['cohort_key']):
            reasons.append('review_source_or_cohort_mismatch:' + mid)
        if mid in package_ids and package_ids[mid] != result['package_sha256']:
            reasons.append('mixed_sample_packages:' + mid)
        package_ids[mid] = result['package_sha256']
        if result.get('observer_prior_exposure') == 'not_declared':
            reasons.append('observer_exposure_not_declared:' + mid)
        for row in result['samples']:
            identity = (mid, row['sample_id'])
            if identity in seen:
                reasons.append('overlapping_review_batches:' + row['sample_id'])
            seen.add(identity)
            coverage[mid].add(row['sample_id'])
            for field, verdict in row['fields'].items():
                if verdict in ('pending', 'mismatch'):
                    reasons.append(verdict + ':' + row['sample_id'] + ':' + field)
                # Unjudgeable coordinates do not become a map certificate.
                if field == 'map_position' and verdict != 'pass':
                    reasons.append('independent_map_point_not_verified:' + row['sample_id'])
        receipts.append({key: result[key] for key in ('match_id', 'package_sha256', 'batch_sha256',
            'observations_sha256', 'comparison_code_sha256', 'checker_code_sha256', 'counts')})
    expected = frozen['random_per_match'] + frozen['special_per_match']
    for mid, ids in coverage.items():
        if len(ids) != expected:
            reasons.append('development_sample_coverage_incomplete:' + mid)
    if Path(revision_path).read_bytes() != revision_body or Path(freeze_path).read_bytes() != freeze_body:
        raise ValueError('release_inputs_changed')
    return {'schema_version': 1, 'kind': 'ward_heldout_release_readiness',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'blocked' if reasons else 'ready_for_explicit_release_review',
        'source_freeze_sha256': sha(freeze_body), 'revision_sha256': sha(revision_body),
        'development_coverage': {mid: {'reviewed': len(ids), 'required': expected} for mid, ids in coverage.items()},
        'reasons': sorted(set(reasons)), 'review_receipts': receipts,
        'heldout_matches_locked': [r['match_id'] for r in frozen['matches'] if r['role'] == 'heldout'],
        'heldout_released': False, 'independent_validation_passed': False,
        'scope': 'readiness only; does not grant an extraction permission or certify screenshot semantics'}
