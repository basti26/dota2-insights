"""Load a complete captured sidecar bound to the existing ward snapshot."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from .postgame_store import bounded,sha
from .replay_postgame_context import validate_context


def load_capture(path,ward_manifest,*,metric_clock_policy='strict',event_clock_policy='strict'):
    raw=bounded(path,20*1024**2);frozen=json.loads(raw)
    if frozen.get('kind')!='isolated_postgame_capture_export' or frozen.get('schema_version')!=1:
        raise ValueError('replay_context_explicit_frozen_capture_required')
    manifest_raw=base64.b64decode(frozen['manifest_base64'],validate=True)
    wards_raw=base64.b64decode(frozen['wards_base64'],validate=True)
    context_raw=base64.b64decode(frozen['context_base64'],validate=True)
    m=json.loads(manifest_raw);context=json.loads(context_raw)
    if (m.get('match_id')!=ward_manifest['match_id'] or m.get('source_sha256')!=ward_manifest['source_sha256']
            or m.get('complete') is not True or m.get('source_unchanged_stat') is not True
            or m.get('independent_footer_matches_parser') is not True or m.get('stop_reason')!='replay_eof'
            or m.get('parse_error') or m.get('game_clock')!=ward_manifest['game_clock']
            or m.get('parser_version')!=ward_manifest['parser_version']
            or m.get('parser_commit')!=ward_manifest['parser_commit']):
        raise ValueError('replay_context_source_complete_parser_clock_mismatch')
    for name,body in [('wards.json',wards_raw),('postgame_context.json',context_raw)]:
        if m.get('files',{}).get(name,{}).get('sha256')!=sha(body) or m['files'][name].get('bytes')!=len(body):
            raise ValueError('replay_context_file_digest_mismatch')
    if sha(wards_raw)!=ward_manifest['files']['wards.json']['sha256']:
        raise ValueError('replay_context_baseline_wards_changed')
    validate_context(context,m['match_id'],m['source_sha256'])
    if m.get('postgame_context',{}).get('producer_sha256')!=context['producer_sha256']:
        raise ValueError('replay_context_collector_producer_mismatch')
    if metric_clock_policy not in ('strict','reconcile_recorded_pauses'):
        raise ValueError('replay_context_metric_clock_policy')
    if event_clock_policy not in ('strict','retain_missing'):
        raise ValueError('replay_context_event_clock_policy')
    from .postgame_capture_review import clock_time
    # Tick and final clock are explicit source fields; never estimate from a
    # nearby event, HTTP receipt or a later metric value.
    clock=m['game_clock'];packets=[];clock_changes=[]
    for index,packet in enumerate(context['metric_packets']):
        final=clock_time(clock,packet['packet_tick'])
        if final is None:raise ValueError('replay_context_metric_final_clock_missing')
        residual=packet['game_time_s']-final
        if abs(residual)>1e-5:
            if metric_clock_policy=='strict':raise ValueError('replay_context_metric_packet_clock_mismatch')
            paused=[p for p in clock.get('pauses',[]) if p['start_tick']<=packet['packet_tick'] and
                (p['end_tick'] is None or packet['packet_tick']<p['end_tick'])]
            if len(paused)!=1:raise ValueError('replay_context_metric_clock_mismatch_outside_unique_recorded_pause')
            elapsed=(packet['packet_tick']-paused[0]['start_tick'])/30
            if abs(residual-elapsed)>1e-5:
                raise ValueError('replay_context_metric_clock_mismatch_not_explained_by_open_pause')
            clock_changes.append({'source_packet_index':index,'packet_tick':packet['packet_tick'],
                'raw_sample_game_time_s':packet['game_time_s'],'final_clock_game_time_s':final,'raw_minus_final_s':residual})
            packets.append({**packet,'game_time_s':final})
        else:packets.append(packet)
    missing_event_times=[]
    for index,event in enumerate(context['events']):
        if event['packet_game_time_s'] is None and event_clock_policy=='retain_missing':
            missing_event_times.append(index)
            continue
        final=clock_time(clock,event['packet_tick'])
        if final is None or event['packet_game_time_s'] is None or abs(event['packet_game_time_s']-final)>1e-5:
            raise ValueError('replay_context_event_packet_clock_mismatch')
    events=[{'match_id':m['match_id'],'event_kind':'captured_combat','event_index':index,
        'time_s':r['packet_game_time_s'],'team':r['attacker_team'],'actor_hero_numeric':None,
        'payload':json.dumps(r,allow_nan=False),'context_sha256':sha(context_raw),
        'time_basis':'same_DEM_same_pass_packet_clock_not_recorded_event_clock_reference_only'
        if r['packet_game_time_s'] is None else 'same_DEM_same_pass_packet_clock_event_clock_retained_separately'}
        for index,r in enumerate(context['events'])]
    if sha(bounded(path,20*1024**2))!=sha(raw):raise ValueError('replay_context_source_changed')
    provenance={'match_id':m['match_id'],'capture_sha256':sha(raw),
        'manifest_sha256':sha(manifest_raw),'context_sha256':sha(context_raw),'replay_sha256':m['source_sha256'],
        'producer_sha256':context['producer_sha256'],'replay_metadata':context['replay_metadata'],
        'ward_bytes_identical_to_baseline':True,'coverage':context['coverage'],
        'limits':context['limits'],'scope':'single_complete_isolated_capture_no_global_quality_claim'}
    if clock_changes:
        provenance['metric_clock_projection']={'policy':metric_clock_policy,
            'basis':'same_packet_tick_final_source_clock_with_explicit_recorded_pause_no_interpolation',
            'source_metric_packet_count':len(context['metric_packets']),'projected_packet_count':len(packets),
            'changed_packets':clock_changes,'original_capture_bytes_preserved':True,
            'same_time_packets':'retained_in_original_order_latest_tick_required_for_checkpoint_ties'}
    if missing_event_times:
        provenance['event_packet_time_missing']={'policy':'retain_missing',
            'basis':'original_null_packet_time_retained_no_final_clock_or_event_clock_fill',
            'record_count':len(missing_event_times),'event_indices':missing_event_times}
    return events,packets,provenance
