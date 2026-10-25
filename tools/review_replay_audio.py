"""Postgame replay/audio alignment after predictions are frozen; no relabeling."""
import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.analyze_match_audio import read_rows, sha


def interpolate(rows, value, x, y):
    for a,b in zip(rows,rows[1:]):
        dx,dy = b[x]-a[x], b[y]-a[y]
        if 0 < dx <= 2.5 and 0 < dy <= 2.5 and a[x] <= value <= b[x]:
            return a[y]+(value-a[x])*dy/dx
    return None


def scan_window_diagnostics(scans, *, take_id, offset_s, recorded_skill, targets, radius_s=2.):
    """Posthoc score context; ordinary control skills need all-target evidence.

    Missing scores are not zero. The highest target score and widest margin
    can come from different scans; neither establishes audibility or a cast.
    Legacy same-skill fields stay available with their original narrow scope.
    """
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    if (not isinstance(take_id, str) or not take_id or not isinstance(recorded_skill, str)
            or not recorded_skill or not number(offset_s) or offset_s < 0
            or not number(radius_s) or radius_s <= 0
            or not isinstance(targets, (set, frozenset, list, tuple)) or not targets
            or any(not isinstance(t, str) or not t for t in targets)):
        raise ValueError('invalid_scan_window_context')
    targets = set(targets)
    nearby = [s for s in scans if s.get('take_id') == take_id
              and number(s.get('audio_offset_s'))
              and abs(s['audio_offset_s'] - offset_s) <= radius_s]
    same = [s for s in nearby if s.get('skill') == recorded_skill]
    target_rows = [s for s in nearby if s.get('skill') in targets]
    def strongest(rows):
        return max((s for s in rows if number(s.get('similarity'))),
                   key=lambda s: s['similarity'], default=None)
    def widest(rows):
        return max((s for s in rows if number(s.get('similarity'))
                    and number(s.get('runner_up_similarity'))),
                   key=lambda s: s['similarity'] - s['runner_up_similarity'], default=None)
    return copy.deepcopy({
        'near_scan_reasons': dict(Counter(s.get('reason', 'unreported') for s in same)),
        'strongest_near_scan': strongest(same),
        'scan_window_diagnostics': {
            'version': 'same_skill_and_all_targets_v1',
            'radius_s': radius_s,
            'same_skill_scope': 'recorded_skill_only_not_all_classifier_outputs',
            'same_skill_scan_count': len(same),
            'all_scan_count': len(nearby),
            'all_scan_reasons': dict(Counter(s.get('reason', 'unreported') for s in nearby)),
            'target_scan_count': len(target_rows),
            'target_scan_reasons': dict(Counter(s.get('reason', 'unreported') for s in target_rows)),
            'strongest_target_scan': strongest(target_rows),
            'widest_margin_same_skill_scan': widest(same),
            'widest_margin_target_scan': widest(target_rows),
            'low_level_scan_count': sum(s.get('reason') == 'low_level' for s in nearby),
            'unscored_scan_count': sum(not number(s.get('similarity')) for s in nearby),
            'audibility_verified': False,
            'prediction_or_threshold_modified': False,
        },
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--analysis',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    run,analysis,archive,output = (p.resolve() for p in (args.run,args.analysis,args.archive,args.output))
    from dota2_insights.history import checked_input, checked_output
    for path in (run, analysis, archive):
        checked_input(ROOT, path)
    checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_local_diagnostics_output_required')
    frozen = json.loads((analysis/'prediction-manifest.json').read_text())
    manifest = json.loads((archive/'manifest.json').read_text())
    if (not manifest.get('export_complete') or manifest.get('parse_error') or manifest.get('truncated_at_tick') is not None
            or manifest.get('source_unchanged_stat') is not True or manifest.get('stop_reason')!='replay_eof'):
        raise ValueError('complete_replay_archive_required')
    events_path = archive/'events.jsonl'
    if (sha(events_path)!=manifest['files']['events.jsonl']['sha256']
            or sha(analysis/'predictions.jsonl')!=frozen['prediction_sha256']
            or sha(run/'segments.jsonl')!=frozen['source_journal_sha256']):
        raise ValueError('frozen_source_hash_mismatch')
    post = json.loads((analysis/'post-gsi-review.json').read_text())
    if post['reference_sha256']!=sha(run/'reference-gsi.jsonl'):
        raise ValueError('reference_hash_mismatch')
    match = manifest['match_id']
    targets = set((frozen.get('engine_policy') or {}).get('targets',[]))
    if not targets:
        raise ValueError('explicit_frozen_target_scope_required')
    predictions = read_rows(analysis/'predictions.jsonl',16*1024**2)
    if sha(analysis/'scans.jsonl')!=frozen['scan_sha256']:
        raise ValueError('frozen_scans_hash_mismatch')
    scans = read_rows(analysis/'scans.jsonl',64*1024**2)
    records = read_rows(run/'segments.jsonl',16*1024**2)
    clocks = {}
    for ref in read_rows(run/'reference-gsi.jsonl',64*1024**2):
        ctx = ref['context']; clock = ctx.get('clock_time_s')
        if str(ctx.get('match_id'))==match and type(clock) in (int,float):
            clocks.setdefault(ref['take_id'],[]).append({'audio':ref['audio_offset_s'],'clock':clock})
    for rows in clocks.values():rows.sort(key=lambda r:r['audio'])
    ability_events = [r for r in read_rows(events_path,128*1024**2) if r.get('event_type')=='ABILITY']
    events = [r for r in ability_events if r.get('inflictor') in targets]
    # Post-hoc confusing ordinary spells, kept separate from target labels.
    controls = [r for r in ability_events if r.get('inflictor') in {'zuus_arc_lightning','zuus_lightning_bolt'}
                and not any(abs(e['game_time_s']-r['game_time_s'])<=5 for e in events)]
    # Spread examples over the replay, rather than selecting best-scoring ones.
    controls = [controls[round(i*(len(controls)-1)/min(11,len(controls)-1))]
                for i in range(min(12,len(controls)))] if len(controls)>1 else controls
    output.mkdir(parents=True)
    cast_rows = []
    for event in events + controls:
        game = event['game_time_s']; anchors = []
        for take,rows in clocks.items():
            offset = interpolate(rows,game,'clock','audio')
            if offset is not None:anchors.append((take,offset))
        # Multiple takes at one game clock (e.g. pause/reconnect) are ambiguous.
        row = {'skill':event['inflictor'],'attacker':event['attacker'],'game_time_s':game,
               'role':'target_cast' if event['inflictor'] in targets else 'ordinary_spell_control',
               'event_tick':event.get('event_tick'),'packet_tick':event['packet_tick'],
               'event_game_time_s':event.get('event_game_time_s'),'timestamp_raw':event.get('timestamp_raw'),
               'status':'no_unambiguous_recorded_clock_anchor','audible_to_player':'unverified'}
        if len(anchors)==1:
            take,offset = anchors[0]
            matched = [i+1 for i,p in enumerate(predictions) if p.get('take_id')==take
                and p['skill']==row['skill'] and abs(p['audio_offset_s']-offset)<=2]
            row.update(take_id=take,audio_offset_s=offset,near_candidate_lines=matched,
                       status='replay_cast_near_candidate' if matched else 'replay_cast_without_near_candidate')
            if row['role']=='ordinary_spell_control':
                row.update(status='control_window_not_verified_audible_negative',
                    near_target_candidate_lines=[i+1 for i,p in enumerate(predictions)
                        if p.get('take_id')==take and abs(p['audio_offset_s']-offset)<=2])
            row.update(scan_window_diagnostics(scans, take_id=take, offset_s=offset,
                                               recorded_skill=row['skill'], targets=targets))
            # Export up to 40 continuous five-second listening windows, source
            # PCM untouched. A cast log is not a verified audible sound label.
            if sum('clip' in r for r in cast_rows)<40:
                start,end=max(0,offset-2),offset+3
                chunks,sources,coverage,fmt=[],[],[],None
                for segment in records:
                    if ((segment.get('take_label') or {}).get('take_id')!=take
                            or str((segment.get('match_association') or {}).get('match_id'))!=match):continue
                    base=segment['take_audio_start_offset_s']
                    left,right=max(start,base),min(end,base+segment['audio_seconds'])
                    if left>=right:continue
                    path=(run/segment['audio_file']).resolve()
                    if path.parent!=run or path.stat().st_size>4*1024**2 or sha(path)!=segment['sha256']:
                        raise ValueError('audio_segment_hash_mismatch')
                    with wave.open(str(path),'rb') as stream:
                        current=(stream.getnchannels(),stream.getsampwidth(),stream.getframerate())
                        if fmt is not None and fmt!=current:raise ValueError('mixed_audio_formats')
                        fmt=current
                        stream.setpos(round((left-base)*fmt[2]))
                        chunks.append(stream.readframes(round((right-left)*fmt[2])))
                    sources.append({'file':path.name,'sha256':segment['sha256']})
                    coverage.append((left,right))
                continuous=(coverage and coverage[0][0]<=start+.001 and coverage[-1][1]>=end-.001
                    and all(abs(a[1]-b[0])<=.001 for a,b in zip(coverage,coverage[1:])))
                if continuous:
                    name=f'cast-review-{len(cast_rows)+1:03d}.wav'
                    with wave.open(str(output/name),'wb') as stream:
                        stream.setnchannels(fmt[0]);stream.setsampwidth(fmt[1]);stream.setframerate(fmt[2])
                        stream.writeframes(b''.join(chunks))
                    row.update(clip=name,clip_sha256=sha(output/name),clip_sources=sources,
                               clip_start_audio_s=start,clip_end_audio_s=end)
        cast_rows.append(row)
    candidate_rows=[]
    for line,p in enumerate(predictions,1):
        game=interpolate(clocks.get(p.get('take_id'),[]),p['audio_offset_s'],'audio','clock')
        nearby=[e for e in cast_rows if e['role']=='target_cast' and e['skill']==p['skill'] and e.get('take_id')==p.get('take_id')
                and e.get('audio_offset_s') is not None and abs(e['audio_offset_s']-p['audio_offset_s'])<=2]
        candidate_rows.append({'prediction_line':line,'skill':p['skill'],'mapped_game_time_s':game,
            'near_replay_casts':[{'game_time_s':e['game_time_s'],
                'approx_audio_delta_s':p['audio_offset_s']-e['audio_offset_s'],'attacker':e['attacker']} for e in nearby],
            'status':'replay_cast_corroborated_timing_not_audibility' if nearby else 'no_nearby_cast_or_clock_unavailable'})
    report={'schema_version':1,'match_id':match,'purpose':'post_prediction_replay_review_only',
        'scan_diagnostics_version':'same_skill_and_all_targets_v1',
        'prediction_sha256':frozen['prediction_sha256'],'archive_manifest_sha256':sha(archive/'manifest.json'),
        'replay_source_sha256':manifest['source_sha256'],'events_sha256':sha(events_path),
        'reference_sha256':post['reference_sha256'],'tool_source_sha256':sha(Path(__file__)),
        'targets':sorted(targets),'cast_counts':dict(Counter(r['skill'] for r in cast_rows if r['role']=='target_cast')),
        'control_counts':dict(Counter(r['skill'] for r in cast_rows if r['role']=='ordinary_spell_control')),
        'status_counts':dict(Counter(r['status'] for r in cast_rows)),
        'casts':cast_rows,'candidates':candidate_rows,
        'limits':['integer GSI clocks and delivery delay make this approximate; +/-2s is not latency acceptance',
                  'replay ABILITY proves logged use, not player audibility or sound phase',
                  'absence of audio candidate is not an independently labeled audible miss',
                  'no cast/GSI input used by classifier; original predictions unchanged']}
    (output/'review.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'match_id':match,'cast_counts':report['cast_counts'],'status_counts':report['status_counts'],
        'clips':sum('clip' in r for r in cast_rows),'candidates':candidate_rows}))


if __name__=='__main__':main()
