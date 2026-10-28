"""Bounded discovery and durable references to sealed journals, never copies GSI."""
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from .history import checked_output
from .match_journals import _json, _read, verify

KINDS = ('helper_history', 'helper_releases')
LAYOUT = 'self-economy/self-economy-*/closed-matches/match_*/game_*'


def resolve_reference(reference, roots):
    key, value = reference.get('root_key'), reference.get('source')
    if key not in KINDS or key not in roots or not isinstance(value, str) or len(value) > 1000:
        raise ValueError('match_journal_reference_invalid')
    path = PurePosixPath(value)
    if (path.is_absolute() or ':' in value or '\\' in value or '..' in path.parts
            or path.as_posix() != value):
        raise ValueError('match_journal_reference_invalid')
    parts = path.parts
    if key == 'helper_releases':
        if (len(parts) != 8 or not parts[0].startswith(('foresight-', 'helper-', 'economy-'))
                or parts[1] not in ('Foresight', 'ForesightEconomy', 'Dota2Helper', 'Dota2Economy') or parts[2] != 'diagnostics'):
            raise ValueError('match_journal_release_layout_invalid')
        parts = parts[3:]
    if (len(parts) != 5 or parts[0] != 'self-economy' or parts[2] != 'closed-matches'
            or not re.fullmatch(r'self-economy-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', parts[1])
            or not re.fullmatch(r'match_[1-9][0-9]{0,19}', parts[3])
            or not re.fullmatch(r'game_[a-f0-9]{32}', parts[4])):
        raise ValueError('match_journal_reference_layout_invalid')
    root = Path(roots[key]).resolve(strict=True)
    target = root / Path(*path.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError('match_journal_outside_history')
    for parent in (target, *target.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('match_journal_link_refused')
    return target


def history_bases(roots):
    """Only known source/release diagnostics roots, shared by journal types."""
    for key in KINDS:
        if key not in roots:
            continue
        root = Path(roots[key]).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('match_journal_history_unavailable')
        bases = [root] if key == 'helper_history' else []
        if key == 'helper_releases':
            releases = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(('foresight-', 'helper-', 'economy-'))]
            if len(releases) > 100:
                raise ValueError('match_journal_release_limit')
            for release in releases:
                bases.extend(release / app / 'diagnostics' for app in ('Foresight', 'ForesightEconomy', 'Dota2Helper', 'Dota2Economy'))
        for base in bases:
            yield key, root, base


def discover(roots, *, latest=20):
    if type(latest) is not int or not 1 <= latest <= 128:
        raise ValueError('match_journal_scan_limit_invalid')
    candidates, seen = [], set()
    for key, root, base in history_bases(roots):
        for folder in base.glob(LAYOUT):
            if len(candidates) >= 10000:
                raise ValueError('match_journal_discovery_limit')
            lexical = str(folder).casefold()
            if lexical not in seen:
                seen.add(lexical)
                candidates.append((folder.stat().st_mtime_ns,
                    {'root_key': key, 'source': folder.relative_to(root).as_posix()}))
    # This chooses recent directories, never claims their mtime is game time.
    return [ref for _, ref in sorted(candidates, key=lambda value: (-value[0], value[1]['source']))[:latest]]


@contextmanager
def index_destination(project, roots, directory):
    if directory not in ('native-match-handoff', 'equipment-handoff', 'draft-phase-handoff', 'minimap-run-handoff', 'audio-match-handoff', 'performance-match-handoff'):
        raise ValueError('journal_index_directory_invalid')
    project = Path(project).resolve()
    output = checked_output(project, project / 'diagnostics' / directory)
    if any(output.is_relative_to(Path(p).resolve()) for p in roots.values()):
        raise ValueError('match_journal_output_overlaps_history')
    output.mkdir(parents=True, exist_ok=True)
    lock = output / 'sync.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.close(descriptor)
        yield output
    finally:
        lock.unlink(missing_ok=True)


def write_index(output, index):
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.index-', suffix='.tmp', dir=output)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(index, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output / 'index.json')
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def sync(project, roots, *, latest=20):
    with index_destination(project, roots, 'native-match-handoff') as output:
        entries, package_ids, duplicates = [], set(), 0
        for ref in discover(roots, latest=latest):
            try:
                folder = resolve_reference(ref, roots)
                manifest, package_id = verify(folder)
                if package_id in package_ids:
                    duplicates += 1
                    continue
                package_ids.add(package_id)
                entries.append({**ref, 'state': 'ready_with_gaps', 'package_id': package_id,
                    'match_id': manifest['match_id'], 'game_session_id': manifest['game_session_id'],
                    'record_session_id': manifest['record_session_id'], 'run_id': manifest['run_id'],
                    'rows': manifest['payload']['rows'], 'recording_integrity': manifest['recording_integrity'],
                    'other_channels': 'not_closed_by_this_manifest'})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entries.append({**ref, 'state': 'blocked', 'error_type': type(exc).__name__})
        index = {'schema_version': 1, 'kind': 'native_match_journal_index',
                 'indexed_at_utc': datetime.now(timezone.utc).isoformat(), 'entries': entries,
                 'duplicate_packages_omitted': duplicates,
                 'index_time_is_not_data_time': True}
        write_index(output, index)
        return index


def read_index(path, *, kind='native_match_journal_index'):
    if kind not in ('native_match_journal_index', 'equipment_observation_index', 'draft_phase_prefix_index', 'closed_minimap_run_index', 'audio_match_handoff_index', 'performance_match_handoff_index'):
        raise ValueError('journal_index_kind_invalid')
    index = _json(_read(Path(path), 512 * 1024))
    entries = index.get('entries')
    if (type(index.get('schema_version')) is not int or index['schema_version'] != 1
            or index.get('kind') != kind
            or not isinstance(entries, list) or len(entries) > 128):
        raise ValueError('match_journal_index_invalid')
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('state') not in ('ready_with_gaps', 'blocked'):
            raise ValueError('match_journal_index_entry_invalid')
        if entry['state'] == 'ready_with_gaps':
            digest = entry.get('package_id')
            if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest) or digest in ids:
                raise ValueError('match_journal_index_package_invalid')
            ids.add(digest)
    return index
