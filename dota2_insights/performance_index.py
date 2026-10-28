"""Bounded source/release discovery of stopped per-match performance captures."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath
import re

from .match_journals import _json, _read
from .match_journal_index import history_bases, index_destination, write_index, read_index as _read_index
from .performance_handoff import verify
from .presentmon_metrics import MAX_CSV_BYTES

MAX_READ_BYTES = 2*MAX_CSV_BYTES


class PerformanceIndexBudgetExceeded(ValueError):
    pass


def resolve_reference(reference, roots):
    key, value = reference.get('root_key'), reference.get('source')
    if (key not in ('helper_history', 'helper_releases') or key not in roots
            or not isinstance(value, str) or len(value) > 1000):
        raise ValueError('performance_index_reference_invalid')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or '\\' in value or path.as_posix() != value:
        raise ValueError('performance_index_reference_invalid')
    parts = path.parts
    if key == 'helper_releases':
        if (len(parts) != 6 or not parts[0].startswith(('foresight-', 'helper-', 'economy-'))
                or parts[1] not in ('Foresight', 'ForesightEconomy', 'Dota2Helper', 'Dota2Economy') or parts[2] != 'diagnostics'):
            raise ValueError('performance_index_release_layout_invalid')
        parts = parts[3:]
    if (len(parts) != 3 or parts[0] != 'game-frames' or parts[2] != 'closed-match'
            or not re.fullmatch(r'run_[a-f0-9]{32}', parts[1])):
        raise ValueError('performance_index_layout_invalid')
    root = Path(roots[key]).resolve(strict=True)
    target = root/Path(*path.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError('performance_index_outside_root')
    for parent in (target, *target.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('performance_index_link_refused')
    return target


def discover(roots, *, latest=20):
    if type(latest) is not int or not 1 <= latest <= 128:
        raise ValueError('performance_index_scan_limit_invalid')
    candidates, seen = [], set()
    for key, root, base in history_bases(roots):
        for manifest in base.glob('game-frames/run_*/closed-match/record-handoff.json'):
            if len(candidates) >= 10000:
                raise ValueError('performance_index_discovery_limit')
            lexical = str(manifest).casefold()
            if lexical not in seen:
                seen.add(lexical)
                candidates.append((manifest.stat().st_mtime_ns, {'root_key': key,
                    'source': manifest.parent.relative_to(root).as_posix()}))
    return [ref for _, ref in sorted(candidates, key=lambda r: (-r[0], r[1]['source']))[:latest]]


def sync(project, roots, *, latest=20):
    with index_destination(project, roots, 'performance-match-handoff') as output:
        entries, captures, duplicates, conflicts, reserved = [], {}, 0, 0, 0
        for ref in discover(roots, latest=latest):
            try:
                folder = resolve_reference(ref, roots)
                raw = _read(folder/'record-handoff.json', 32768)
                preliminary = _json(raw)
                sources = preliminary.get('sources')
                if not isinstance(sources, dict) or any(not isinstance(s, dict) or type(s.get('bytes')) is not int
                        or s['bytes'] < 0 for s in sources.values()):
                    raise ValueError('performance_index_source_spec_invalid')
                cost = sum(s['bytes'] for s in sources.values())
                if reserved+cost > MAX_READ_BYTES:
                    raise PerformanceIndexBudgetExceeded()
                reserved += cost  # Failed attempts consume their reserved budget too.
                package = verify(folder, hashlib.sha256(raw).hexdigest(), source_budget=cost)
                doc = package['document']
                identity = doc['capture_run_id']
                entry = {**ref, 'state': 'ready_with_gaps', 'package_id': package['package_id'],
                    'capture_run_id': identity, **doc['scope'], 'closed_at_utc': doc['closed_at_utc'],
                    'end_provider_at_utc': doc['end_provider_at_utc'],
                    'source_bytes': cost, 'other_channels': 'not_closed_by_this_manifest'}
                if identity not in captures:
                    captures[identity] = len(entries)
                    entries.append(entry)
                else:
                    position = captures[identity]
                    old = entries[position]
                    if old['state'] == 'blocked':
                        continue
                    if old['package_id'] == entry['package_id']:
                        duplicates += 1
                    else:
                        conflicts += 1
                        entries[position] = {**ref, 'state': 'blocked', 'capture_run_id': identity,
                            'error_type': 'conflicting_performance_capture_receipts'}
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entries.append({**ref, 'state': 'blocked', 'error_type': type(exc).__name__})
        index = {'schema_version': 1, 'kind': 'performance_match_handoff_index',
            'indexed_at_utc': datetime.now(timezone.utc).isoformat(), 'entries': entries,
            'duplicate_packages_omitted': duplicates, 'conflicting_capture_packages': conflicts,
            'reserved_source_bytes': reserved, 'source_read_budget_bytes': MAX_READ_BYTES,
            'index_time_is_not_data_time': True}
        write_index(output, index)
        return index


def read_index(path):
    return _read_index(path, kind='performance_match_handoff_index')
