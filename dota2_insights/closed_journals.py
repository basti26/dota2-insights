"""Bounded, fingerprint-checked streaming from an admitted recording package."""
import hashlib
import json
from pathlib import Path

from .helper_handoff import FILE_LIMIT, LINE_LIMIT, MAX_ROWS, verify_package
from .replay_holdout import refuse_locked_match


def read_package(path):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 ** 2:
        raise ValueError('record_package_size_limit')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != path.stem:
        raise ValueError('record_package_digest_mismatch')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('record_package_object_required')
    return value


def explicit_match(row):
    """Check all supported identity fields; never silently prefer conflicting IDs."""
    from .helper_handoff import match_id
    candidates = []
    for value in (row, row.get('snapshot'), row.get('clock_context'),
                  (row.get('view_input') or {}).get('snapshot')):
        if isinstance(value, dict):
            mid = match_id(value.get('match_id'))
            if mid:
                candidates.append(mid)
    if len(set(candidates)) > 1:
        raise ValueError('record_row_match_identity_conflict')
    return candidates[0] if candidates else None


class ClosedJournals:
    def __init__(self, package, roots):
        self.package, self.roots = package, roots
        self.active = False

    def __enter__(self):
        if self.active:
            raise ValueError('record_context_already_active')
        verify_package(self.package, self.roots)
        self.active = True
        return self

    def __exit__(self, exc_type, exc, trace):
        try:
            if exc_type is None:
                verify_package(self.package, self.roots)
        finally:
            self.active = False

    def journals(self, kind=None):
        if not self.active:
            raise ValueError('record_verified_context_required')
        for component in self.package['components']:
            if component['state'] != 'closed_verified' or kind is not None and component['kind'] != kind:
                continue
            for journal in component.get('journals', []):
                ref = journal['source']
                if ref not in self.package['sources']:
                    raise ValueError('record_journal_not_admitted')
                if ref['content_kind'] == 'jsonl':
                    yield component, ref

    def rows(self, ref):
        if not self.active:
            raise ValueError('record_verified_context_required')
        if ref not in self.package['sources'] or ref['content_kind'] != 'jsonl':
            raise ValueError('record_journal_not_admitted')
        root = Path(self.roots[ref['root_key']]).resolve()
        path = (root / ref['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('record_journal_outside_history')
        from .minimap.offline_io import check_input_paths
        check_input_paths(path)
        if not 0 <= ref['bytes'] <= FILE_LIMIT or path.stat().st_size != ref['bytes']:
            raise ValueError('record_journal_size_changed')
        digest, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            for number in range(1, MAX_ROWS + 2):
                if not self.active:
                    raise ValueError('record_verified_context_required')
                raw = stream.readline(LINE_LIMIT + 1)
                if not raw:
                    break
                size += len(raw)
                if number > MAX_ROWS or len(raw) > LINE_LIMIT or size > FILE_LIMIT or not raw.endswith(b'\n'):
                    raise ValueError('record_journal_budget_or_partial_line')
                digest.update(raw)
                def nonfinite(_):
                    raise ValueError('record_nonfinite_json')
                row = json.loads(raw, parse_constant=nonfinite)
                if not isinstance(row, dict):
                    raise ValueError('record_row_object_required')
                mid = explicit_match(row)
                if mid and mid not in {m['match_id'] for m in self.package['matches']}:
                    refuse_locked_match(mid)
                    raise ValueError('record_row_foreign_match')
                for value in (row, row.get('clock_context')):
                    if not isinstance(value, dict): continue
                    game, session = value.get('game_session_id'), value.get('record_session_id')
                    if game is not None or session is not None:
                        candidates = [m for m in self.package['matches']
                            if (game is None or m['game_session_id'] == game)
                            and (session is None or m['record_session_id'] == session)
                            and (mid is None or m['match_id'] == mid)]
                        if not candidates:
                            raise ValueError('record_row_session_identity_conflict')
                yield number, row, {'source': ref, 'line': number,
                    'line_sha256': hashlib.sha256(raw).hexdigest()}
        if size != ref['bytes'] or digest.hexdigest() != ref['sha256']:
            raise ValueError('record_journal_changed_during_analysis')
