"""Conservative offline death evidence; never infer scoreboard deaths from totals.

The pinned decoder's False also means an absent protobuf field. Existing
archives may omit even that decoder value. State samples, nearby modifiers,
API totals and same-tick duplicates therefore never change classification here.
"""
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy


_S2_SOURCES = frozenset(('s2_direct', 's2_bulk'))
_COUNTS = (
    'raw_death_events', 'explicit_real_hero_death_events',
    'explicit_nonhero_or_illusion_death_events',
    'target_identity_unresolved_death_events',
    'positive_reincarnation_triggers', 'explicit_reincarnation_triggers',
    'decoder_positive_reincarnation_triggers',
    'explicit_real_hero_reincarnation_triggers',
    'scoreboard_compatible_candidates', 'unresolved_real_hero_death_events',
)


def _member(value, key):
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _decoder_value(entry):
    value = _member(entry, 'will_reincarnate')
    return value if type(value) is bool else None


def _empty_context(entry=None):
    return {'will_reincarnate': None, 'will_reincarnate_present': None,
            'will_reincarnate_decoder_value': _decoder_value(entry),
            'will_reincarnate_basis': 'not_observed_for_entry'}


@contextmanager
def capture_reincarnation_presence(processor):
    """Observe optional S2 bools without editing the pinned upstream decoder.

    The processor emits synchronously. The yielded ``read_context(entry)`` is
    for that callback; passing entry also prevents a nested S1 callback from
    borrowing its caller's S2 context. Calling without entry reads only the raw
    current protobuf context and returns no decoder value. A local stack supports
    recursive synchronous calls. It is not a cross-thread or async observer.

    Only this processor instance's process_s2_entry is wrapped. Bulk decoding
    calls that same instance method. The prior instance/class binding is restored
    on every exit, including exceptions and nested observers.
    """
    original = processor.process_s2_entry
    instance_dict = getattr(processor, '__dict__', {})
    had_instance_binding = 'process_s2_entry' in instance_dict
    original_binding = instance_dict.get('process_s2_entry')
    stack = []

    def wrapped(msg, *args, **kwargs):
        context = _empty_context()
        try:
            present = msg.HasField('will_reincarnate')
            if type(present) is bool:
                context['will_reincarnate_present'] = present
                if present:
                    value = msg.will_reincarnate
                    if type(value) is bool:
                        context['will_reincarnate'] = value
                        context['will_reincarnate_basis'] = (
                            'explicit_s2_protobuf_true' if value
                            else 'explicit_s2_protobuf_false')
                else:
                    context['will_reincarnate_basis'] = 'absent_s2_protobuf_field'
        except (AttributeError, TypeError, ValueError):
            # Unsupported synthetic/legacy messages cannot establish presence.
            context = _empty_context()
        stack.append(context)
        try:
            return original(msg, *args, **kwargs)
        finally:
            stack.pop()

    def read_context(entry=None):
        if not stack or (entry is not None
                         and str(_member(entry, 'source')) not in _S2_SOURCES):
            return _empty_context(entry)
        result = dict(stack[-1])
        result['will_reincarnate_decoder_value'] = _decoder_value(entry)
        return result

    processor.process_s2_entry = wrapped
    try:
        yield read_context
    finally:
        if had_instance_binding:
            processor.process_s2_entry = original_binding
        else:
            del processor.process_s2_entry


def _field(event, key):
    # Unified timelines keep original source flags separately from nullable
    # interpreted details. An explicitly stored None must not fall through.
    for container in (event.get('source_flags'), event, event.get('details')):
        if isinstance(container, Mapping) and key in container:
            return container[key]
    return None


def _explicit_target_flag(event, key):
    value = _field(event, key)
    if _field(event, key + '_present') is True and type(value) is bool:
        return value
    return None


def _reincarnation_status(event):
    raw = _field(event, 'will_reincarnate')
    present = _field(event, 'will_reincarnate_present')
    decoder = _field(event, 'will_reincarnate_decoder_value')
    # A historical/custom export may keep only the named decoder bool.
    if decoder is None and type(raw) is bool and present is not True:
        decoder = raw
    if ((present is False and raw is not None)
            or (present is True and type(raw) is bool
                and type(decoder) is bool and raw != decoder)):
        return 'conflicting_unresolved'
    if present is True and raw is True:
        return 'explicit_true'
    if present is True and raw is False:
        return 'explicit_false_unresolved'
    if present is False:
        return 'absent_unresolved'
    if decoder is True:
        # Positive decoder evidence is kept distinct from observed HasField.
        return 'decoder_true_positive'
    if decoder is False:
        return 'false_or_absent_unresolved'
    return 'not_exported'


def _classify_death_event(event, *, input_index, keep_source_event):
    if not isinstance(event, Mapping):
        raise TypeError('death_event_must_be_mapping')
    kind = event.get('event_type', event.get('kind'))
    if kind != 'DEATH':
        return None
    hero = _explicit_target_flag(event, 'target_is_hero')
    illusion = _explicit_target_flag(event, 'target_is_illusion')
    if hero is True and illusion is False:
        target_status = 'explicit_real_hero'
    elif hero is False or illusion is True:
        target_status = 'explicit_nonhero_or_illusion'
    else:
        target_status = 'unresolved'
    status = _reincarnation_status(event)
    positive = status in ('explicit_true', 'decoder_true_positive')
    source_ref = event.get('source_ref')
    if not isinstance(source_ref, str) or not source_ref:
        source_ref = f'input:{input_index}'
    target = _field(event, 'target')
    result = {
        'input_index': input_index, 'source_ref': source_ref,
        'match_id': event.get('match_id'), 'tick': event.get('tick'),
        'tick_epoch': event.get('tick_epoch'),
        'game_time_s': event.get('game_time_s'),
        'target': target if isinstance(target, str) else None,
        'target_identity': target_status,
        'reincarnation_status': status,
        'positive_reincarnation_trigger': positive,
        'scoreboard_compatible_candidate': target_status == 'explicit_real_hero' and not positive,
        'final_death_certified': False,
    }
    if keep_source_event:
        result['source_event'] = deepcopy(dict(event))
    return result


def classify_death_event(event, *, input_index=1):
    """Return a lossless referenced classification, or None for a non-DEATH.

    Accepts an archive combat_event or unified timeline row. Target presence
    flags are required; a canonical hero name alone never proves identity.
    Neither explicit False nor a missing reincarnation flag proves a final death.
    """
    return _classify_death_event(event, input_index=input_index, keep_source_event=True)


def _new_bucket(max_refs_per_bucket=None):
    bucket = {'counts': dict.fromkeys(_COUNTS, 0),
              'refs': {key: [] for key in _COUNTS}}
    if max_refs_per_bucket is not None:
        bucket['refs_omitted'] = dict.fromkeys(_COUNTS, 0)
    return bucket


def _add(bucket, record, max_refs_per_bucket=None):
    names = ['raw_death_events']
    target = record['target_identity']
    real = target == 'explicit_real_hero'
    names.append({'explicit_real_hero': 'explicit_real_hero_death_events',
                  'explicit_nonhero_or_illusion': 'explicit_nonhero_or_illusion_death_events',
                  'unresolved': 'target_identity_unresolved_death_events'}[target])
    if record['positive_reincarnation_trigger']:
        names.extend(('positive_reincarnation_triggers',
                      'explicit_reincarnation_triggers'
                      if record['reincarnation_status'] == 'explicit_true'
                      else 'decoder_positive_reincarnation_triggers'))
        if real:
            names.append('explicit_real_hero_reincarnation_triggers')
    elif real:
        names.extend(('scoreboard_compatible_candidates',
                      'unresolved_real_hero_death_events'))
    for name in names:
        bucket['counts'][name] += 1
        if max_refs_per_bucket is None or len(bucket['refs'][name]) < max_refs_per_bucket:
            bucket['refs'][name].append(record['source_ref'])
        else:
            bucket['refs_omitted'][name] += 1


def summarize_death_semantics(events, *, max_events_kept=None,
                              max_refs_per_bucket=None, max_heroes=None):
    """Count all DEATHs, optionally bounding retained details and references.

    Candidates mean explicit real heroes with no positive reincarnation flag.
    They remain unresolved and are never returned as certified final deaths or
    a normalized scoreboard count. This function reads no files or external data.

    Defaults retain every event and reference, including same-tick duplicates.
    Bounded mode never truncates counts: events_omitted counts unretained detail
    records, and each bucket's refs_omitted counts unretained references per
    category. Neither describes missing source events. New hero categories past
    max_heroes raise ValueError rather than silently dropping a category.
    """
    limits = {'max_events_kept': max_events_kept,
              'max_refs_per_bucket': max_refs_per_bucket, 'max_heroes': max_heroes}
    for name, limit in limits.items():
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError(name + '_must_be_nonnegative_integer_or_none')
    summary = _new_bucket(max_refs_per_bucket)
    summary.update({'schema_version': 1, 'kind': 'conservative_death_semantics',
                    'by_hero': {}, 'events': [],
                    'normalization_status': 'no_death_events',
                    'candidate_basis': 'explicit_real_hero_without_positive_reincarnation_evidence; '
                                       'false_and_absence_remain_unresolved',
                    'deduplication': 'none; source_order_and_duplicates_preserved'})
    if any(limit is not None for limit in limits.values()):
        summary['retention'] = {**limits, 'counts_cover_all_death_events': True,
                                'omission_basis': 'unretained_details_or_references; '
                                                  'not_missing_source_events'}
    if max_events_kept is not None:
        summary['events_omitted'] = 0
    for index, event in enumerate(events, 1):
        keep = max_events_kept is None or len(summary['events']) < max_events_kept
        record = _classify_death_event(event, input_index=index, keep_source_event=keep)
        if record is None:
            continue
        if keep:
            summary['events'].append(record)
        else:
            summary['events_omitted'] += 1
        _add(summary, record, max_refs_per_bucket)
        target = record['target']
        if target and target.startswith('npc_dota_hero_'):
            bucket = summary['by_hero'].get(target)
            if bucket is None:
                if max_heroes is not None and len(summary['by_hero']) >= max_heroes:
                    raise ValueError('death_semantics_hero_limit')
                bucket = summary['by_hero'][target] = _new_bucket(max_refs_per_bucket)
            _add(bucket, record, max_refs_per_bucket)
    if summary['counts']['raw_death_events']:
        summary['normalization_status'] = 'unresolved_no_final_death_count_certified'
    return summary
