"""Recorded callback lineage only; never match audio by wall-clock proximity."""
from collections import Counter
from contextlib import ExitStack
import math
import re

from .helper_handoff import utc
from .match_journal_index import resolve_reference as resolve_gsi
from .match_journals import open_rows as open_gsi

IDENTITY=('match_id','game_session_id','record_session_id')
ABILITY_FIELDS=('level','cooldown','charges','can_cast','ability_active','ultimate')


def _number(value):
    return type(value) in (int,float) and math.isfinite(value)


def _source_id(value):
    return isinstance(value,str) and re.fullmatch(r'gsi_[a-f0-9]{32}',value) is not None


def _ability_comparison(audio, native):
    """Compare only shared reported fields, never turn missing data into zero."""
    result={'status':'not_comparable','compared_fields':0,'missing_fields':0,'mismatch_examples':[]}
    if not isinstance(audio,list) or not isinstance(native,list) or len(audio)>32 or len(native)>32:
        return result
    values={}
    for row in native:
        if not isinstance(row,dict) or type(row.get('slot')) is not int or not isinstance(row.get('name'),str):
            continue
        key=(row['slot'],row['name'])
        if key in values:
            result['status']='ambiguous_native_ability_slot';return result
        values[key]=row
    mismatches=0
    for row in audio:
        if (not isinstance(row,dict) or not isinstance(row.get('slot'),str) or not isinstance(row.get('name'),str)
                or not re.fullmatch(r'ability(?:[0-9]|[12][0-9]|3[01])',row['slot'])):
            continue
        slot=int(row['slot'][7:]);other=values.get((slot,row.get('name')), {})
        for field in ABILITY_FIELDS:
            a,b=row.get(field),other.get(field)
            reported=(type(a) is bool and type(b) is bool if field in ABILITY_FIELDS[3:]
                      else _number(a) and _number(b) and a>=0 and b>=0)
            if not reported:
                result['missing_fields']+=1;continue
            result['compared_fields']+=1
            if a!=b:
                mismatches+=1
                if len(result['mismatch_examples'])<8:
                    result['mismatch_examples'].append({'slot':slot,'name':row.get('name'),'field':field,
                                                        'audio_reference_value':a,'native_gsi_value':b})
    result['status']='disagrees' if mismatches else 'equal_on_comparable_fields' if result['compared_fields'] else 'not_comparable'
    result['mismatched_fields']=mismatches
    return result


def align_package(package, native_entries, roots, *, native_index_state):
    """Caller owns open_package; its exit must verify audio before publication."""
    if (package.get('reference_semantics_verified') is not True or not isinstance(native_entries,list)
            or len(native_entries)>128 or any(not isinstance(e,dict) for e in native_entries)):
        raise ValueError('audio_alignment_requires_semantic_review_and_bounded_native_index')
    doc=package['manifest'];scope=tuple(doc[k] for k in IDENTITY)
    items,wanted,found=[],set(),{}
    for line,row in enumerate(package['references'],1):
        context=row['context'];source=context.get('recorded_source_context')
        item={'reference_line':line,'take_id':row.get('take_id'),'audio_offset_s':row.get('audio_offset_s'),
            'source_record_status':'unknown','reason':'source_id_not_recorded','context':context}
        if source is not None and source['match_id'] is None:
            item['reason']='public_match_id_unknown'
        elif source is not None and _source_id(source.get('gsi_source_id')):
            wanted.add(scope+(source['gsi_source_id'],))
            item['reason']=('native_index_not_available' if native_index_state=='missing_not_treated_as_zero' else
                            'source_record_not_received_or_not_in_selected_closed_packages')
        items.append(item)
    sources,opened,rows_seen=[],set(),0
    with ExitStack() as stack:
        for entry in native_entries:
            if not wanted or entry.get('state')!='ready_with_gaps' or tuple(entry.get(k) for k in IDENTITY)!=scope:
                continue
            if not isinstance(entry.get('package_id'),str) or not re.fullmatch(r'[a-f0-9]{64}',entry['package_id']):
                raise ValueError('audio_alignment_native_package_id_invalid')
            if entry['package_id'] in opened:continue
            manifest,records=stack.enter_context(open_gsi(resolve_gsi(entry,roots),expected_package_id=entry['package_id']))
            if tuple(manifest[k] for k in IDENTITY)!=scope:
                raise ValueError('audio_alignment_native_index_identity_conflict')
            opened.add(entry['package_id']);sources.append(entry['package_id'])
            for line,row in enumerate(records,1):
                rows_seen+=1
                if rows_seen>100000:raise ValueError('audio_alignment_native_row_limit')
                token=row.get('gsi_source_id')
                if not _source_id(token) or scope+(token,) not in wanted:continue
                key=scope+(token,)
                if key in found:
                    found[key]['count']+=1;continue
                heroes=set()
                for value in (row.get('self_economy'),row.get('self_status')):
                    if isinstance(value,dict) and isinstance(value.get('hero_id'),str):heroes.add(value['hero_id'])
                probe=row.get('gsi_probe')
                abilities=(probe.get('own_abilities') if isinstance(probe,dict)
                    and type(probe.get('schema_version')) is int and probe['schema_version']==1 else None)
                game_map=row.get('map_context')
                found[key]={'count':1,'package_id':entry['package_id'],'line':line,'sequence':row['sequence'],
                    'provider_at_utc':row.get('provider_at_utc'),'clock_time_s':row.get('clock_time_s'),
                    'game_state':game_map.get('game_state') if isinstance(game_map,dict) else None,
                    'heroes':heroes,'abilities':abilities}
        for item in items:
            context=item['context'];source=context.get('recorded_source_context')
            if source is None or source['match_id'] is None or not _source_id(source.get('gsi_source_id')):continue
            match=found.get(scope+(source['gsi_source_id'],))
            if match is None:continue
            if match['count']!=1:
                item.update(reason='source_id_ambiguous',matching_record_count=match['count']);continue
            try:same_time=utc(source['provider_at_utc'])==utc(match['provider_at_utc'])
            except (ValueError,TypeError):
                item['reason']='source_id_content_not_comparable';continue
            hero=context.get('hero_id')
            if (not _number(context.get('clock_time_s')) or not _number(match['clock_time_s'])
                    or not isinstance(match['game_state'],str)):
                item['reason']='source_id_content_not_comparable';continue
            if (not same_time or context['clock_time_s']!=match['clock_time_s'] or context.get('game_state')!=match['game_state']
                    or len(match['heroes'])>1 or hero is not None and match['heroes'] and hero not in match['heroes']):
                item['reason']='source_id_content_disagrees';continue
            comparison=_ability_comparison(context.get('abilities'),match['abilities'])
            item['ability_comparison']=comparison
            if comparison['status']=='disagrees':
                item['reason']='source_id_ability_values_disagree';continue
            item.update(source_record_status='exact_recorded_source_id',reason=None,
                native_source={k:match[k] for k in ('package_id','line','sequence')})
    for item in items:
        context=item.pop('context')
        item['gsi_source_id']=(context.get('recorded_source_context') or {}).get('gsi_source_id')
    return {'schema_version':1,'kind':'audio_gsi_source_alignment','audio_package_id':package['package_id'],
        'native_package_ids':sorted(set(sources)),'reference_rows':len(items),'items':items,
        'native_rows_scanned':rows_seen,'native_source_index_state':native_index_state,
        'native_sources_verified_before_and_after':True if sources else None,
        'basis':'exact_public_match_both_runtime_sessions_and_callback_source_id_not_timestamp_join',
        'skill_audibility_verified':False,'exact_audio_sample_time_verified':False,'cast_or_CD_boundary_verified':False}


def prepare(entry, native_entries, roots, *, native_index_state='read_and_sources_revalidated'):
    from .audio_match_handoff import open_package
    from .audio_match_index import resolve_reference
    if entry.get('state')!='ready_with_gaps':raise ValueError('audio_alignment_package_not_ready')
    with open_package(resolve_reference(entry,roots),expected_package_id=entry['package_id']) as package:
        if any(package['manifest'][key]!=entry.get(key) for key in (*IDENTITY,'audio_capture_run_id')):
            raise ValueError('audio_alignment_index_identity_conflict')
        result=align_package(package,native_entries,roots,native_index_state=native_index_state)
    result['audio_source_verified_before_and_after']=True
    return result


def summarize(report):
    items=report['items'];matched=sum(r['source_record_status']=='exact_recorded_source_id' for r in items)
    return {k:report[k] for k in ('audio_package_id','native_package_ids','reference_rows',
        'native_rows_scanned','native_source_index_state','native_sources_verified_before_and_after','basis',
        'skill_audibility_verified','exact_audio_sample_time_verified','cast_or_CD_boundary_verified')} | {
        'exact_source_record_rows':matched,'all_reference_sources_matched':bool(items) and matched==len(items),
        'unmatched_reasons':dict(Counter(r['reason'] for r in items if r['reason'] is not None)),
        'ability_fields_compared':sum(r.get('ability_comparison',{}).get('compared_fields',0) for r in items),
        'ability_comparison_statuses':dict(Counter(r.get('ability_comparison',{}).get('status','not_checked') for r in items)),
        'meaning':'recorded_callback_lineage_and_shared_fields_not_sound_or_cooldown_accuracy'}
