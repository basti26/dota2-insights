"""Pure, offline annotations for an existing replay archive's recorded clocks.

No parser, file, network, or game API is imported here. Labels are a derivative
view, never an edit to an event or proof of exact game state. File readers must
separately verify the manifest's file hashes and enforce input/output budgets.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

from . import replay_phase_evidence as phase


SCHEMA_VERSION = 1
# An explicit domain guard, not a claim about how long a match should last.
MAX_ABS_GAME_TIME_S = 7 * 24 * 60 * 60
MAX_ABS_RAW_TIMESTAMP_S = 2 ** 53
MAX_TICK = 2 ** 63 - 1
MAX_MATCH_ID = 2 ** 64 - 1
PERIODS = ('pregame', 'in_match', 'end_boundary_uncertain',
           'post_match_tail', 'end_unknown', 'clock_unknown')
_CLOCK_FIELDS = ('tick', 'net_tick', 'game_time_s', 'event_tick', 'packet_tick',
                 'event_game_time_s', 'timestamp_raw')
_TICK_FIELDS = ('tick', 'net_tick', 'event_tick', 'packet_tick')
_LIMITATIONS = (
    'Labels use recorded game_time_s without sorting, interpolation, or rewriting.',
    'Independent integer duration is an external final summary; its closed '
    '[duration, duration+1] boundary band does not prove exact POST_GAME state.',
    'Native phase evidence brackets the last observed in-progress packet and '
    'first observed post-game packet; it is not an integer duration or exact real end.',
    'Independent duration does not certify the archive clock globally; '
    'source version may be unavailable.',
    'Without bound end evidence, nonnegative clocks remain end_unknown; '
    'last event/sample time and manifest phase markers are never used as duration.',
    'A period label neither proves event completeness nor backfills missing state.',
)


def _match_id(value: Any) -> str:
    if type(value) is int:
        if 0 < value <= MAX_MATCH_ID:
            return str(value)
    elif type(value) is str and re.fullmatch(r'[1-9][0-9]{0,19}', value):
        if int(value) <= MAX_MATCH_ID:
            return value
    raise ValueError('period_match_id_invalid')


def _sha256(value: Any, name: str) -> str:
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('period_' + name + '_sha256_invalid')
    return value


def _text(value: Any, name: str, limit: int = 1024) -> str:
    if (type(value) is not str or not 0 < len(value) <= limit
            or any(ord(char) < 32 for char in value)):
        raise ValueError('period_' + name + '_invalid')
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(dict(value), sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise ValueError('period_context_not_finite_json') from exc
    return hashlib.sha256(raw).hexdigest()


def _archive_context(manifest: Mapping[str, Any]) -> tuple[str, str | None, str, str]:
    if not isinstance(manifest, Mapping) or manifest.get('kind') != 'offline_replay_archive':
        raise ValueError('period_archive_kind_required')
    match_id = _match_id(manifest.get('match_id'))
    source_sha256 = _sha256(manifest.get('source_sha256'), 'source_replay')
    session_id = (_text(manifest['session_id'], 'archive_session', 256)
                  if 'session_id' in manifest else None)
    return match_id, session_id, source_sha256, _canonical_sha256(manifest)


def _duration(value: Any) -> int:
    if type(value) is not int or not 0 < value < MAX_ABS_GAME_TIME_S:
        raise ValueError('period_duration_must_be_bounded_positive_integer')
    return value


def _game_clock(value: Any, name: str, limit: int = MAX_ABS_GAME_TIME_S) -> int | float | None:
    if value is None:
        return None
    if (type(value) not in (int, float) or not -limit <= value <= limit
            or not math.isfinite(value)):
        raise ValueError('period_' + name + '_must_be_bounded_finite_number')
    return value


@dataclass(frozen=True)
class DurationEvidence:
    """A validated receipt binding, scoped to one complete manifest snapshot.

    The hash binds local evidence bytes; it does not authenticate the provider
    or establish a global mapping between API duration and the replay clock.
    Obtain this object with ``bind_duration_evidence`` rather than constructing it.
    """

    match_id: str
    archive_session_id: str | None
    source_replay_sha256: str
    archive_manifest_sha256: str
    duration_s: int | None
    status: str
    reason: str
    facts_sha256: str | None = None
    request_sha256: str | None = None
    raw_response_sha256: str | None = None
    facts_ref: str | None = None
    request_ref: str | None = None
    source_url: str | None = None
    source_version: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, 'source_kind': 'external_opendota_final_summary',
                'global_clock_certified': False}


@dataclass(frozen=True)
class ObservedEndEvidence:
    """A native phase-observation interval bound to a complete manifest snapshot."""

    match_id: str
    archive_session_id: str | None
    source_replay_sha256: str
    archive_manifest_sha256: str
    status: str
    reason: str
    phase_evidence_sha256: str | None = None
    phase_evidence_ref: str = 'manifest.json:phase_evidence'
    phase_status: str | None = None
    phase_reasons: tuple[str, ...] = ()
    end_window_game_time_s: tuple[int | float, int | float] | None = None
    end_window_tick: tuple[int, int] | None = None
    end_window_completed_packet: tuple[int, int] | None = None

    @property
    def duration_s(self) -> None:
        return None

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, 'duration_s': None,
                'source_kind': 'native_game_rules_phase_observation',
                'global_clock_certified': False, 'exact_end_verified': False}


EndEvidence = DurationEvidence | ObservedEndEvidence


def _phase_integer(value: Any, name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError('period_phase_' + name + '_invalid')
    return value


def _phase_observation(value: Any, packets: int) -> Mapping[str, Any]:
    required = {'completed_packet', 'tick', 'game_time_s', 'state_raw', 'source_ref'}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError('period_phase_observation_schema_invalid')
    sequence = _phase_integer(value['completed_packet'], 'packet', 1, packets)
    _phase_integer(value['tick'], 'tick', -MAX_TICK, MAX_TICK)
    _game_clock(value['game_time_s'], 'phase_game_time_s')
    if value['state_raw'] is not None:
        _phase_integer(value['state_raw'], 'state_raw', 0, phase.MAX_STATE)
    if value['source_ref'] != f'completed_packet:{sequence}':
        raise ValueError('period_phase_observation_ref_mismatch')
    return value


def _validate_phase_report(report: Any) -> Mapping[str, Any]:
    if not isinstance(report, Mapping):
        raise ValueError('period_phase_report_object_required')
    constants = {'kind': phase.KIND, 'basis': phase.BASIS, 'field': phase.FIELD,
                 'clock_field': 'game_time_s', 'sampling': phase.SAMPLING,
                 'ordering_scope': phase.ORDERING_SCOPE}
    if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
            or any(report.get(key) != value for key, value in constants.items())):
        raise ValueError('period_phase_report_schema_or_basis_invalid')
    enums = report.get('state_enums')
    if (not isinstance(enums, Mapping) or set(enums) != {'in_progress', 'post_game'}
            or type(enums['in_progress']) is not int or enums['in_progress'] != phase.IN_PROGRESS
            or type(enums['post_game']) is not int or enums['post_game'] != phase.POST_GAME):
        raise ValueError('period_phase_supported_enum_binding_required')
    packets = _phase_integer(report.get('completed_packets'), 'packets', 0, MAX_TICK)
    budget = _phase_integer(report.get('max_changes'), 'change_budget', 1, phase.MAX_CHANGE_BUDGET)
    for key in ('changes_truncated', 'observation_complete', 'boundary_clock_complete'):
        if type(report.get(key)) is not bool:
            raise ValueError('period_phase_' + key + '_bool_required')
    for key in ('clock_regressions', 'tick_regressions', 'state_reversals',
                'unknown_state_packets', 'missing_clock_packets'):
        _phase_integer(report.get(key), key, 0, packets)
    stop_reason = report.get('stop_reason')
    if stop_reason is not None:
        _text(stop_reason, 'phase_stop_reason', 256)
    changes = report.get('changes')
    if (type(changes) is not list or len(changes) > budget
            or bool(changes) != bool(packets)
            or report['changes_truncated'] and len(changes) != budget):
        raise ValueError('period_phase_change_budget_or_count_invalid')
    previous = None
    for item in changes:
        current = _phase_observation(item, packets)
        if previous is None:
            if current['completed_packet'] != 1:
                raise ValueError('period_phase_first_change_packet_invalid')
        elif (current['completed_packet'] <= previous['completed_packet']
                or current['state_raw'] == previous['state_raw']):
            raise ValueError('period_phase_change_order_invalid')
        previous = current
    lower, upper = report.get('last_in_progress'), report.get('first_post_game')
    for endpoint, state in ((lower, phase.IN_PROGRESS), (upper, phase.POST_GAME)):
        if endpoint is not None and _phase_observation(endpoint, packets)['state_raw'] != state:
            raise ValueError('period_phase_endpoint_state_mismatch')
    observations = {item['completed_packet']: item for item in changes}
    for endpoint in (lower, upper):
        if endpoint is not None:
            sequence = endpoint['completed_packet']
            if sequence in observations and observations[sequence] != endpoint:
                raise ValueError('period_phase_same_packet_observation_mismatch')
            observations[sequence] = endpoint
    previous_tick = previous_time = None
    ordered_phase_seen = False
    for sequence in sorted(observations):
        item = observations[sequence]
        if not ordered_phase_seen and item['state_raw'] in (phase.IN_PROGRESS, phase.POST_GAME):
            ordered_phase_seen = True
            previous_tick = previous_time = None
        if (ordered_phase_seen and previous_tick is not None and not report['tick_regressions']
                and item['tick'] < previous_tick):
            raise ValueError('period_phase_unreported_tick_regression')
        previous_tick = item['tick']
        if item['game_time_s'] is not None:
            if (ordered_phase_seen and previous_time is not None and not report['clock_regressions']
                    and item['game_time_s'] < previous_time):
                raise ValueError('period_phase_unreported_clock_regression')
            previous_time = item['game_time_s']
    if (report['missing_clock_packets'] < sum(item['game_time_s'] is None
            for item in observations.values()) or report['unknown_state_packets']
            < sum(item['state_raw'] is None for item in observations.values())):
        raise ValueError('period_phase_missing_value_counts_inconsistent')
    if lower is not None and upper is not None:
        if lower['completed_packet'] >= upper['completed_packet']:
            raise ValueError('period_phase_endpoint_packet_order_invalid')
        if (not report['tick_regressions'] and lower['tick'] > upper['tick']
                or not report['clock_regressions']
                and lower['game_time_s'] is not None and upper['game_time_s'] is not None
                and lower['game_time_s'] > upper['game_time_s']):
            raise ValueError('period_phase_endpoint_clock_order_invalid')
        if report['boundary_clock_complete'] and any(
                lower['completed_packet'] <= sequence <= upper['completed_packet']
                and item['game_time_s'] is None for sequence, item in observations.items()):
            raise ValueError('period_phase_unreported_boundary_clock_gap')
    if not report['changes_truncated']:
        posts = [item for item in changes if item['state_raw'] == phase.POST_GAME]
        if (upper is None) != (not posts) or posts and upper != posts[0]:
            raise ValueError('period_phase_first_post_change_mismatch')
        before_post = [item for item in changes if upper is None
                       or item['completed_packet'] < upper['completed_packet']]
        actives = [item for item in before_post if item['state_raw'] == phase.IN_PROGRESS]
        if (lower is None) != (not actives):
            raise ValueError('period_phase_prior_active_change_mismatch')
        if lower is not None:
            active = actives[-1]
            next_changes = [item for item in changes
                            if item['completed_packet'] > active['completed_packet']]
            expected_lower_packet = (next_changes[0]['completed_packet'] - 1
                                     if next_changes else packets)
            if lower['completed_packet'] != expected_lower_packet:
                raise ValueError('period_phase_last_active_packet_mismatch')
        saw_active = saw_post = saw_disconnect = False
        obvious_reversals = 0
        for item in changes:
            state = item['state_raw']
            if state is not None and (
                    saw_post and (saw_disconnect and state != phase.DISCONNECT
                                  or not saw_disconnect and state not in (phase.POST_GAME, phase.DISCONNECT))
                    or saw_active and not saw_post and state not in (phase.IN_PROGRESS, phase.POST_GAME)):
                obvious_reversals += 1
            saw_disconnect = saw_disconnect or saw_post and state == phase.DISCONNECT
            saw_active = saw_active or state == phase.IN_PROGRESS
            saw_post = saw_post or state == phase.POST_GAME
        if report['state_reversals'] < obvious_reversals:
            raise ValueError('period_phase_unreported_state_reversal')
    invalid = []
    if not report['observation_complete']:
        invalid.append('observation_incomplete')
    if stop_reason not in phase.COMPLETE_STOP_REASONS:
        invalid.append('stop_reason_not_complete')
    if report['changes_truncated']:
        invalid.append('phase_change_budget_exceeded')
    for field, reason in (('tick_regressions', 'tick_regression'),
                          ('clock_regressions', 'clock_regression'),
                          ('state_reversals', 'phase_state_reversal_or_unexpected_transition')):
        if report[field]:
            invalid.append(reason)
    reasons = invalid[:]
    if upper is None:
        reasons.append('first_post_game_not_observed')
    elif lower is None:
        reasons.append('prior_in_progress_not_observed')
    if lower is not None and upper is not None and (not report['boundary_clock_complete']
            or lower['game_time_s'] is None or upper['game_time_s'] is None):
        reasons.append('boundary_clock_missing')
    expected_status = 'invalid' if invalid else 'unknown' if reasons else 'observed_interval'
    if report.get('status') != expected_status or report.get('reasons') != reasons:
        raise ValueError('period_phase_report_status_inconsistent')
    if expected_status != 'observed_interval':
        if report.get('end_window') is not None:
            raise ValueError('period_phase_unavailable_window_must_be_null')
    else:
        expected = {key: [lower[key], upper[key]]
                    for key in ('game_time_s', 'tick', 'completed_packet')}
        expected.update(interval_semantics=phase.INTERVAL_SEMANTICS, exact_end_verified=False)
        window = report.get('end_window')
        if not isinstance(window, Mapping) or set(window) != set(expected):
            raise ValueError('period_phase_end_window_schema_invalid')
        for key in ('tick', 'completed_packet'):
            if type(window[key]) is not list or len(window[key]) != 2:
                raise ValueError('period_phase_end_window_pair_invalid')
            for value in window[key]:
                _phase_integer(value, key, -MAX_TICK if key == 'tick' else 1, MAX_TICK)
        if type(window['game_time_s']) is not list or len(window['game_time_s']) != 2:
            raise ValueError('period_phase_end_window_pair_invalid')
        for value in window['game_time_s']:
            if _game_clock(value, 'phase_end_window_clock') is None:
                raise ValueError('period_phase_end_window_clock_required')
        if window['exact_end_verified'] is not False or window != expected:
            raise ValueError('period_phase_end_window_endpoint_mismatch')
    return report


def bind_observed_end_evidence(manifest: Mapping[str, Any]) -> ObservedEndEvidence:
    """Bind a strictly validated native report; unavailable reports stay unknown."""
    mid, session_id, replay_sha, manifest_sha = _archive_context(manifest)
    base = dict(match_id=mid, archive_session_id=session_id,
                source_replay_sha256=replay_sha, archive_manifest_sha256=manifest_sha,
                status='unavailable', reason='native_phase_evidence_missing')
    if manifest.get('phase_evidence') is None:
        return ObservedEndEvidence(**base)
    report = _validate_phase_report(manifest['phase_evidence'])
    base.update(phase_evidence_sha256=_canonical_sha256(report),
                phase_status=report['status'], phase_reasons=tuple(report['reasons']))
    if report['status'] != 'observed_interval':
        base['reason'] = 'native_phase_evidence_' + report['status']
        return ObservedEndEvidence(**base)
    window = report['end_window']
    base.update(status='bound', reason='observed_in_progress_to_first_post_interval',
                end_window_game_time_s=tuple(window['game_time_s']),
                end_window_tick=tuple(window['tick']),
                end_window_completed_packet=tuple(window['completed_packet']))
    return ObservedEndEvidence(**base)


def bind_available_end_evidence(
    manifest: Mapping[str, Any], facts_raw: bytes | None = None,
    request: Mapping[str, Any] | None = None, *, facts_ref: str = 'facts.json',
    request_ref: str = 'request.json',
) -> EndEvidence:
    """Explicit API inputs take priority, including incomplete/rejected evidence.

    With neither API input supplied, only the versioned native phase report is
    considered. Arbitrary phase_marker or old match_phase_boundary fields are ignored.
    """
    _archive_context(manifest)
    if facts_raw is not None or request is not None:
        return bind_duration_evidence(manifest, facts_raw, request,
                                      facts_ref=facts_ref, request_ref=request_ref)
    if manifest.get('phase_evidence') is None:
        return bind_duration_evidence(manifest)
    return bind_observed_end_evidence(manifest)


def _json_object(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError('period_facts_duplicate_json_key')
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise ValueError('period_facts_nonfinite_json')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError, OverflowError, RecursionError) as exc:
        raise ValueError('period_facts_invalid_json') from exc
    if type(value) is not dict:
        raise ValueError('period_facts_object_required')
    # Also catches exponent overflow (e.g. JSON 1e999) outside the duration field.
    _canonical_sha256(value)
    return value


def bind_duration_evidence(
    manifest: Mapping[str, Any],
    facts_raw: bytes | None = None,
    request: Mapping[str, Any] | None = None,
    *,
    facts_ref: str = 'facts.json',
    request_ref: str = 'request.json',
) -> DurationEvidence:
    """Bind sanitized OpenDota facts to their successful GET receipt and archive.

    Missing facts, receipt, or duration yield an unavailable binding. Present
    but mismatched/malformed evidence raises ValueError. SHA256 uses the exact
    facts bytes, never reserialized JSON. The request hash is canonical JSON.
    No fetch is performed and no facts field is copied into replay events.
    """
    mid, session_id, replay_sha, manifest_sha = _archive_context(manifest)
    base: dict[str, Any] = dict(match_id=mid, archive_session_id=session_id,
        source_replay_sha256=replay_sha, archive_manifest_sha256=manifest_sha,
        duration_s=None, status='unavailable', reason='independent_duration_not_supplied')
    if facts_raw is not None and type(facts_raw) is not bytes:
        raise ValueError('period_facts_exact_bytes_required')
    if request is not None and not isinstance(request, Mapping):
        raise ValueError('period_source_receipt_object_required')
    if facts_raw is None and request is None:
        return DurationEvidence(**base)
    # A one-sided source cannot establish duration, but keep the supplied ref/hash.
    if facts_raw is not None:
        base.update(facts_ref=_text(facts_ref, 'facts_ref'),
                    facts_sha256=hashlib.sha256(facts_raw).hexdigest())
    if request is not None:
        base.update(request_ref=_text(request_ref, 'request_ref'),
                    request_sha256=_canonical_sha256(request))
    if request is not None and (_match_id(request.get('match_id')) != mid
            or request.get('method') != 'GET'
            or type(request.get('http_status')) is not int or request['http_status'] != 200
            or request.get('status') != 'downloaded_sanitized'
            or request.get('url') != f'https://api.opendota.com/api/matches/{mid}'):
        raise ValueError('period_source_receipt_mismatch')
    if request is not None and 'session_id' in request and (
            session_id is None or request['session_id'] != session_id):
        raise ValueError('period_source_receipt_session_mismatch')
    if request is not None:
        _sha256(request.get('facts_sha256'), 'facts')
        parsed_version = request.get('parsed_version')
        if parsed_version is not None and (type(parsed_version) is not int
                or not 0 <= parsed_version <= 2 ** 31 - 1):
            raise ValueError('period_source_version_invalid')
        base['source_url'] = request['url']
        if request.get('raw_response_sha256') is not None:
            base['raw_response_sha256'] = _sha256(request['raw_response_sha256'], 'raw_response')
    if facts_raw is None:
        base['reason'] = 'independent_facts_missing'
        return DurationEvidence(**base)
    if request is not None and request['facts_sha256'] != base['facts_sha256']:
        raise ValueError('period_facts_source_changed')
    facts = _json_object(facts_raw)
    if _match_id(facts.get('match_id')) != mid:
        raise ValueError('period_facts_match_mismatch')
    version = facts.get('version')
    if version is not None and (type(version) is not int or not 0 <= version <= 2 ** 31 - 1):
        raise ValueError('period_source_version_invalid')
    if request is not None and 'parsed_version' in request and request['parsed_version'] != version:
        raise ValueError('period_source_version_mismatch')
    base['source_version'] = version
    duration = _duration(facts['duration']) if facts.get('duration') is not None else None
    if request is None:
        base['reason'] = 'source_receipt_missing'
        return DurationEvidence(**base)
    if facts.get('duration') is None:
        base['reason'] = 'independent_duration_missing'
        return DurationEvidence(**base)
    base.update(duration_s=duration, status='bound',
                reason='same_match_integer_duration_bound_to_successful_get_receipt')
    return DurationEvidence(**base)


def _evidence_for(manifest: Mapping[str, Any], evidence: EndEvidence | None) -> EndEvidence:
    context = _archive_context(manifest)
    if evidence is None:
        return bind_available_end_evidence(manifest)
    if type(evidence) not in (DurationEvidence, ObservedEndEvidence):
        raise ValueError('period_bound_duration_evidence_required')
    if (evidence.match_id, evidence.archive_session_id, evidence.source_replay_sha256,
            evidence.archive_manifest_sha256) != context:
        raise ValueError('period_duration_archive_session_mismatch')
    if type(evidence) is ObservedEndEvidence:
        if evidence != bind_observed_end_evidence(manifest):
            raise ValueError('period_native_evidence_binding_changed')
        return evidence
    if evidence.status == 'bound':
        _duration(evidence.duration_s)
        _sha256(evidence.facts_sha256, 'facts')
        _sha256(evidence.request_sha256, 'request')
        if not evidence.facts_ref or not evidence.request_ref or not evidence.source_url:
            raise ValueError('period_duration_source_binding_required')
    elif evidence.status != 'unavailable' or evidence.duration_s is not None:
        raise ValueError('period_duration_evidence_invalid')
    return evidence


def _boundary_for(evidence: EndEvidence) -> tuple[int | float, int | float] | None:
    if type(evidence) is ObservedEndEvidence:
        return evidence.end_window_game_time_s
    if evidence.duration_s is not None:
        return evidence.duration_s, evidence.duration_s + 1
    return None


def _label_rows(rows: Iterable[Mapping[str, Any]], evidence: EndEvidence,
                source_name: str) -> Iterator[dict[str, Any]]:
    boundary = _boundary_for(evidence)
    for sequence, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or _match_id(row.get('match_id')) != evidence.match_id:
            raise ValueError('period_row_match_mismatch')
        if 'session_id' in row and (evidence.archive_session_id is None
                or row['session_id'] != evidence.archive_session_id):
            raise ValueError('period_row_session_mismatch')
        for field in _TICK_FIELDS:
            value = row.get(field)
            if value is not None and (type(value) is not int or not -MAX_TICK <= value <= MAX_TICK):
                raise ValueError('period_' + field + '_must_be_bounded_integer')
        clock = _game_clock(row.get('game_time_s'), 'game_time_s')
        _game_clock(row.get('event_game_time_s'), 'event_game_time_s')
        _game_clock(row.get('timestamp_raw'), 'timestamp_raw', MAX_ABS_RAW_TIMESTAMP_S)
        if clock is None:
            period = 'clock_unknown'
        elif clock < 0:
            period = 'pregame'
        elif boundary is None:
            period = 'end_unknown'
        elif clock < boundary[0]:
            period = 'in_match'
        elif clock <= boundary[1]:
            period = 'end_boundary_uncertain'
        else:
            period = 'post_match_tail'
        yield {'schema_version': SCHEMA_VERSION, 'sequence': sequence,
               'match_id': evidence.match_id, 'source_ref': f'{source_name}:{sequence}',
               'period': period,
               'source_clocks': {field: row[field] for field in _CLOCK_FIELDS if field in row}}


def iter_period_labels(
    rows: Iterable[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    evidence: EndEvidence | None = None,
    *,
    source_name: str = 'events.jsonl',
) -> Iterator[dict[str, Any]]:
    """Yield separate labels in input order with 1-based original row references.

    Validation of the archive/evidence happens immediately; row validation is
    incremental. A downstream writer must mark any partial failed output invalid.
    Missing and explicit-null clocks retain their distinct source representation.
    """
    bound = _evidence_for(manifest, evidence)
    return _label_rows(rows, bound, _text(source_name, 'source_name', 256))


def label_replay_periods(
    rows: Iterable[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    evidence: EndEvidence | None = None,
    *,
    source_name: str = 'events.jsonl',
) -> dict[str, Any]:
    """Collect labels plus an observation summary without modifying source rows.

    Accepts a one-pass iterable. Use iter_period_labels when the file layer needs
    streaming rather than retaining labels. Callers enforce row/output budgets.
    """
    bound = _evidence_for(manifest, evidence)
    labels = list(_label_rows(rows, bound, _text(source_name, 'source_name', 256)))
    counts = {period: 0 for period in PERIODS}
    minimum = maximum = previous = None
    regressions = 0
    for label in labels:
        counts[label['period']] += 1
        clock = label['source_clocks'].get('game_time_s')
        if clock is not None:
            minimum = clock if minimum is None else min(minimum, clock)
            maximum = clock if maximum is None else max(maximum, clock)
            if previous is not None and clock < previous:
                regressions += 1
        previous = clock
    boundary = _boundary_for(bound)
    return {'labels': labels, 'summary': {
        'schema_version': SCHEMA_VERSION, 'kind': 'offline_replay_period_annotations',
        'match_id': bound.match_id, 'source_name': source_name, 'rows': len(labels),
        'counts': counts, 'duration_s': bound.duration_s,
        'end_boundary_interval_s': list(boundary) if boundary is not None else None,
        'duration_evidence': bound.as_dict() if type(bound) is DurationEvidence else None,
        'end_evidence': bound.as_dict(),
        'min_observed_game_time_s': minimum, 'max_observed_game_time_s': maximum,
        'clock_order_regressions': regressions,
        'clock_order': 'source_order_not_sorted_or_interpolated',
        'exact_game_state_verified': False, 'limitations': list(_LIMITATIONS),
    }}
