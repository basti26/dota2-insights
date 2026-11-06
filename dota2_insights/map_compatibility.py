"""Explicit, directional same-map declarations for development comparisons only.

This never changes VirtualMap.require_compatible or certifies runtime UI.
File consumers must also reverify the declaration's source fingerprints.
"""
from dataclasses import asdict

from .virtual_map import MapIdentity, digest


SCOPE = 'development_landmark_comparison'


def create(source, target, evidence):
    source, target = MapIdentity(**source), MapIdentity(**target)
    if (source == target or source.map_name != target.map_name
            or source.map_asset_sha256 != target.map_asset_sha256):
        raise ValueError('compatibility_requires_distinct_context_same_map_bytes')
    if not isinstance(evidence, dict) or not 2 <= len(evidence) <= 64:
        raise ValueError('compatibility_evidence_required')
    for name, value in evidence.items():
        if (not isinstance(name, str) or not 1 <= len(name) <= 2048
                or not isinstance(value, str) or len(value) != 64
                or any(c not in '0123456789abcdef' for c in value)):
            raise ValueError('compatibility_evidence_invalid')
    body = {'schema_version': 1, 'kind': 'explicit_map_development_compatibility',
            'source_identity': asdict(source), 'target_identity': asdict(target),
            'scope': SCOPE, 'basis': 'same_full_map_archive_sha256',
            'source_sha256': dict(evidence), 'ui_equivalence_verified': False,
            'calibration_verified': False, 'production_ready': False}
    return {**body, 'declaration_id': digest(body)}


def validate(value, source, target):
    if not isinstance(value, dict):
        raise ValueError('explicit_map_compatibility_required')
    expected = create(source, target, value.get('source_sha256'))
    body = {k: v for k, v in value.items() if k != 'declaration_id'}
    if (type(value.get('schema_version')) is not int
            or any(value.get(k) is not False for k in ('ui_equivalence_verified', 'calibration_verified', 'production_ready'))
            or value.get('declaration_id') != digest(body) or value != expected):
        raise ValueError('compatibility_context_scope_or_receipt_changed')
    return value['declaration_id']
