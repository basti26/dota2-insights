"""Discover BP prefixes and explicit late bindings without guessing matches."""
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re

from .match_journal_index import history_bases, index_destination, write_index, read_index as _read_index
from .draft_phase_handoff import verify


def resolve_reference(reference, roots):
    key, value = reference.get('root_key'), reference.get('source')
    if (key not in ('helper_history', 'helper_releases') or key not in roots
            or not isinstance(value, str) or len(value) > 1000):
        raise ValueError('draft_index_reference_invalid')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or '\\' in value or path.as_posix() != value:
        raise ValueError('draft_index_reference_invalid')
    parts, prefix = path.parts, ()
    if key == 'helper_releases':
        if (len(parts) != 6 or not parts[0].startswith(('helper-', 'economy-'))
                or parts[1] not in ('Dota2Helper', 'Dota2Economy') or parts[2] != 'diagnostics'):
            raise ValueError('draft_index_release_layout_invalid')
        prefix, parts = parts[:3], parts[3:]
    if (len(parts) != 3 or parts[0] != 'drafts' or not re.fullmatch(r'session_[a-f0-9]{32}', parts[1])
            or parts[2] != 'phase-handoff.json'):
        raise ValueError('draft_index_layout_invalid')
    root = Path(roots[key]).resolve(strict=True)
    target = root / Path(*path.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError('draft_index_outside_root')
    for parent in (target, *target.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('draft_index_link_refused')
    return root.joinpath(*prefix), parts[1].removeprefix('session_')


def discover(roots, *, latest=20):
    if type(latest) is not int or not 1 <= latest <= 128:
        raise ValueError('draft_index_scan_limit_invalid')
    candidates, seen = [], set()
    for key, root, base in history_bases(roots):
        for path in base.glob('drafts/session_*/phase-handoff.json'):
            if len(candidates) >= 10000:
                raise ValueError('draft_index_discovery_limit')
            lexical = str(path).casefold()
            if lexical not in seen:
                seen.add(lexical)
                # A late binding can make an old prefix newly useful. This is
                # discovery order only, never its observation/game timestamp.
                binding = path.with_name('phase-match-binding.json')
                modified = max(path.stat().st_mtime_ns, binding.stat().st_mtime_ns if binding.exists() else 0)
                candidates.append((modified, {'root_key': key, 'source': path.relative_to(root).as_posix()}))
    return [ref for _, ref in sorted(candidates, key=lambda row: (-row[0], row[1]['source']))[:latest]]


def sync(project, roots, *, latest=20):
    with index_destination(project, roots, 'draft-phase-handoff') as output:
        entries, positions, duplicates, conflicts = [], {}, 0, 0
        for ref in discover(roots, latest=latest):
            try:
                root, sid = resolve_reference(ref, roots)
                manifest, package_id = verify(root, sid)
                entry = {**ref, 'package_id': package_id, 'state': 'ready_with_gaps',
                    'binding_package_id': manifest['binding_package_id'], 'session_id': sid,
                    'match_id_at_cutoff': manifest['match_id_at_cutoff'], 'effective_match_id': manifest['effective_match_id'],
                    'ending_phase': manifest['ending_phase'], 'source_error_present': manifest['source_error_present'],
                    'source_limit_reached': manifest['source_limit_reached']}
                if package_id not in positions:
                    positions[package_id] = len(entries)
                    entries.append(entry)
                    continue
                duplicates += 1
                position = positions[package_id]
                old = entries[position]
                if old['state'] == 'blocked' or old['binding_package_id'] == entry['binding_package_id']:
                    continue
                if old['binding_package_id'] is None:
                    entries[position] = entry  # Prefer explicit binding over a pre-binding copy.
                elif entry['binding_package_id'] is not None:
                    conflicts += 1
                    entries[position] = {**ref, 'state': 'blocked', 'package_id': package_id,
                                         'error_type': 'conflicting_match_bindings'}
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entries.append({**ref, 'state': 'blocked', 'error_type': type(exc).__name__})
        index = {'schema_version': 1, 'kind': 'draft_phase_prefix_index',
            'indexed_at_utc': datetime.now(timezone.utc).isoformat(), 'entries': entries,
            'duplicate_packages_omitted': duplicates, 'conflicting_binding_packages': conflicts,
            'index_time_is_not_data_time': True}
        write_index(output, index)
        return index


def read_index(path):
    return _read_index(path, kind='draft_phase_prefix_index')
