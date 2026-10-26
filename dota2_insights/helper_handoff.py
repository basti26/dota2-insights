"""Read-only admission of closed helper journals; never copies payloads or imports helper."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import time
import uuid

META_LIMIT = 512 * 1024
FILE_LIMIT = 128 * 1024 ** 2
CSV_LIMIT = 512 * 1024 ** 2
RUN_LIMIT = 512 * 1024 ** 2
LINE_LIMIT = 1024 ** 2
MAX_ROWS = 100000
TOKEN = re.compile(r'[a-zA-Z0-9_-]{1,100}')
RUN = re.compile(r'run_[a-f0-9]{32}')


def _relative(value):
    if (not isinstance(value, str) or not value or ':' in value or '\\' in value
            or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts):
        raise ValueError('handoff_package_source_path_invalid')
    return PurePosixPath(value)


def _registry_base(root_key, relative):
    p = _relative(relative)
    prefix = p.parts[:-3]
    if (p.name != 'recorders.json' or len(p.parts) < 3 or p.parts[-3] != 'field-recording'
            or not RUN.fullmatch(p.parts[-2])):
        raise ValueError('handoff_registry_layout_invalid')
    if root_key == 'helper_history' and not prefix:
        return PurePosixPath('.')
    if (root_key == 'helper_releases' and len(prefix) == 3
            and prefix[0].startswith(('helper-', 'economy-'))
            and prefix[1] in ('Dota2Helper', 'Dota2Economy') and prefix[2] == 'diagnostics'):
        return PurePosixPath(*prefix)
    raise ValueError('handoff_registry_layout_invalid')


def _admitted_source(relative):
    token, run = r'[A-Za-z0-9_-]{1,100}', r'run_[a-f0-9]{32}'
    patterns = [
        rf'field-recording/{run}/recorders\.json',
        rf'self-economy/self-economy-{token}/(?:recording-summary\.json|samples\.jsonl)',
        rf'matches/sessions/{run}/(?:recording-summary\.json|context\.jsonl|roster-evidence\.jsonl|buff-samples\.jsonl)',
        rf'matches/match_[1-9][0-9]{{0,19}}/{run}/observations\.jsonl',
        r'drafts/session_[a-f0-9]{32}/(?:session-end\.json|events\.jsonl|ui-presented\.jsonl|portrait-visibility\.jsonl|chat-candidates\.jsonl|ban-window-candidates\.jsonl)',
        rf'minimap-learning/{run}/(?:session\.json|samples\.jsonl|capture_attempts\.jsonl|context\.jsonl|markers\.jsonl|rejected\.jsonl)',
        rf'field-visuals/{run}/(?:session\.json|screenshots\.jsonl)',
        rf'match-audio/{token}/(?:summary\.json|segments\.jsonl|reference-gsi\.jsonl)',
        rf'game-frames/{run}/(?:capture\.json|frames\.csv|context\.jsonl)',
        rf'process-metrics/{run}/metrics\.jsonl']
    return any(re.fullmatch(pattern, relative) for pattern in patterns)


def utc(value):
    if not isinstance(value, str):
        raise ValueError('handoff_utc_required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError('handoff_time_must_be_utc')
    return result


def match_id(value):
    if value is None or value == '' or type(value) in (str, int) and value in (0, '0'):
        return None
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value):
        raise ValueError('handoff_match_id_invalid')
    return value


def _json(raw):
    def invalid(_):
        raise ValueError('handoff_nonfinite_json')
    value = json.loads(raw, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError('handoff_object_required')
    return value


def _fingerprint(path):
    s = path.stat()
    return s.st_size, s.st_mtime_ns, s.st_ino


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Inputs:
    def __init__(self, root_key, root, *, quiet_seconds=2):
        if type(quiet_seconds) not in (int, float) or not 0 <= quiet_seconds <= 60:
            raise ValueError('handoff_quiet_seconds_invalid')
        self.root_key, self.root = root_key, Path(root).resolve(strict=True)
        self.quiet_seconds, self.total = quiet_seconds, 0
        self.sources, self.cache = {}, {}
        self.associations = None

    def path(self, value):
        p = Path(value).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError('handoff_source_outside_root')
        return p

    def read(self, value, *, mode='json', declared_match=None, expected_run_id=None):
        path = self.path(value)
        from .minimap.offline_io import check_input_paths
        check_input_paths(path)
        key = str(path)
        cache_key = (key, mode, declared_match, expected_run_id)
        if cache_key in self.cache:
            return self.cache[cache_key]
        before = _fingerprint(path)
        limit = META_LIMIT if mode == 'json' else CSV_LIMIT if mode == 'bytes' else FILE_LIMIT
        if before[0] > limit or self.total + before[0] > RUN_LIMIT:
            raise ValueError('handoff_source_size_limit')
        if time.time_ns() - before[1] < self.quiet_seconds * 1e9:
            raise ValueError('handoff_source_not_quiet')
        digest = hashlib.sha256()
        stats = {'rows': 0, 'reported_match_counts': {}, 'unbound_rows': 0,
                 'post_game_match_ids': [], 'time_fields': {}}
        with path.open('rb') as stream:
            if mode == 'json':
                raw = stream.read(META_LIMIT + 1)
                if len(raw) > META_LIMIT:
                    raise ValueError('handoff_source_size_limit')
                digest.update(raw)
                result = _json(raw)
            else:
                matches, ended = Counter(), set()
                consumed = 0
                while raw := stream.readline(LINE_LIMIT + 1):
                    consumed += len(raw)
                    if consumed > limit or self.total + consumed > RUN_LIMIT:
                        raise ValueError('handoff_source_size_limit')
                    if len(raw) > LINE_LIMIT or stats['rows'] >= (MAX_ROWS if mode == 'jsonl' else 2000000):
                        raise ValueError('handoff_journal_limit')
                    digest.update(raw)
                    stats['rows'] += 1
                    if mode == 'jsonl':
                        if not raw.endswith(b'\n'):
                            raise ValueError('handoff_journal_partial_line')
                        row = _json(raw)
                        snapshot = row.get('snapshot')
                        snapshot = snapshot if isinstance(snapshot, dict) else {}
                        mid = match_id(row.get('match_id', snapshot.get('match_id')))
                        if expected_run_id and row.get('run_id') not in (None, expected_run_id):
                            raise ValueError('handoff_journal_run_conflict')
                        game = row.get('game_session_id')
                        if game is not None and self.associations is not None:
                            if not isinstance(game, str) or game not in self.associations:
                                raise ValueError('handoff_journal_game_session_conflict')
                            association = self.associations[game]
                            if (mid is not None and association['match_id'] is not None and mid != association['match_id']
                                    or row.get('record_session_id') not in (None, association['record_session_id'])):
                                raise ValueError('handoff_journal_game_session_conflict')
                        if mid is not None:
                            from .replay_holdout import refuse_locked_match
                            refuse_locked_match(mid)
                            if declared_match and mid != declared_match:
                                raise ValueError('handoff_journal_match_conflict')
                            matches[mid] += 1
                        else:
                            stats['unbound_rows'] += 1
                        if len(matches) > 128:
                            raise ValueError('handoff_journal_match_limit')
                        context = row.get('map_context')
                        if mid and isinstance(context, dict) and context.get('game_state') == 'DOTA_GAMERULES_STATE_POST_GAME':
                            ended.add(mid)
                        for name in ('captured_at_utc', 'observed_at_utc', 'received_at_utc', 'written_at_utc'):
                            value = row.get(name)
                            if value is None:
                                continue
                            try:
                                stamp = utc(value).isoformat()
                            except (ValueError, TypeError):
                                continue
                            span = stats['time_fields'].setdefault(name, {'first': stamp, 'last': stamp})
                            span['first'], span['last'] = min(span['first'], stamp), max(span['last'], stamp)
                stats['reported_match_counts'], stats['post_game_match_ids'] = dict(matches), sorted(ended)
                stats['row_meaning'] = 'json_objects' if mode == 'jsonl' else 'physical_lines_including_header_not_frame_count'
                result = stats
        if _fingerprint(path) != before:
            raise ValueError('handoff_source_changed_during_read')
        ref = {'root_key': self.root_key, 'path': path.relative_to(self.root).as_posix(),
               'bytes': before[0], 'sha256': digest.hexdigest(), 'content_kind': mode}
        if key in self.sources and self.sources[key] != ref:
            raise ValueError('handoff_source_changed_between_reads')
        if key not in self.sources:
            self.total += before[0]
            self.sources[key] = ref
        self.cache[cache_key] = result, ref
        return result, ref

    def verify(self):
        for name, ref in self.sources.items():
            p = Path(name)
            before = _fingerprint(p)
            if before[0] != ref['bytes'] or _sha(p) != ref['sha256'] or _fingerprint(p) != before:
                raise ValueError('handoff_source_changed_after_read')


def _health(*values):
    warnings = set()
    queue = list(values)
    visited = 0
    while queue:
        value = queue.pop()
        if not isinstance(value, dict):
            continue
        visited += 1
        if visited > 4096:
            warnings.add('health_metadata_truncated')
            break
        queue.extend(v for v in value.values() if isinstance(v, dict))
        for key in ('limit_reached', 'limited', 'image_limit_reached', 'roster_image_limit_reached'):
            if value.get(key) is True:
                warnings.add(key)
        for key in ('dropped', 'images_failed', 'images_skipped', 'roster_images_skipped', 'write_errors',
                    'encode_errors', 'partial_index_rows', 'orphan_images', 'skipped_budget', 'rollback_failures'):
            if type(value.get(key)) is int and value[key] > 0:
                warnings.add(key)
        if value.get('error') or value.get('errors') or value.get('close_error') or value.get('error_present'):
            warnings.add('writer_error_present')
        if value.get('complete') is False or value.get('evidence_complete') is False:
            warnings.add('coverage_incomplete')
        if value.get('limits_reached'):
            warnings.add('limits_reached')
    return sorted(warnings)


def _drained(summary):
    status = summary.get('run_recording_status') or {}
    if not isinstance(status, dict) or summary.get('evidence_queue_drained') is not True:
        raise ValueError('handoff_writer_not_drained')
    for name in ('pending', 'queue_depth', 'inflight'):
        if name in status and (type(status[name]) is not int or status[name] != 0):
            raise ValueError('handoff_writer_pending')
    if status.get('accepting') is True:
        raise ValueError('handoff_writer_accepting')
    return status


def inspect_registry(root_key, root, registry, *, quiet_seconds=2):
    """Closed means journal admission only, never completion of a match or image audit."""
    inputs = Inputs(root_key, root, quiet_seconds=quiet_seconds)
    registry = inputs.path(registry)
    _registry_base(root_key, registry.relative_to(inputs.root).as_posix())
    if registry.name != 'recorders.json' or not RUN.fullmatch(registry.parent.name):
        raise ValueError('handoff_registry_path_invalid')
    document, ref = inputs.read(registry)
    package = {'schema_version': 1, 'kind': 'helper_record_package', 'source_registry': ref,
        'launcher_run_id': registry.parent.name, 'state': 'waiting', 'matches': [],
        'components': [], 'issues': [], 'sources': [], 'binary_assets_verified': False,
        'scope': 'closed_journal_index_only_no_payload_copy_no_accuracy_claim',
        'generator': {'protocol': 'legacy_helper_closed_v1', 'source_sha256': _sha(Path(__file__))}}
    if document.get('schema_version') != 1 or type(document.get('schema_version')) is not int or document.get('scope') != 'launcher_recording_status_only':
        raise ValueError('handoff_registry_schema_invalid')
    if document.get('closed') is not True:
        package['issues'] = ['launcher_not_closed']
        package['sources'] = list(inputs.sources.values())
        return package
    started, closed = utc(document.get('started_at_utc')), utc(document.get('closed_at_utc'))
    if closed < started:
        raise ValueError('handoff_registry_clock_reversed')
    package['started_at_utc'], package['closed_at_utc'] = started.isoformat(), closed.isoformat()
    associations = document.get('game_sessions')
    if not isinstance(associations, dict) or len(associations) > 64:
        raise ValueError('handoff_game_associations_missing')
    from .replay_holdout import refuse_locked_match
    for game, association in associations.items():
        if (not isinstance(game, str) or not re.fullmatch(r'game_[a-f0-9]{32}', game)
                or not isinstance(association, dict) or not RUN.fullmatch(str(association.get('record_session_id', '')))):
            raise ValueError('handoff_game_association_invalid')
        mid = match_id(association.get('match_id'))
        if mid:
            refuse_locked_match(mid)
        package['matches'].append({'match_id': mid, 'game_session_id': game,
            'record_session_id': association['record_session_id'], 'match_end_evidence': 'not_observed'})
    observed = document.get('observed_match_ids', [])
    if not isinstance(observed, list) or len(observed) > 128:
        raise ValueError('handoff_observed_matches_invalid')
    allowed_matches = {r['match_id'] for r in package['matches'] if r['match_id']}
    for mid in observed:
        mid = match_id(mid)
        if mid:
            refuse_locked_match(mid)
            if mid not in allowed_matches:
                package['issues'].append('observed_match_without_session_association')
            allowed_matches.add(mid)
    inputs.associations = {r['game_session_id']: r for r in package['matches']}
    if registry.parent.parent.name != 'field-recording':
        raise ValueError('handoff_registry_layout_invalid')
    base = registry.parent.parent.parent
    recorders = document.get('recorders')
    if not isinstance(recorders, dict) or any(v is not None and not isinstance(v, dict) for v in recorders.values()):
        raise ValueError('handoff_recorder_status_missing')

    def component(kind, folder, metadata, *, mode, required=(), optional=(), ack=None, declared_match=None):
        result = {'kind': kind, 'state': 'blocked', 'issues': [], 'warnings': [], 'journals': [],
                  'association': 'explicit_match_folder' if declared_match else 'launcher_scope_only',
                  'declared_match_id': declared_match}
        package['components'].append(result)
        try:
            if isinstance(ack, dict) and ack.get('shutdown_complete') is False:
                raise ValueError('handoff_registry_reports_incomplete_shutdown')
            d, source = inputs.read(folder / metadata)
            result['metadata'] = source
            if mode == 'drained':
                status = _drained(d)
                utc(d.get('closed_at_utc'))
            elif mode == 'draft':
                status = d.get('recording') or {}
                if status.get('closed') is not True:
                    raise ValueError('handoff_component_not_closed')
                utc(d.get('closed_at_utc'))
                if d.get('session_id') != folder.name.removeprefix('session_'):
                    raise ValueError('handoff_component_identity_conflict')
            elif mode == 'audio':
                status = d
                if (not isinstance(ack, dict) or ack.get('shutdown_complete') is not True
                        or d.get('run_id') != folder.name or d.get('running') is not False
                        or d.get('worker_alive') is not False or d.get('finalizing') is not False):
                    raise ValueError('handoff_audio_not_closed')
            elif mode == 'presentmon':
                status = d
                if d.get('status') != 'closed' or d.get('shutdown_complete') is not True:
                    raise ValueError('handoff_component_not_closed')
                utc(d.get('ended_at_utc'))
            else:
                status = d
                if d.get('closed') is not True:
                    raise ValueError('handoff_component_not_closed')
                utc(d.get('ended_at_utc'))
                if mode == 'minimap':
                    evidence = d.get('recording_evidence') or {}
                    if evidence.get('pending_images') != 0 or type(evidence.get('pending_images')) is not int:
                        raise ValueError('handoff_minimap_pending_unknown')
                    if d.get('run_id') != folder.name.removeprefix('run_'):
                        raise ValueError('handoff_component_identity_conflict')
            result['warnings'] = _health(ack, d, status)
            for name in [*required, *(n for n in optional if (folder / n).is_file())]:
                stats, source = inputs.read(folder / name, mode='jsonl' if name.endswith('.jsonl') else 'bytes', declared_match=declared_match,
                                           expected_run_id=ack.get('run_id') if kind == 'own_gsi' and isinstance(ack, dict) else None)
                if set(stats['reported_match_counts']) - allowed_matches:
                    raise ValueError('handoff_journal_match_not_in_registry')
                result['journals'].append({'source': source, 'summary': stats})
            result['state'] = 'closed_verified'
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('handoff_') else 'handoff_source_unavailable_or_invalid'
            result['issues'].append(code)
        return result

    def token(value):
        if not isinstance(value, str) or not TOKEN.fullmatch(value):
            raise ValueError('handoff_run_token_invalid')
        return value

    own = recorders.get('self_recording') or {}
    if own.get('run_id'):
        rid = token(own['run_id'])
        result = component('own_gsi', base / 'self-economy' / ('self-economy-' + rid),
                          'recording-summary.json', mode='drained', required=['samples.jsonl'], ack=own)
        if result.get('metadata') and result['metadata']:
            d, _ = inputs.read(base / 'self-economy' / ('self-economy-' + rid) / 'recording-summary.json')
            if d.get('run_id') != rid:
                result['state'] = 'blocked'
                result['issues'].append('handoff_component_identity_conflict')
    if recorders.get('recording') is not None:
        for association in package['matches']:
            session = association['record_session_id']
            result = component('economy_context', base / 'matches/sessions' / session,
                'recording-summary.json', mode='drained', optional=['context.jsonl', 'roster-evidence.jsonl', 'buff-samples.jsonl'], ack=recorders.get('recording'))
            mid = association['match_id']
            if mid and result['state'] == 'closed_verified':
                path = base / 'matches' / ('match_' + mid) / session / 'observations.jsonl'
                try:
                    stats, source = inputs.read(path, mode='jsonl', declared_match=mid)
                    result['journals'].append({'source': source, 'summary': stats})
                    result['association'], result['declared_match_id'] = 'registry_session_and_match_folder', mid
                except (OSError, ValueError):
                    result['state'] = 'blocked'
                    result['issues'].append('handoff_observations_unavailable_or_conflicting')
    draft = recorders.get('draft_recording') or {}
    if draft.get('session_id'):
        component('bp', base / 'drafts' / ('session_' + token(draft['session_id'])),
                  'session-end.json', mode='draft', required=['events.jsonl'],
                  optional=['ui-presented.jsonl', 'portrait-visibility.jsonl', 'chat-candidates.jsonl', 'ban-window-candidates.jsonl'], ack=draft)
    local = document.get('local_run_folders') or {}
    if not isinstance(local, dict):
        raise ValueError('handoff_local_run_folders_invalid')
    for kind, directory, files in [('minimap', 'minimap-learning', ['samples.jsonl', 'capture_attempts.jsonl', 'context.jsonl', 'markers.jsonl', 'rejected.jsonl']),
                                    ('visuals', 'field-visuals', ['screenshots.jsonl'])]:
        folders = local.get(kind, [])
        if not isinstance(folders, list) or len(folders) > 64:
            raise ValueError('handoff_local_run_folders_invalid')
        for run in folders:
            if not isinstance(run, str) or not RUN.fullmatch(run):
                raise ValueError('handoff_local_run_folder_invalid')
            component(kind, base / directory / run, 'session.json', mode=kind,
                      required=files[:1], optional=files[1:], ack=recorders.get(kind))
    audio = recorders.get('audio_samples') or {}
    if audio.get('run_id'):
        component('audio', base / 'match-audio' / token(audio['run_id']), 'summary.json', mode='audio',
                  required=['segments.jsonl'], optional=['reference-gsi.jsonl'], ack=audio)
    frames = recorders.get('presentmon') or {}
    if frames.get('run_id'):
        component('fps', base / 'game-frames' / ('run_' + token(frames['run_id'])), 'capture.json',
                  mode='presentmon', required=['frames.csv'], optional=['context.jsonl'], ack=frames)
    metrics = recorders.get('process_metrics') or {}
    if metrics.get('run_id'):
        result = {'kind': 'process_metrics', 'state': 'blocked', 'issues': [], 'warnings': _health(metrics),
                  'association': 'launcher_scope_only', 'journals': []}
        package['components'].append(result)
        if metrics.get('active') is False:
            try:
                stats, source = inputs.read(base / 'process-metrics' / ('run_' + token(metrics['run_id'])) / 'metrics.jsonl', mode='jsonl')
                result['journals'].append({'source': source, 'summary': stats})
                result['state'] = 'closed_verified'
            except (OSError, ValueError):
                result['issues'].append('handoff_metrics_unavailable_or_invalid')
        else:
            result['issues'].append('handoff_metrics_still_active')
    ended = set()
    for c in package['components']:
        if c['kind'] == 'own_gsi' and c['state'] == 'closed_verified':
            for journal in c['journals']:
                ended.update(journal['summary']['post_game_match_ids'])
    for association in package['matches']:
        if association['match_id'] in ended:
            association['match_end_evidence'] = 'own_recorded_gsi_post_game'
    inputs.verify()
    good = [c for c in package['components'] if c['state'] == 'closed_verified']
    package['state'] = 'ready' if good and len(good) == len(package['components']) and not any(c['warnings'] for c in good) else 'ready_with_gaps' if good else 'blocked'
    if not package['matches']:
        package['issues'].append('no_explicit_game_associations')
    if package['issues'] and package['state'] == 'ready':
        package['state'] = 'ready_with_gaps'
    package['sources'] = list(inputs.sources.values())
    return package


def verify_package(package, roots):
    """Consumers must call again before analysis; an old ready index is not a lease."""
    if package.get('kind') != 'helper_record_package' or type(package.get('schema_version')) is not int or package['schema_version'] != 1:
        raise ValueError('handoff_package_schema_invalid')
    if package.get('state') not in ('ready', 'ready_with_gaps'):
        raise ValueError('handoff_package_not_ready')
    sources, registry = package.get('sources'), package.get('source_registry')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 1024 or not isinstance(registry, dict) or registry not in sources:
        raise ValueError('handoff_package_sources_invalid')
    base = _registry_base(registry.get('root_key'), registry.get('path'))
    if package.get('launcher_run_id') != PurePosixPath(registry['path']).parent.name:
        raise ValueError('handoff_package_identity_invalid')
    associations, components = package.get('matches'), package.get('components')
    if not isinstance(associations, list) or len(associations) > 64 or not isinstance(components, list) or not components:
        raise ValueError('handoff_package_identity_invalid')
    for association in associations:
        if (not isinstance(association, dict) or not re.fullmatch(r'game_[a-f0-9]{32}', str(association.get('game_session_id', '')))
                or not RUN.fullmatch(str(association.get('record_session_id', '')))):
            raise ValueError('handoff_package_identity_invalid')
        match_id(association.get('match_id'))
    if not any(isinstance(c, dict) and c.get('state') == 'closed_verified' for c in components):
        raise ValueError('handoff_package_not_ready')
    seen = set()
    for source in sources:
        if not isinstance(source, dict) or source.get('root_key') != registry['root_key']:
            raise ValueError('handoff_package_sources_invalid')
        path = _relative(source.get('path'))
        try:
            local = path.relative_to(base).as_posix()
        except ValueError:
            raise ValueError('handoff_package_source_path_invalid') from None
        if not _admitted_source(local) or local.casefold() in seen:
            raise ValueError('handoff_package_source_path_invalid')
        seen.add(local.casefold())
        if (type(source.get('bytes')) is not int or not 0 <= source['bytes'] <= CSV_LIMIT
                or not isinstance(source.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', source['sha256'])):
            raise ValueError('handoff_package_sources_invalid')
        # Refuse a match-bearing path before hashing its body.
        from .minimap.offline_io import check_input_paths
        check_input_paths(path)
    for association in associations:
        from .replay_holdout import refuse_locked_match
        mid = match_id(association.get('match_id'))
        if mid:
            refuse_locked_match(mid)
    for source in sources:
        relative = source.get('path')
        if (not isinstance(relative, str) or not relative or ':' in relative or '\\' in relative
                or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts):
            raise ValueError('handoff_package_source_path_invalid')
        reader = Inputs(source['root_key'], roots[source['root_key']], quiet_seconds=0)
        path = reader.path(reader.root / source['path'])
        from .minimap.offline_io import check_input_paths
        check_input_paths(path)
        if path.stat().st_size != source['bytes'] or _sha(path) != source['sha256']:
            raise ValueError('handoff_index_stale')
    return True


def discover(roots, *, latest=5):
    if type(latest) is not int or not 1 <= latest <= 25:
        raise ValueError('handoff_latest_range')
    if not any(key in roots for key in ('helper_history', 'helper_releases')):
        raise ValueError('handoff_recording_roots_not_configured')
    candidates, seen = [], set()
    for key in ('helper_history', 'helper_releases'):
        if key not in roots:
            continue
        root = Path(roots[key]).resolve()
        if not root.is_dir():
            raise ValueError('handoff_history_root_unavailable')
        bases = [root] if key == 'helper_history' else []
        if key == 'helper_releases' and root.is_dir():
            releases = sorted((p for p in root.iterdir() if p.is_dir() and p.name.startswith(('helper-', 'economy-'))), reverse=True)
            if len(releases) > 100:
                raise ValueError('handoff_release_directory_limit')
            for release in releases:
                bases.extend(release / app / 'diagnostics' for app in ('Dota2Helper', 'Dota2Economy'))
        for base in bases:
            directory = base / 'field-recording'
            if not directory.is_dir():
                continue
            paths = list(directory.glob('run_*/recorders.json'))
            if len(paths) > 10000:
                raise ValueError('handoff_registry_discovery_limit')
            for path in paths:
                resolved = path.resolve()
                if not resolved.is_relative_to(root):
                    raise ValueError('handoff_source_outside_root')
                if str(resolved).casefold() not in seen:
                    seen.add(str(resolved).casefold())
                    candidates.append((key, root, path))
    return sorted(candidates, key=lambda row: row[2].stat().st_mtime_ns, reverse=True)[:latest]


def sync(root, roots, *, latest=5, quiet_seconds=2):
    from .history import checked_output
    root = Path(root).resolve()
    destination = checked_output(root, root / 'diagnostics/helper-handoff')
    if any(destination.is_relative_to(Path(p).resolve()) for p in roots.values()):
        raise ValueError('handoff_output_overlaps_history')
    (destination / 'packages').mkdir(parents=True, exist_ok=True)
    lock = destination / 'sync.lock'
    # Explicit single writer. A crash lock requires inspection, never killing an unknown PID.
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    results = []
    try:
        os.close(fd)
        for key, source_root, registry in discover(roots, latest=latest):
            try:
                package = inspect_registry(key, source_root, registry, quiet_seconds=quiet_seconds)
                encoded = json.dumps(package, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
                package_id = hashlib.sha256(encoded).hexdigest()
                path = destination / 'packages' / (package_id + '.json')
                existed = path.exists()
                if existed:
                    if path.read_bytes() != encoded:
                        raise ValueError('handoff_existing_package_changed')
                else:
                    with path.open('xb') as stream:
                        stream.write(encoded)
                results.append({'package_id': package_id, 'state': package['state'],
                    'launcher_run_id': package['launcher_run_id'], 'matches': package['matches'],
                    'components': [{'kind': c['kind'], 'state': c['state'], 'warnings': c['warnings'], 'issues': c['issues']} for c in package['components']],
                    'already_indexed': existed})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                transient = str(exc) in ('handoff_source_not_quiet', 'handoff_source_changed_during_read', 'handoff_source_changed_after_read')
                results.append({'state': 'waiting' if transient else 'blocked', 'source': {'root_key': key, 'path': registry.relative_to(source_root).as_posix()},
                    'reason': str(exc) if isinstance(exc, ValueError) and str(exc).startswith('handoff_') else 'handoff_source_unavailable_or_invalid'})
        index = {'schema_version': 1, 'kind': 'helper_handoff_index', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
            'scope': 'latest_selected_launcher_runs_not_all_history', 'limit': latest, 'runs': results,
            'counts': dict(Counter(r['state'] for r in results)), 'payloads_copied': False, 'analysis_started': False}
        temporary = destination / ('.index-' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            temporary.replace(destination / 'index.json')
        finally:
            temporary.unlink(missing_ok=True)
        return index
    finally:
        lock.unlink(missing_ok=True)
