"""Discover equipment handoffs and persist references, without copying assets."""
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re

from .match_journal_index import history_bases, index_destination, write_index, read_index as _read_index
from .observation_handoff import verify


def resolve_reference(reference, roots):
    key, value = reference.get('root_key'), reference.get('source')
    if (key not in ('helper_history', 'helper_releases') or key not in roots
            or not isinstance(value, str) or len(value) > 1000):
        raise ValueError('observation_index_reference_invalid')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or '\\' in value or path.as_posix() != value:
        raise ValueError('observation_index_reference_invalid')
    parts, prefix = path.parts, ()
    if key == 'helper_releases':
        if (len(parts) != 7 or not parts[0].startswith(('helper-', 'economy-'))
                or parts[1] not in ('Dota2Helper', 'Dota2Economy') or parts[2] != 'diagnostics'):
            raise ValueError('observation_index_release_layout_invalid')
        prefix, parts = parts[:3], parts[3:]
    if (len(parts) != 4 or parts[:2] != ('matches', 'sessions')
            or not re.fullmatch(r'run_[a-f0-9]{32}', parts[2]) or parts[3] != 'observation-handoff.json'):
        raise ValueError('observation_index_layout_invalid')
    root = Path(roots[key]).resolve(strict=True)
    target = root / Path(*path.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError('observation_index_outside_root')
    for parent in (target, *target.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('observation_index_link_refused')
    return root.joinpath(*prefix), parts[2]


def discover(roots, *, latest=20):
    if type(latest) is not int or not 1 <= latest <= 128:
        raise ValueError('observation_index_scan_limit_invalid')
    candidates, seen = [], set()
    for key, root, base in history_bases(roots):
        for path in base.glob('matches/sessions/run_*/observation-handoff.json'):
            if len(candidates) >= 10000:
                raise ValueError('observation_index_discovery_limit')
            lexical = str(path).casefold()
            if lexical not in seen:
                seen.add(lexical)
                candidates.append((path.stat().st_mtime_ns,
                    {'root_key': key, 'source': path.relative_to(root).as_posix()}))
    return [ref for _, ref in sorted(candidates, key=lambda row: (-row[0], row[1]['source']))[:latest]]


def sync(project, roots, *, latest=20):
    with index_destination(project, roots, 'equipment-handoff') as output:
        entries, packages, duplicates = [], set(), 0
        for ref in discover(roots, latest=latest):
            try:
                root, session = resolve_reference(ref, roots)
                manifest, package_id = verify(root, session)
                if package_id in packages:
                    duplicates += 1
                    continue
                packages.add(package_id)
                payload = manifest['payload']
                entries.append({**ref, 'package_id': package_id, 'state': 'ready_with_gaps',
                    'match_id': manifest['match_id'], 'game_session_id': manifest['game_session_id'],
                    'record_session_id': session, 'recording_integrity': manifest['recording_integrity'],
                    'rows': payload['rows'] if payload is not None else 0,
                    'channel_has_observations': payload is not None and payload['rows'] > 0,
                    'other_channels': 'not_closed_by_this_manifest'})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entries.append({**ref, 'state': 'blocked', 'error_type': type(exc).__name__})
        index = {'schema_version': 1, 'kind': 'equipment_observation_index',
                 'indexed_at_utc': datetime.now(timezone.utc).isoformat(), 'entries': entries,
                 'duplicate_packages_omitted': duplicates, 'index_time_is_not_data_time': True}
        write_index(output, index)
        return index


def read_index(path):
    return _read_index(path, kind='equipment_observation_index')


def journal_reference(entry, manifest):
    payload = manifest['payload']
    if payload is None:
        return None
    prefix = PurePosixPath(entry['source']).parts[:-4]
    return {'root_key': entry['root_key'], 'path': PurePosixPath(*prefix, 'matches', payload['path']).as_posix(),
            'bytes': payload['bytes'], 'sha256': payload['sha256'], 'content_kind': 'jsonl'}
