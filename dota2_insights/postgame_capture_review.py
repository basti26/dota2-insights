"""Review a frozen real context capture against its same-replay ward baseline."""
from __future__ import annotations

import base64
from collections import Counter,defaultdict
import json
import math
from pathlib import Path

from .postgame_store import bounded,sha
from .replay_postgame_context import METRICS,validate_context


def clock_time(clock,tick):
    if type(tick) is not int:return None
    def paused(at):
        total=0
        for p in clock.get('pauses',[]):
            if at<=p['start_tick']:break
            total+=min(at,p['end_tick'] if p['end_tick'] is not None else at)-p['start_tick']
        return total
    anchor=clock.get('game_start_time_s')
    if anchor is not None:base=anchor*30-clock.get('net_tick_offset',0)
    elif clock.get('game_start_tick') is not None:
        base=clock['game_start_tick']-paused(clock['game_start_tick'])
    else:return None
    return (tick-paused(tick)-base)/30


def review(capture_path,baseline_snapshot,output):
    capture_path,baseline_snapshot,output=map(lambda p:Path(p).resolve(),(capture_path,baseline_snapshot,output))
    if output.exists():raise ValueError('capture_review_new_output_required')
    raw=bounded(capture_path,20*1024**2);frozen=json.loads(raw)
    if frozen.get('kind')!='isolated_postgame_capture_export' or frozen.get('schema_version')!=1:
        raise ValueError('capture_review_explicit_export_required')
    files={name:base64.b64decode(frozen[name],validate=True) for name in
           ('manifest_base64','wards_base64','context_base64')}
    manifest=json.loads(files['manifest_base64']);context=json.loads(files['context_base64'])
    mid=manifest['match_id'];source=manifest['source_sha256']
    for name,key in [('wards.json','wards_base64'),('postgame_context.json','context_base64')]:
        receipt=manifest['files'][name]
        if receipt['sha256']!=sha(files[key]) or receipt['bytes']!=len(files[key]):
            raise ValueError('capture_review_output_digest')
    if manifest.get('postgame_context',{}).get('producer_sha256')!=context.get('producer_sha256'):
        raise ValueError('capture_review_context_producer')
    validate_context(context,mid,source,require_complete=False)
    baseline_raw=bounded(baseline_snapshot,32*1024**2);baseline=json.loads(baseline_raw)
    if baseline.get('kind')!='frozen_verified_ward_exports' or baseline.get('schema_version')!=1:
        raise ValueError('capture_review_baseline_kind')
    candidates=[r for r in baseline['matches'] if r['match_id']==mid]
    if len(candidates)!=1:raise ValueError('capture_review_baseline_identity')
    entry=candidates[0];old_raw=base64.b64decode(entry['wards_base64'],validate=True)
    old_manifest=json.loads(base64.b64decode(entry['manifest_base64'],validate=True))
    if (old_manifest['source_sha256']!=source or old_manifest.get('complete') is not True
            or sha(old_raw)!=old_manifest['files']['wards.json']['sha256']
            or entry['catalog']['sha256']!=source):raise ValueError('capture_review_baseline_binding')
    old_wards,new_wards=json.loads(old_raw),json.loads(files['wards_base64'])
    full=(manifest.get('complete') is True and context['complete'] is True
          and manifest.get('stop_reason')=='replay_eof' and not manifest.get('parse_error')
          and manifest.get('source_unchanged_stat') is True and manifest.get('independent_footer_matches_parser') is True)
    comparisons={'byte_identical':old_raw==files['wards_base64'],'record_identical':old_wards==new_wards,
        'baseline_count':len(old_wards),'capture_count':len(new_wards),
        'clock_identical':manifest.get('game_clock')==old_manifest.get('game_clock')}
    fields=Counter();mapping_issues=[];time_residuals=defaultdict(list);identity=defaultdict(set)
    seen_time=None
    for index,packet in enumerate(context['metric_packets']):
        if seen_time is not None and packet['game_time_s']<seen_time:
            mapping_issues.append({'packet':index,'issue':'sample_clock_decreased'})
        seen_time=packet['game_time_s']
        expected=clock_time(manifest['game_clock'],packet['packet_tick'])
        if expected is not None:time_residuals['metric_packet_minus_final_clock_s'].append(packet['game_time_s']-expected)
        if packet['resource_mapping_resolved']:
            teams=Counter(p['team'] for p in packet['players'])
            slots={(p['team'],p['team_slot']) for p in packet['players']}
            if teams!={2:5,3:5} or len(slots)!=10 or any(type(s) is not int or not 0<=s<5 for _,s in slots):
                mapping_issues.append({'packet':index,'issue':'reported_resolved_mapping_not_five_per_team'})
        for player in packet['players']:
            if player['hero_id']:identity[player['logical_player_id']].add(player['hero_id'])
            for field,value in player['metrics'].items():fields[field+('::present' if value is not None else '::missing')]+=1
    missing_event_times=[]
    for index,event in enumerate(context['events']):
        if event['packet_game_time_s'] is None:missing_event_times.append(index)
        expected=clock_time(manifest['game_clock'],event['packet_tick'])
        if expected is not None and event['packet_game_time_s'] is not None:
            time_residuals['event_packet_minus_final_clock_s'].append(event['packet_game_time_s']-expected)
        if event['packet_game_time_s'] is not None and event['event_game_time_s'] is not None:
            time_residuals['combat_event_minus_packet_s'].append(event['event_game_time_s']-event['packet_game_time_s'])
    recomputed_events=dict(Counter(r['family'] for r in context['events']))
    coverage_match=(dict(fields)==context['coverage']['metric_field_counts']
                    and recomputed_events==context['coverage']['event_family_counts'])
    issues=list(mapping_issues)
    if not coverage_match:issues.append({'issue':'coverage_recount_mismatch'})
    if full and not comparisons['record_identical']:issues.append({'issue':'ward_records_changed'})
    if full and not comparisons['clock_identical']:issues.append({'issue':'ward_clock_changed'})
    if not full:issues.append({'issue':'not_a_complete_capture'})
    if missing_event_times:
        issues.append({'issue':'event_packet_time_missing','event_indices':missing_event_times})
    for kind,values in time_residuals.items():
        if kind.endswith('final_clock_s') and any(abs(v)>1e-5 for v in values):
            issues.append({'issue':'saved_packet_time_differs_from_final_clock','kind':kind})
    result={'schema_version':1,'kind':'postgame_capture_real_source_review','match_id':mid,
        'source_sha256':source,'capture_sha256':sha(raw),'baseline_snapshot_sha256':sha(baseline_raw),
        'full_capture':full,'status':'review_complete_with_issues' if issues else 'structural_review_complete_semantics_still_pending',
        'ward_comparison':comparisons,'event_family_counts':recomputed_events,
        'event_classifications':dict(Counter(r['classification'] for r in context['events'])),
        'metric_packets':len(context['metric_packets']),'metric_fields':dict(fields),
        'event_packet_time_counts':{'recorded':len(context['events'])-len(missing_event_times),'missing':len(missing_event_times)},
        'metric_identities':{str(k):sorted(v) for k,v in identity.items()},
        'coverage_recount_matches':coverage_match,'clock_residuals':{key:{'count':len(values),'min':min(values),'max':max(values)} for key,values in time_residuals.items()},
        'replay_metadata':context['replay_metadata'],'issues':issues,
        'limitations':['Structural and source consistency does not certify game counter meaning or hero attribution.',
            'Sampled metrics are not every-frame coverage; events may retain unknown optional flags.',
            'This single isolated replay does not certify every map/build/parser combination or resource profile.']}
    if sha(bounded(capture_path,20*1024**2))!=sha(raw) or sha(bounded(baseline_snapshot,32*1024**2))!=sha(baseline_raw):
        raise ValueError('capture_review_source_changed')
    output.mkdir(parents=True)
    (output/'review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return result
