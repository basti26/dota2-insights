"""Versioned context strata without changing the historical ward-only split."""
from __future__ import annotations

import json

from .ward_patterns import key

VERSION = 'context_cohort_v3'
FIELDS = ('source_group', 'ward_producer', 'patch', 'map', 'mode',
          'context_kind', 'resolved_game_build', 'context_producer_sha256',
          'resolved_game_mode')


def context_key(match, source):
    provenance = json.loads(source['provenance_json'])
    metadata = source.get('resolved_replay_metadata',provenance.get('replay_metadata', {}))
    build = metadata.get('game_build')
    mode = metadata.get('game_mode')
    producer = provenance.get('producer_sha256')
    if build is not None and (type(build) is not int or build <= 0):
        raise ValueError('context_cohort_invalid_game_build')
    if mode is not None and (type(mode) is not int or mode <= 0):
        raise ValueError('context_cohort_invalid_game_mode')
    if producer is not None and (not isinstance(producer, str) or len(producer) != 64
            or any(c not in '0123456789abcdef' for c in producer)):
        raise ValueError('context_cohort_invalid_producer')
    # Unknown build/producer stays a separate stratum; it is never a wildcard.
    return (*key(match), source['source_kind'],
            str(build) if build is not None else 'not_recorded',
            producer if producer is not None else 'not_recorded',
            str(mode) if mode is not None else 'not_recorded')
