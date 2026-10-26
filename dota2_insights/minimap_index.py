"""Discover standalone closed minimap runs without inventing match identity."""
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re

from .match_journal_index import history_bases, index_destination, write_index, read_index as _read_index
from .minimap_handoff import verify


def resolve_reference(reference, roots):
    key, value = reference.get('root_key'), reference.get('source')
    if (key not in ('helper_history', 'helper_releases') or key not in roots
            or not isinstance(value, str) or len(value) > 1000):
        raise ValueError('minimap_index_reference_invalid')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or '\\' in value or path.as_posix() != value:
        raise ValueError('minimap_index_reference_invalid')
    parts, prefix = path.parts, ()
    if key == 'helper_releases':
        if (len(parts) != 7 or not parts[0].startswith(('helper-', 'economy-'))
                or parts[1] not in ('Dota2Helper', 'Dota2Economy') or parts[2] != 'diagnostics'):
            raise ValueError('minimap_index_release_layout_invalid')
        prefix, parts = parts[:3], parts[3:]
    if (len(parts) != 4 or parts[:2] != ('minimap-learning', 'closed-runs')
            or not re.fullmatch(r'run_[a-f0-9]{32}', parts[2]) or parts[3] != 'record-handoff.json'):
        raise ValueError('minimap_index_layout_invalid')
    root = Path(roots[key]).resolve(strict=True)
    target = root / Path(*path.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError('minimap_index_outside_root')
    for parent in (target, *target.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('minimap_index_link_refused')
    return root.joinpath(*prefix), parts[2][4:]


def discover(roots, *, latest=20):
    if type(latest) is not int or not 1 <= latest <= 128:
        raise ValueError('minimap_index_scan_limit_invalid')
    candidates, seen = [], set()
    for key, root, base in history_bases(roots):
        for path in base.glob('minimap-learning/closed-runs/run_*/record-handoff.json'):
            if len(candidates) >= 10000:
                raise ValueError('minimap_index_discovery_limit')
            lexical = str(path).casefold()
            if lexical not in seen:
                seen.add(lexical)
                candidates.append((path.stat().st_mtime_ns,
                    {'root_key': key, 'source': path.relative_to(root).as_posix()}))
    return [ref for _, ref in sorted(candidates, key=lambda row: (-row[0], row[1]['source']))[:latest]]


def sync(project, roots, *, latest=20):
    with index_destination(project, roots, 'minimap-run-handoff') as output:
        entries, runs, duplicates, conflicts = [], {}, 0, 0
        for ref in discover(roots, latest=latest):
            try:
                root, run = resolve_reference(ref, roots)
                doc, _, package_id = verify(root, run)
                entry = {**ref, 'package_id': package_id, 'state': 'ready_with_gaps',
                    **{key: doc[key] for key in ('run_id', 'chain_id', 'match_id', 'started_at_utc', 'ended_at_utc', 'close_reason')}}
                if run not in runs:
                    runs[run] = len(entries)
                    entries.append(entry)
                else:
                    position = runs[run]
                    old = entries[position]
                    if old['state'] == 'blocked':
                        continue
                    if old['package_id'] == package_id:
                        duplicates += 1
                    else:
                        conflicts += 1
                        entries[position] = {**ref, 'state': 'blocked', 'run_id': run,
                            'error_type': 'conflicting_minimap_run_receipts'}
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entries.append({**ref, 'state': 'blocked', 'error_type': type(exc).__name__})
        index = {'schema_version': 1, 'kind': 'closed_minimap_run_index',
            'indexed_at_utc': datetime.now(timezone.utc).isoformat(), 'entries': entries,
            'duplicate_packages_omitted': duplicates, 'conflicting_run_packages': conflicts,
            'index_time_is_not_data_time': True, 'match_association': 'unknown_no_time_inference'}
        write_index(output, index)
        return index


def read_index(path):
    return _read_index(path, kind='closed_minimap_run_index')
