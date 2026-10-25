"""Admission gate for ward validation sources; never proves visual correctness."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from dota2_insights.ward_validation_inventory import digest

SHA = re.compile(r'[a-f0-9]{64}')


def freeze_sources(request, inventory_path, protocol_path, probe):
    """Build an admission receipt; read no ward records, including heldout ones.

    `probe` is the bounded prefix/footer inspector, supplied by the CLI.
    History coverage still requires an explicit review; no negative search proves
    that every external analysis archive has been searched.
    """
    inventory_bytes = Path(inventory_path).read_bytes()
    historical = json.loads(inventory_bytes)
    protocol_bytes = Path(protocol_path).read_bytes()
    reasons = []
    matches = request.get('matches', [])
    inventory_sha = hashlib.sha256(inventory_bytes).hexdigest()
    protocol_sha = hashlib.sha256(protocol_bytes).hexdigest()
    if request.get('inventory_sha256') != inventory_sha:
        reasons.append('inventory_fingerprint_mismatch')
    if request.get('protocol_sha256') != protocol_sha:
        reasons.append('protocol_fingerprint_mismatch')
    if historical.get('history_scan_complete') is not True or historical.get('scan_issues'):
        reasons.append('history_scan_has_unresolved_gaps')
    review = request.get('history_review', {})
    if not all(isinstance(review.get(k), str) and review[k].strip()
               for k in ('reviewer', 'reviewed_at_utc', 'scope_notes')):
        reasons.append('history_scope_review_required')
    else:
        try:
            stamp = datetime.fromisoformat(review['reviewed_at_utc'].replace('Z', '+00:00'))
            if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
                raise ValueError('UTC required')
        except ValueError:
            reasons.append('history_review_time_must_be_utc')
    if not isinstance(matches, list) or len(matches) not in (2, 3) or any(not isinstance(r, dict) for r in matches):
        reasons.append('require_two_or_three_match_objects')
        matches = []
    roles = [r.get('role') for r in matches]
    if roles.count('heldout') != 1 or roles.count('development') != len(matches) - 1:
        reasons.append('require_one_heldout_and_remaining_development')
    ids = [str(r.get('match_id', '')) for r in matches]
    if len(set(ids)) != len(ids):
        reasons.append('duplicate_match_id')
    for row, mid in zip(matches, ids):
        if not re.fullmatch(r'\d{10}', mid):
            reasons.append('invalid_match_id:' + mid)
        if historical.get('exposure', {}).get(mid):
            reasons.append('historically_exposed:' + mid)
        if not SHA.fullmatch(str(row.get('replay_sha256', ''))):
            reasons.append('invalid_replay_fingerprint:' + mid)
        if not all(isinstance(row.get(k), str) and row[k].strip()
                   for k in ('source', 'source_origin', 'acquired_at_utc', 'map_image', 'map_mapping')):
            reasons.append('missing_source_fields:' + mid)
        else:
            try:
                acquired = datetime.fromisoformat(row['acquired_at_utc'].replace('Z', '+00:00'))
                if acquired.utcoffset() != timezone.utc.utcoffset(acquired):
                    raise ValueError('UTC required')
            except ValueError:
                reasons.append('acquisition_time_must_be_utc:' + mid)
    sources = []
    # Reject at the history gate before opening any candidate replay or maps.
    if not reasons:
        for row, mid in zip(matches, ids):
            try:
                source = Path(row['source'])
                if not 16 <= source.stat().st_size <= 512 * 1024 * 1024:
                    reasons.append('source_size_outside_budget:' + mid)
                    continue
                actual = digest(source)
                if actual != row['replay_sha256']:
                    reasons.append('source_fingerprint_mismatch:' + mid)
                    continue
                metadata = probe(source, mid, actual)
                if (metadata.get('status') not in ('verified_bounded_metadata', 'verified_partial_metadata')
                        or metadata.get('match_id') != mid
                        or not isinstance(metadata.get('footer'), dict)
                        or metadata['footer'].get('match_id') != mid
                        or metadata.get('source_sha256') != actual
                        or metadata.get('source_sha256_after_read') != actual
                        or metadata.get('source_stat_unchanged') is not True):
                    reasons.append('metadata_identity_not_verified:' + mid)
                    continue
                image, mapping = Path(row['map_image']), Path(row['map_mapping'])
                sources.append({'match_id': mid, 'role': row['role'],
                                'source': str(source.resolve()), 'source_origin': row['source_origin'],
                                'acquired_at_utc': row['acquired_at_utc'], 'replay_sha256': actual,
                                'source_bytes': source.stat().st_size,
                                'game_build': metadata.get('game_build'), 'game_mode': metadata.get('game_mode'),
                                'metadata': metadata, 'map_image_sha256': digest(image),
                                'map_image': str(image.resolve()), 'map_mapping': str(mapping.resolve()),
                                'map_mapping_sha256': digest(mapping),
                                'map_version_label': row.get('map_version_label'),
                                'map_calibration_verified': False})
            except (OSError, ValueError, TypeError):
                reasons.append('source_or_metadata_unreadable:' + mid)
        if len({row['replay_sha256'] for row in sources}) != len(sources):
            reasons.append('duplicate_replay_bytes')
    for row in sources:
        row['cohort_key'] = (f"build:{row['game_build']}|mode:{row['game_mode']}|"
                             f"image:{row['map_image_sha256']}|mapping:{row['map_mapping_sha256']}")
    return {'schema_version': 1,
            'status': 'rejected' if reasons else 'sources_frozen_pending_samples',
            'independent_validation_passed': False,
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'inventory_sha256': inventory_sha, 'protocol_sha256': protocol_sha,
            'history_review': review, 'rejection_reasons': reasons,
            'matches': sources if not reasons else [],
            'cohort_keys': sorted({row['cohort_key'] for row in sources}) if not reasons else [],
            'random_per_match': 16 if len(matches) == 2 else 12,
            'special_per_match': 8 if len(matches) == 2 else 6,
            'heldout_release': 'after_development_fix_version_frozen',
            'scope': 'source_admission_only_not_ward_extraction_integrity_or_visual_verification'}
