"""Bounded streaming resolution of explicitly recorded historical text gaps."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import time

PATTERN = re.compile(rb'(?<!\d)(\d{10})(?!\d)')


def scan_text(path, deadline, *, chunk_size=1024 * 1024, guard=lambda: None):
    path = Path(path)
    before = path.stat()
    if before.st_size > 256 * 1024 * 1024:
        raise ValueError('stream_file_budget')
    if chunk_size < 64:
        raise ValueError('chunk_size_too_small')
    found, tail = set(), b''
    sha = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        while True:
            guard()
            if time.monotonic() > deadline:
                raise TimeoutError('stream_wall_budget')
            block = stream.read(chunk_size)
            if not block:
                break
            total += len(block)
            if total > 256 * 1024 * 1024:
                raise ValueError('stream_file_budget')
            sha.update(block)
            content = tail + block
            # 64-byte overlap preserves digit-boundary checks across chunks.
            found.update(m.group(1).decode('ascii') for m in PATTERN.finditer(content)
                         if m.end() <= len(content) - 32 and (not tail or m.start() > 0))
            tail = content[-64:]
            if len(found) > 100000:
                raise ValueError('stream_id_budget')
        found.update(m.group(1).decode('ascii') for m in PATTERN.finditer(tail)
                     if total <= 64 or m.start() > 0)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or total != before.st_size:
        raise ValueError('stream_source_changed')
    return found, sha.hexdigest()


def resolve_gaps(inventory, *, max_seconds=180, guard=lambda: None):
    result = deepcopy(inventory)
    deadline = time.monotonic() + max_seconds
    remaining, completed = [], []
    for issue in inventory['scan_issues']:
        if issue['reason'] != 'text_exceeds_scan_limit':
            remaining.append(issue)
            continue
        try:
            ids, fingerprint = scan_text(issue['path'], deadline, guard=guard)
            for mid in ids:
                reasons = result['exposure'].setdefault(mid, [])
                if 'historical_reference' not in reasons:
                    reasons.append('historical_reference')
                    reasons.sort()
            completed.append({'path': issue['path'], 'sha256': fingerprint, 'match_count': len(ids),
                              'scan_method': 'stream_ascii_digit_references_not_semantic_analysis'})
        except (OSError, ValueError, TimeoutError) as error:
            remaining.append({**issue, 'stream_error': type(error).__name__})
    result['history_sources'].extend(completed)
    result['scan_issues'] = remaining
    result['history_scan_complete'] = not remaining
    result['stream_resolved_file_count'] = len(completed)
    for row in result['local_replays']:
        row['exclusions'] = sorted(result['exposure'].get(row['match_id'], []))
        row['eligibility'] = 'excluded' if row['exclusions'] else 'requires_footer_and_history_review'
    # Do not advance admission or visual-verification state on negative searches.
    result['eligibility_certified'] = False
    result['independent_validation_passed'] = False
    return result
