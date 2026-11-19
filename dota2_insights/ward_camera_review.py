"""Read closed automatic camera evidence and optionally inspect minimap pixels."""
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

from .replay_holdout import refuse_locked_match
from .match_journal_index import history_bases
from .ward_projection_diagnostics import ProjectionDiagnostics
from .ward_ground_pose import GroundPoseReview

MAX_BYTES=128*1024**2
KINDS={'location','surface','waiting','capture_error','surface_error','capture_unavailable','reference_added','native_camera_position','native_camera_orientation'}


def _digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def _file(folder,name):
    if not (name=='events.jsonl' or re.fullmatch(r'frame-[0-9]{4}\.(?:png|jpg)',name)):
        raise ValueError('ward_camera_file_name')
    p=folder/name
    if p.is_symlink() or p.is_junction() or not p.resolve(strict=True).is_relative_to(folder.resolve(strict=True)):
        raise ValueError('ward_camera_link_or_escape')
    return p


def _validate_installed_map(value):
    expected = {'schema_version', 'asset', 'status', 'entry', 'exit', 'same_file_stat_at_boundaries',
                'runtime_map_identity_verified', 'capture_map_binding_verified'}
    if (not isinstance(value, dict) or value.keys() != expected
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['asset'] != 'game/dota/maps/dota.vpk'
            or value['runtime_map_identity_verified'] is not False
            or value['capture_map_binding_verified'] is not False):
        raise ValueError('ward_installed_map_contract')
    entry, end, same = (value[k] for k in ('entry', 'exit', 'same_file_stat_at_boundaries'))
    for receipt in (entry, end):
        if receipt is not None and (not isinstance(receipt, dict) or set(receipt) != {'bytes', 'sha256'}
                or type(receipt['bytes']) is not int or not 0 < receipt['bytes'] <= 64 * 1024**2
                or not isinstance(receipt['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', receipt['sha256'])):
            raise ValueError('ward_installed_map_fingerprint')
    status = value['status']
    if status in ('same_on_disk_at_boundaries', 'changed_on_disk'):
        if entry is None or end is None or type(same) is not bool:
            raise ValueError('ward_installed_map_boundary_missing')
        if (status == 'same_on_disk_at_boundaries') != (same and entry == end):
            raise ValueError('ward_installed_map_boundary_disagrees')
    elif (status not in ('probe_incomplete', 'no_unique_installation', 'asset_unavailable')
            or end is not None or same is not None or status == 'no_unique_installation' and entry is not None):
        raise ValueError('ward_installed_map_unavailable_contract')


def installed_map_relation(evidence, target_sha256):
    """Use disk receipts to prioritize review, without binding a frame to a map."""
    if not isinstance(target_sha256, str) or not re.fullmatch('[a-f0-9]{64}', target_sha256):
        raise ValueError('ward_target_map_fingerprint')
    if evidence is None:
        return 'boundary_receipt_missing'
    _validate_installed_map(evidence)
    if evidence['status'] == 'changed_on_disk':
        return 'installation_changed_during_run'
    if evidence['status'] != 'same_on_disk_at_boundaries':
        return 'usable_boundary_pair_missing'
    return ('both_boundaries_match_target' if evidence['entry']['sha256'] == target_sha256
            else 'both_boundaries_differ_from_target')


def _header(folder, *, name='CLOSED.json', require_closed=True):
    folder=Path(folder)
    if not re.fullmatch(r'run_[a-f0-9]{32}',folder.name):raise ValueError('ward_camera_run_name')
    from .minimap.offline_io import check_input_paths
    check_input_paths(folder)
    if name not in ('CLOSED.json','session.json'):raise ValueError('ward_camera_header_name')
    p=folder/name
    if p.is_symlink() or p.is_junction():raise ValueError('ward_camera_header_link')
    with p.open('rb') as f:raw=f.read(128*1024+1)
    if len(raw)>128*1024:raise ValueError('ward_camera_header_limit')
    d=json.loads(raw)
    if (not isinstance(d,dict) or type(d.get('schema_version')) is not int or d['schema_version'] not in (1,2)
            or d.get('kind')!='ward_camera_evidence' or type(d.get('closed')) is not bool
            or d.get('run_id')!=folder.name[4:] or type(d.get('complete')) is not bool
            or d.get('coordinate_accuracy_verified') is not False
            or d.get('complete') is True and d.get('closed') is not True):
        raise ValueError('ward_camera_closed_metadata')
    error=d.get('error_type')
    if 'installed_map_evidence' in d:
        _validate_installed_map(d['installed_map_evidence'])
    if error is not None and (not isinstance(error,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}',error)):
        raise ValueError('ward_camera_error_label')
    if require_closed and (d['closed'] is not True or error is not None):
        raise ValueError('ward_camera_closed_metadata')
    if d['schema_version']==2:
        if (d.get('image_format') not in ('png','jpeg')
                or d.get('native_image_dimensions_preserved') is not True
                or d['image_format']=='jpeg' and (type(d.get('jpeg_quality')) is not int or not 1<=d['jpeg_quality']<=100)
                or d['image_format']=='png' and d.get('jpeg_quality') is not None):
            raise ValueError('ward_camera_image_policy')
    scopes=d.get('contexts')
    if not isinstance(scopes,list) or len(scopes)>128:raise ValueError('ward_camera_contexts')
    identities=[]
    for scope in scopes:
        if not isinstance(scope,dict):raise ValueError('ward_camera_context')
        mid=scope.get('match_id')
        if mid is not None and (not isinstance(mid,str) or not mid.isascii() or not mid.isdigit() or not 1<=len(mid)<=20):
            raise ValueError('ward_camera_match_id')
        refuse_locked_match(mid)  # Before hashing/decoding any evidence payload.
        for key in ('game_session_id','record_session_id'):
            if not isinstance(scope.get(key),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',scope[key]):
                raise ValueError('ward_camera_context_identity')
        identity=tuple(scope.get(k) for k in ('match_id','game_session_id','record_session_id'))
        if identity in identities:raise ValueError('ward_camera_duplicate_scope')
        identities.append(identity)
    return d,raw,identities


def _frame_key(row):
    context=row.get('context')
    frame=row.get('frame_id')
    if not isinstance(context,dict) or not isinstance(frame,str) or not 1<=len(frame)<=256:
        return None
    source=context.get('gsi_source_id')
    if not isinstance(source,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',source):
        return None
    return (frame,context.get('match_id'),context.get('game_session_id'),context.get('record_session_id'),source)


def _record_alt_motion(groups,row,schema_version):
    sample=row.get('alt_motion_window')
    paint_status=row.get('alt_motion_paint_status')
    if sample is None:
        if paint_status is not None:
            raise ValueError('ward_camera_alt_motion_paint_without_window')
        return
    key=_frame_key(row)
    geometry=tuple(row.get(k) for k in ('reference_epoch','width','height'))
    at=row.get('captured_monotonic')
    if (schema_version!=2 or row['kind']!='location' or key is None
            or not isinstance(sample,dict)
            or set(sample)!={'version','id','index','alt_requested','camera_cut_verified'}
            or sample['version']!='ward_alt_motion_window_v1'
            or not isinstance(sample['id'],str) or not re.fullmatch('[a-f0-9]{32}',sample['id'])
            or type(sample['index']) is not int or not 0<=sample['index']<=2
            or sample['alt_requested'] is not True or sample['camera_cut_verified'] is not False
            or any(type(v) is not int or v<0 for v in geometry)
            or not 1<=geometry[1]<=4096 or not 1<=geometry[2]<=2160
            or type(at) not in (int,float) or not math.isfinite(at) or at<0):
        raise ValueError('ward_camera_alt_motion_metadata')
    identity=sample['id']
    if paint_status is not None:
        states=('no_prior_paint','paint_changed_during_capture','paint_scope_changed',
                'paint_outside_capture_window','paint_candidate_recorded')
        if (paint_status not in states or (paint_status=='paint_candidate_recorded') !=
                (row.get('terrain_patch_surface_sample') is not None)):
            raise ValueError('ward_camera_alt_motion_paint_status')
    if identity not in groups and len(groups)>=6:
        raise ValueError('ward_camera_alt_motion_window_budget')
    group=groups.setdefault(identity,[])
    point=(sample['index'],at,key[0],key[1:4]+geometry,(row.get('image') or {}).get('file'),paint_status)
    if group:
        old_index,old_at,_,scope,_,_=group[-1]
        steps=point[0]-old_index
        if (point[3]!=scope or any(point[2]==old[2] for old in group) or steps<1
                or not .25*steps<=at-old_at<=1.*steps):
            raise ValueError('ward_camera_alt_motion_window_order')
    group.append(point)


def _return_motion_attachment(files,viewport,paths,paints):
    first,_,last=files
    if first not in paints or last not in paints:
        return {'status':'missing_endpoint_paint_evidence'}
    old_key,old=paints[first]
    new_key,new=paints[last]
    if old_key!=new_key or old['render_frame_id']==new['render_frame_id']:
        return {'status':'paint_identity_not_comparable'}
    if len(old.get('footprints',{}).keys() & new.get('footprints',{}).keys())<2:
        return {'status':'insufficient_shared_painted_footprints'}
    from .ward_minimap_pixels import read_rgb
    from .ward_terrain_pixel_review import compare
    try:
        return compare(read_rgb(paths[first],viewport),read_rgb(paths[last],viewport),old,new,
                       min_terrain_motion_px=0.,attachment_gate_px=5.33*viewport[0]/2560.)
    except (OSError,ValueError):
        return {'status':'image_not_decodable'}


def _alt_motion_summary(groups, paths=None, paints=None):
    windows=[]
    for identity,group in groups.items():
        files={point[4] for point in group if point[4] is not None}
        complete=[point[0] for point in group]==[0,1,2] and len(files)==3
        result={'id':identity,'complete':complete,'frames':[
            {'index':index,'capture_monotonic':at,'frame_id':frame,'file':file,'paint_status':status}
            for index,at,frame,_,file,status in group]}
        if complete and paths is not None:
            from .ward_alt_motion_pixels import classify
            files_in_order=[point[4] for point in group]
            viewport=group[0][3][-2:]
            try:
                result['pixel_comparison']=classify([paths[file] for file in files_in_order],viewport)
            except (OSError,ValueError):
                result['pixel_comparison']={'state':'image_not_decodable'}
            if result['pixel_comparison']['state']=='view_return_candidate':
                result['return_attachment']=_return_motion_attachment(files_in_order,viewport,paths,paints or {})
        windows.append(result)
    return {'windows':windows,'complete_image_windows':sum(row['complete'] for row in windows),
            'camera_cut_verified':False,'world_alignment_verified':False}


def _calibrated_minimap(row):
    value = row.get('calibrated_minimap')
    sample = row.get('calibrated_minimap_surface_sample')
    if value is None and sample is None:
        return None, (), None
    if (not isinstance(value, dict) or not isinstance(value.get('reason'), str)
            or not re.fullmatch(r'minimap_[a-z_]{1,55}', value['reason'])
            or not isinstance(value.get('minimap_markers'), list) or len(value['minimap_markers']) > 5000):
        raise ValueError('ward_calibrated_minimap_shape')
    points = []
    seen = set()
    for point in value['minimap_markers']:
        if (not isinstance(point, dict) or point.get('surface') != 'minimap'
                or not isinstance(point.get('id'), str) or not 1 <= len(point['id']) <= 160
                or point['id'] in seen or point.get('color') not in ('#ff4141', '#39eb70')
                or type(point.get('tree_cross')) is not bool
                or any(type(point.get(k)) not in (float,int) or not math.isfinite(point[k])
                       or not 0 <= point[k] < row[dimension] for k, dimension in (('x','width'),('y','height')))):
            raise ValueError('ward_calibrated_minimap_point')
        seen.add(point['id'])
        points.append(tuple(point[k] for k in ('id','x','y','color','tree_cross')))
    if points and value['reason'] != 'minimap_calibrated_hud_confirmed':
        raise ValueError('ward_calibrated_minimap_unconfirmed_points')
    if sample is not None:
        if (not points or not isinstance(sample, dict)
                or sample.keys() != {'shown','alt_held','changed_monotonic','render_frame_id'}
                or type(sample['shown']) is not bool or sample['alt_held'] is not sample['shown']
                or not isinstance(sample['render_frame_id'], str) or not 1 <= len(sample['render_frame_id']) <= 256
                or any(type(at) not in (int,float) or not math.isfinite(at)
                       for at in (sample['changed_monotonic'],row.get('captured_monotonic')))
                or sample['changed_monotonic'] > row['captured_monotonic']
                or type(row.get('reference_epoch')) is not int or row['reference_epoch'] < 0
                or not isinstance(row.get('reference_packet_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}',row['reference_packet_sha256'])):
            raise ValueError('ward_calibrated_minimap_surface_sample')
    return value['reason'], tuple(sorted(points)), sample


def _calibrated_minimap_pairs(frames):
    image_uses = Counter(frame['file'] for frame in frames)
    by_scope = {}
    for frame in frames:
        by_scope.setdefault(frame['key'], []).append(frame)
    pairs = []
    for scoped in by_scope.values():
        ordered = sorted(scoped, key=lambda frame: frame['captured'])
        for before, after in zip(ordered, ordered[1:]):
            gap = after['captured'] - before['captured']
            if (before['shown'] == after['shown']
                    or image_uses[before['file']] != 1 or image_uses[after['file']] != 1
                    or not 0 < gap <= 3.
                    or not before['captured'] < after['changed'] <= after['captured']):
                continue
            shown, hidden = (after, before) if after['shown'] else (before, after)
            pairs.append((after['changed'], {'shown_image': shown['file'], 'hidden_image': hidden['file'],
                          'capture_gap_s': gap, 'transition': 'press' if after['shown'] else 'release',
                          'marker_count': len(after['key'][-1])}))
    return [pair for _, pair in sorted(pairs, key=lambda item: item[0])]


def _projection(row):
    width,height=row.get('width'),row.get('height')
    count,points,omitted=row.get('projected_count'),row.get('projected_markers'),row.get('projected_omitted')
    if (type(width) is not int or type(height) is not int or not 1<=width<=4096 or not 1<=height<=2160
            or type(count) is not int or not 0<=count<=5000 or not isinstance(points,list)
            or len(points)!=min(count,256) or type(omitted) is not int or omitted!=count-len(points)):
        raise ValueError('ward_camera_projection_shape')
    seen=set()
    for point in points:
        if (not isinstance(point,dict) or not isinstance(point.get('id'),str) or not 1<=len(point['id'])<=160
                or point['id'] in seen or point.get('color') not in ('#ff4141','#39eb70')
                or type(point.get('tree_cross')) is not bool
                or any(type(point.get(k)) not in (int,float) or not math.isfinite(point[k]) for k in ('x','y'))
                or not 0<=point['x']<width or not 0<=point['y']<height):
            raise ValueError('ward_camera_projection_point')
        seen.add(point['id'])
    error=row.get('reprojection_error_px')
    if error is not None and (type(error) not in (int,float) or not math.isfinite(error) or error<0):
        raise ValueError('ward_camera_registration_error')
    return count,len(points),omitted,error


def _terrain_projection(row):
    value=row.get('terrain_registration')
    if value is None:return 0,None
    if not isinstance(value,dict) or value.get('version')!='ward_terrain_trial_v1':
        raise ValueError('ward_terrain_evidence_version')
    if (value.get('status') not in ('unlocalized','experimental_visual_reference')
            or value.get('coordinate_accuracy_verified') is True
            or not isinstance(value.get('reason'),str) or not re.fullmatch('[a-z_]{1,80}',value['reason'])):
        raise ValueError('ward_terrain_status_or_claim')
    points=value.get('minimap_markers')
    if not isinstance(points,list) or len(points)>128:raise ValueError('ward_terrain_markers')
    if value.get('status')!='experimental_visual_reference':
        if points:raise ValueError('ward_terrain_unlocalized_points')
        return 0,value.get('reason')
    matrix=value.get('raw_to_minimap_screen');roi=value.get('minimap_roi');age=value.get('registration_age_s')
    if (value.get('coordinate_accuracy_verified') is not False
            or not isinstance(matrix,list) or len(matrix)!=3 or any(not isinstance(r,list) or len(r)!=3 for r in matrix)
            or any(type(x) not in (int,float) or not math.isfinite(x) for r in matrix for x in r)
            or not isinstance(roi,list) or len(roi)!=4 or any(type(x) is not int for x in roi)
            or not (0<=roi[0] and 0<=roi[1] and roi[2]>0 and roi[3]>0
                    and roi[0]+roi[2]<=row['width'] and roi[1]+roi[3]<=row['height'])
            or type(age) not in (int,float) or not math.isfinite(age) or not 0<=age<=2.5):
        raise ValueError('ward_terrain_projection_context')
    _projection({**row,'projected_count':len(points),'projected_markers':points,'projected_omitted':0,'reprojection_error_px':None})
    for point in points:
        if (point.get('surface')!='minimap' or not roi[0]<=point['x']<roi[0]+roi[2]
                or not roi[1]<=point['y']<roi[1]+roi[3]):raise ValueError('ward_terrain_point_outside_minimap')
    return len(points),value.get('reason')


def _surface_links(locations,surfaces):
    joined=0;unknown=Counter();contradictions=Counter();examples=[]
    for number,key,shown,reason in surfaces:
        if key is None:unknown['missing_exact_source_or_frame']+=1;continue
        candidates=locations.get(key,[])
        if not candidates:unknown['no_recorded_location_for_exact_frame']+=1;continue
        if len(candidates)!=1:unknown['duplicate_location_identity']+=1;continue
        location=candidates[0]
        if location['line']>=number:unknown['location_not_prior_to_surface']+=1;continue
        joined+=1;issue=None
        if shown and (location['status']!='experimental_visual_reference' or location['points']==0):
            issue='shown_without_localized_projected_points'
        elif not shown and reason=='no_projected_points' and location['points']>0:
            issue='hidden_as_no_points_but_projection_nonempty'
        elif not shown and reason=='applied_to_tk_surface':issue='applied_reason_but_shown_false'
        if issue:
            contradictions[issue]+=1
            if len(examples)<12:examples.append({'surface_line':number,'location_line':location['line'],'issue':issue})
    return {'association_basis':'same_run_exact_frame_match_both_sessions_and_gsi_source_id_not_UTC',
        'matched_prior_location':joined,'unknown_reasons':dict(unknown),'contradictions':dict(contradictions),
        'examples':examples,'os_visibility_verified':False,'latency_computed':False}


def _native_image_links(frames, positions, orientations):
    counts = Counter()
    examples = []
    for line, row in frames:
        snapshot = row['native_camera_telemetry']
        position = snapshot['candidate']
        context = row['context']
        frame_key = _frame_key(row)
        observed = position['log_observed_monotonic']
        captured = row.get('captured_monotonic')
        match_lines = []
        if frame_key is not None:
            match_lines = [number for number, event in positions
                           if event['context'] == context and event['telemetry'] == position]

        if frame_key is None:
            status = 'missing_frame_or_gsi_source'
        elif len(match_lines) != 1:
            status = 'position_event_ambiguous' if match_lines else 'position_event_missing'
        elif (type(captured) not in (int, float) or not observed <= captured <= observed + .5
                or abs(captured-observed-snapshot['candidate_age_since_log_visible_s']) > 1e-6):
            status = 'frame_clock_unbound_or_age_mismatch'
        elif any(
            event['context'] == context
            and observed <= event['telemetry']['log_observed_monotonic'] <= captured
            and any(event['telemetry'][key] != position[key] for key in ('camera_xyz', 'look_at_xy'))
            for _, event in positions
        ):
            status = 'competing_position_receipt_before_frame'
        else:
            counts['images_with_position_receipt_verified'] += 1
            matches = [(number,event['telemetry']) for number,event in orientations
                       if event['context'] == context and event['telemetry']['position_candidate'] == position]
            if len(matches) != 1:
                status = 'orientation_event_ambiguous' if matches else 'orientation_event_missing'
            elif matches[0][1]['log_observed_monotonic'] > captured:
                status = 'orientation_observed_after_frame'
            else:
                status = 'orientation_receipt_available_before_frame'
        counts[status] += 1
        if len(examples) < 12:
            examples.append({'location_line': line, 'image_sha256': row['image']['sha256'],
                             'position_line': match_lines[0] if len(match_lines) == 1 else None,
                             'status': status})
    return {'counts': dict(counts), 'examples': examples,
            'basis': 'exact_record_context_and_position_receipt_checked_against_host_monotonic_frame',
            'camera_acquisition_time_verified': False, 'capture_frame_alignment_verified': False}


def _native_transition_links(frames, positions, limit):
    if type(limit) is not int or not 0<=limit<=6:
        raise ValueError('ward_camera_transition_budget')
    prior = {}
    pairs = []
    for number,row in frames:
        context = row['context']
        scope = tuple(context.get(k) for k in ('match_id','game_session_id','record_session_id'))
        sample = row.get('native_camera_transition_sample')
        if sample is not None:
            if (len(pairs)>=limit or not isinstance(sample,dict) or set(sample)!={
                'version','before_frame_id','before_captured_monotonic','look_at_delta_world_units',
                'capture_frame_alignment_verified'}
                or sample['version']!='native_camera_transition_pair_v1'
                or sample['capture_frame_alignment_verified'] is not False
                or not isinstance(sample['before_frame_id'],str)):
                raise ValueError('ward_camera_transition_metadata')
            matches = prior.get((*scope,sample['before_frame_id']),[])
            if len(matches)!=1:
                raise ValueError('ward_camera_transition_before_image')
            before_line,before = matches[0]
            a = before['native_camera_telemetry']['candidate']
            b = row['native_camera_telemetry']['candidate']
            at,bt = before.get('captured_monotonic'),row.get('captured_monotonic')
            distance = sample['look_at_delta_world_units']
            same = ('width','height','reference_epoch')
            if (row.get('frame_id')==before.get('frame_id') or _frame_key(row) is None or _frame_key(before) is None
                or any(before.get(k)!=row.get(k) for k in same) or a['log_generation']!=b['log_generation']
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in (at,bt,distance))
                or b['log_observed_monotonic']<=at
                or at!=sample['before_captured_monotonic'] or not .15<=bt-at<=5.
                or not (0<=at-a['log_observed_monotonic']<=.35 and 0<=bt-b['log_observed_monotonic']<=.35)
                or distance<160. or not math.isclose(distance,math.dist(a['look_at_xy'],b['look_at_xy']),abs_tol=1e-7)):
                raise ValueError('ward_camera_transition_scope_or_geometry')
            exact_receipts = [sum(e['context']==image['context'] and e['telemetry']==candidate
                                  for _,e in positions)==1
                              for image,candidate in ((before,a),(row,b))]
            pairs.append({'before_location_line':before_line,'after_location_line':number,
                          'before_image':before['image']['file'],'after_image':row['image']['file'],
                          'frame_gap_s':bt-at,'look_at_delta_world_units':distance,
                          'both_exact_position_events_present':all(exact_receipts),
                          'capture_frame_alignment_verified':False})
        key = (*scope,row.get('frame_id'))
        prior.setdefault(key,[]).append((number,row))
    return pairs


def _terrain_patch_sample(row):
    sample=row.get('terrain_patch_surface_sample')
    if sample is None:
        return None
    if not isinstance(sample,dict):
        raise ValueError('ward_terrain_patch_sample')
    painted=sample.get('painted_monotonic')
    captured=row.get('captured_monotonic')
    points=sample.get('points')
    if (type(painted) not in (int,float) or type(captured) not in (int,float)
            or not 0<=captured-painted<=.35
            or not isinstance(sample.get('render_frame_id'),str)
            or not isinstance(points,dict) or not 1<=len(points)<=192):
        raise ValueError('ward_terrain_patch_sample')
    footprints=sample.get('footprints',{})
    if (not isinstance(footprints,dict) or footprints.keys()-points.keys()
            or any(not isinstance(vertices,list) or len(vertices)!=4 for vertices in footprints.values())):
        raise ValueError('ward_terrain_patch_footprint')
    coordinates=[*points.values(),*(xy for vertices in footprints.values() for xy in vertices)]
    for xy in coordinates:
        if (not isinstance(xy,list) or len(xy)!=2
                or any(type(v) not in (int,float) or not 0<=v<limit
                       for v,limit in zip(xy,(row['width'],row['height'])))):
            raise ValueError('ward_terrain_patch_point')
    return sample


def _terrain_patch_pairs(frames):
    pairs=[]
    for (scope,a,old),(next_scope,b,new) in zip(frames,frames[1:]):
        if scope!=next_scope or a==b:
            continue
        old,new=old['points'],new['points']
        common=old.keys() & new.keys()
        moved={key:round(math.dist(old[key],new[key]),2) for key in sorted(common)
               if math.dist(old[key],new[key])>=12}
        if len(common)>=2:
            pairs.append({'images':[a,b],'marker_displacements_px':moved})
    return pairs


def review(folder, *, analyze_minimap_pixels=False, analyze_ground_ring_pixels=False,
           analyze_terrain_patch_pixels=False, analyze_alt_motion_pixels=False):
    folder=Path(folder);d,header,identities=_header(folder)
    # Absence/false-like values cannot certify a loss-free closed recording.
    # Keep unpublished metadata diagnostics separate from payload admission.
    if (type(d.get('dropped')) is not int or d['dropped']<0
            or type(d.get('limited')) is not bool or type(d.get('shutdown_timed_out')) is not bool
            or type(d.get('images_saved')) is not int or not 0<=d['images_saved']<=80):
        raise ValueError('ward_camera_closed_loss_metadata')
    session=folder/'session.json'
    if session.is_symlink() or session.is_junction() or session.stat().st_size!=len(header) or session.read_bytes()!=header:
        raise ValueError('ward_camera_session_not_closed_version')
    files=d.get('files')
    if not isinstance(files,dict) or 'events.jsonl' not in files or not 1<=len(files)<=81:
        raise ValueError('ward_camera_files')
    total=0;paths={}
    for name,ref in files.items():
        if (not isinstance(ref,dict) or type(ref.get('bytes')) is not int or not 0<=ref['bytes']<=MAX_BYTES
                or not isinstance(ref.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',ref['sha256'])):
            raise ValueError('ward_camera_file_reference')
        if name!='events.jsonl':
            if d['schema_version']==1 and not name.endswith('.png'):
                raise ValueError('ward_camera_v1_png_required')
            if d['schema_version']==2 and not name.endswith('.jpg' if d['image_format']=='jpeg' else '.png'):
                raise ValueError('ward_camera_image_suffix_policy')
        total+=ref['bytes']
        if total>MAX_BYTES:raise ValueError('ward_camera_source_budget')
        p=_file(folder,name)
        if p.stat().st_size!=ref['bytes'] or _digest(p)!=ref['sha256']:raise ValueError('ward_camera_source_mismatch')
        paths[name]=p
    counts=Counter();statuses=Counter();reasons=Counter();image_refs=set();surfaces=Counter();rows=0
    projection_diagnostics=ProjectionDiagnostics()
    native_frames=Counter()
    native_images, position_events, orientation_events = [], [], []
    locations={};surface_events=[];projection_counts=Counter();registration_errors=[];encodings=Counter();terrain_counts=Counter()
    calibrated_counts=Counter();calibrated_images=[];terrain_patch_images=[]
    alt_motion_groups={};alt_motion_paints={}
    ground_pose=GroundPoseReview()
    with paths['events.jsonl'].open('rb') as f:
        while True:
            raw=f.readline(256*1024+1)
            if not raw:break
            rows+=1
            if rows>4096 or len(raw)>256*1024 or not raw.endswith(b'\n'):raise ValueError('ward_camera_event_budget')
            row=json.loads(raw)
            if not isinstance(row,dict) or type(row.get('schema_version')) is not int or row.get('schema_version')!=d['schema_version'] or row.get('kind') not in KINDS:
                raise ValueError('ward_camera_event_kind')
            context=row.get('context')
            if context is not None:
                if not isinstance(context,dict) or tuple(context.get(k) for k in ('match_id','game_session_id','record_session_id')) not in identities:
                    raise ValueError('ward_camera_undeclared_context')
                refuse_locked_match(context.get('match_id'))
            if row['kind']=='location':
                from .native_camera_review import validate_snapshot
                validate_snapshot(row.get('native_camera_telemetry'))
                if context is None:raise ValueError('ward_camera_location_context_missing')
                try:
                    at=datetime.fromisoformat(row['captured_at_utc'].replace('Z','+00:00'))
                    provider=datetime.fromisoformat(context['provider_at_utc'].replace('Z','+00:00'))
                    if at.utcoffset() is None or provider.utcoffset() is None or not -2<=(at-provider).total_seconds()<=5:
                        raise ValueError('ward_camera_capture_clock')
                except (KeyError,TypeError,AttributeError):raise ValueError('ward_camera_capture_clock')
                status=row.get('location_status')
                if status not in ('unlocalized','ambiguous','experimental_visual_reference'):raise ValueError('ward_camera_location_status')
                matrix=row.get('world_to_screen')
                if status=='experimental_visual_reference':
                    if (not isinstance(matrix,list) or len(matrix)!=3 or any(not isinstance(a,list) or len(a)!=3 for a in matrix)
                            or any(type(x) not in (float,int) or not math.isfinite(x) for a in matrix for x in a)):
                        raise ValueError('ward_camera_matrix')
                elif matrix is not None or row.get('projected_count')!=0:raise ValueError('ward_camera_unlocalized_projection')
                count,saved,omitted,error=_projection(row)
                projection_diagnostics.add(row)
                mini,terrain_reason=_terrain_projection(row)
                calibrated_reason,calibrated_points,calibrated_sample=_calibrated_minimap(row)
                if calibrated_reason is not None:
                    calibrated_counts[calibrated_reason]+=1
                if terrain_reason:terrain_counts[terrain_reason]+=1
                terrain_counts['stored_minimap_marker_observations']+=mini
                projection_counts['frames_with_points']+=int(count>0)
                projection_counts['stored_marker_observations']+=saved
                projection_counts['omitted_marker_observations']+=omitted
                if error is not None and status=='experimental_visual_reference':registration_errors.append(error)
                key=_frame_key(row)
                if key is not None:locations.setdefault(key,[]).append({'line':rows,
                    'status':'experimental_visual_reference' if mini or calibrated_points else status,
                    'points':count+max(mini,len(calibrated_points))})
                statuses[status]+=1
            if row['kind'] in {'native_camera_position', 'native_camera_orientation'}:
                from .native_camera_review import validate_position, validate_orientation
                if (d['schema_version']!=2 or context is None or row.get('projection_applied') is not False
                        or row.get('frame_id','missing') is not None
                        or row.get('capture_frame_alignment_verified') is not False):
                    raise ValueError('native_camera_event_claim')
                validator = validate_position if row['kind']=='native_camera_position' else validate_orientation
                validator(row.get('telemetry'))
                if counts['native_camera_position']+counts['native_camera_orientation']>=128:
                    raise ValueError('native_camera_event_limit')
                events = position_events if row['kind']=='native_camera_position' else orientation_events
                events.append((rows, row))
            if row['kind']=='surface':
                if type(row.get('shown')) is not bool or row.get('os_visibility_verified') is not False:
                    raise ValueError('ward_camera_surface_claim')
                surfaces['shown' if row['shown'] else 'hidden']+=1
                surface_events.append((rows,_frame_key(row),row['shown'],row.get('reason')))
            reason=row.get('location_reason',row.get('reason'))
            if isinstance(reason,str) and re.fullmatch('[a-z_]{1,80}',reason):reasons[reason]+=1
            image=row.get('image')
            if row.get('native_camera_transition_sample') is not None and (
                    row['kind']!='location' or not isinstance(image,dict)
                    or (row.get('native_camera_telemetry') or {}).get('candidate_recent') is not True):
                raise ValueError('ward_camera_transition_without_image')
            if image is not None:
                if (not isinstance(image,dict) or image.get('file') not in files
                        or image['file']=='events.jsonl' or {k:image.get(k) for k in ('bytes','sha256')}!=files[image['file']]):
                    raise ValueError('ward_camera_image_reference')
                if d['schema_version']==2:
                    expected={'format':d['image_format'],'quality':d['jpeg_quality'],'lossless':d['image_format']=='png'}
                    if (image.get('encoding')!=expected or type(image.get('width')) is not int
                            or type(image.get('height')) is not int or not 1<=image['width']<=4096 or not 1<=image['height']<=2160
                            or row['kind']=='location' and (image['width'],image['height'])!=(row['width'],row['height'])):
                        raise ValueError('ward_camera_image_encoding_or_geometry')
                    encodings[d['image_format']]+=1
                else:encodings['legacy_v1_png_unspecified']+=1
                image_refs.add(image['file'])
            _record_alt_motion(alt_motion_groups,row,d['schema_version'])
            if row['kind']=='location':
                native_frames['location_frames']+=1
                ground_pose.add(row,at.timestamp(),provider.timestamp())
                patch_sample=_terrain_patch_sample(row)
                if patch_sample is not None and image is not None:
                    scope=tuple(context.get(k) for k in ('match_id','game_session_id','record_session_id'))
                    terrain_patch_images.append(
                        ((*scope,row['reference_epoch'],row['width'],row['height']),
                         image['file'],patch_sample))
                    if row.get('alt_motion_window') is not None:
                        alt_motion_paints[image['file']]=(
                            (*scope,row['reference_epoch'],row.get('reference_packet_sha256')),patch_sample)
                if calibrated_sample is not None:
                    calibrated_counts['observed_shown_frames' if calibrated_sample['shown'] else 'observed_hidden_frames']+=1
                    if image is not None:
                        key=tuple(context.get(k) for k in ('match_id','game_session_id','record_session_id'))
                        calibrated_images.append({'shown':calibrated_sample['shown'],'captured':row['captured_monotonic'],
                            'changed':calibrated_sample['changed_monotonic'],
                            'file':image['file'],'key':(*key,row['reference_epoch'],row['reference_packet_sha256'],
                                row['width'],row['height'],calibrated_points)})
                native_snapshot=row.get('native_camera_telemetry') or {}
                wake = row.get('native_camera_wake_sample', False)
                if type(wake) is not bool or (wake and native_snapshot.get('candidate_recent') is not True):
                    raise ValueError('ward_camera_wake_sample_metadata')
                if wake:
                    native_frames['native_wake_location_frames']+=1
                    native_frames['native_wake_images_saved']+=int(image is not None)
                if native_snapshot.get('candidate_recent') is True:
                    native_frames['frames_with_native_candidate']+=1
                    if image is not None:
                        native_frames['candidate_frames_with_image_bytes_verified']+=1
                        native_images.append((rows, row))
            counts[row['kind']]+=1
    if type(d.get('written')) is not int or rows!=d['written'] or d.get('images_saved')!=len(files)-1:
        raise ValueError('ward_camera_declared_counts')
    if d['complete'] and any(d.get(k) for k in ('dropped','limited','shutdown_timed_out')):
        raise ValueError('ward_camera_complete_with_loss')
    minimap_pairs = _calibrated_minimap_pairs(calibrated_images)
    pixel_comparisons = []
    if analyze_minimap_pixels:
        from .ward_minimap_pixels import compare, read_rgb
        retained = {frame['file']:frame for frame in calibrated_images}
        for pair in minimap_pairs[:12]:
            a, b = (pair[k] for k in ('shown_image','hidden_image'))
            width, height, points = retained[a]['key'][-3:]
            if len(points) <= 512:
                try:
                    result = compare(read_rgb(paths[a], (width,height)), read_rgb(paths[b], (width,height)), points)
                except (OSError, ValueError):
                    result = {'status': 'composited_images_not_decodable_at_declared_size'}
            else:
                result = {'status':'marker_comparison_budget'}
            pixel_comparisons.append({**pair,**result})
    pixel_results_by_transition = {}
    for pair in pixel_comparisons:
        pixel_results_by_transition.setdefault(pair['transition'], Counter())[pair['status']] += 1
    ground_controls = ground_pose.summary()
    ring_reviews = []
    if analyze_ground_ring_pixels:
        from .ward_minimap_pixels import read_rgb
        from .ward_partial_ring import locate
        for item in ground_controls['bracketed_same_position'][:16]:
            try:
                pixels = read_rgb(paths[item['image']],item['viewport'])
                result = locate(pixels,item['screen_xy_candidate'])
            except (OSError,ValueError):
                result = {'status':'ring_image_not_decodable','candidate':None}
            ring_reviews.append({'image':item['image'],**result})
    ground_controls['partial_ring_pixel_reviews'] = ring_reviews
    ground_controls['partial_ring_pixel_review_enabled'] = analyze_ground_ring_pixels
    terrain_pairs = _terrain_patch_pairs(terrain_patch_images)
    terrain_pixel_comparisons = []
    if analyze_terrain_patch_pixels:
        from .ward_minimap_pixels import read_rgb
        from .ward_terrain_pixel_review import compare
        retained = {filename:(scope[-2:],sample) for scope,filename,sample in terrain_patch_images}
        for pair in terrain_pairs[:6]:
            a,b = pair['images']
            geometry,old = retained[a]
            _,new = retained[b]
            try:
                result = compare(read_rgb(paths[a],geometry),read_rgb(paths[b],geometry),old,new)
            except (OSError,ValueError):
                result = {'status':'terrain_patch_image_not_decodable'}
            terrain_pixel_comparisons.append({**pair,**result})
    alt_motion_summary=_alt_motion_summary(alt_motion_groups,
        paths if analyze_alt_motion_pixels else None,alt_motion_paints)
    # Revalidate after inspection; active or rewritten inputs cannot publish a successful report.
    if (folder/'CLOSED.json').read_bytes()!=header or session.read_bytes()!=header:
        raise ValueError('ward_camera_header_changed')
    for name,p in paths.items():
        p=_file(folder,name)
        if p.stat().st_size!=files[name]['bytes'] or _digest(p)!=files[name]['sha256']:
            raise ValueError('ward_camera_source_changed')
    from .native_camera_review import summary as native_summary
    return {'run_id':d['run_id'],'package_id':hashlib.sha256(header).hexdigest(),
        'native_camera_summary':native_summary(counts['native_camera_position'], counts['native_camera_orientation']),
        'alt_motion_sampling':alt_motion_summary,
        'installed_map_evidence':d.get('installed_map_evidence'),
        'native_calibration_readiness':{'counts':dict(native_frames),
            'image_log_linkage': _native_image_links(native_images, position_events, orientation_events),
            'position_transition_pairs':_native_transition_links(
                native_images,position_events,d.get('native_camera_transition_limit',0)),
            'explicit_3d_landmarks_available':False,'capture_frame_alignment_verified':False,
            'calibration_ready':False,'required':['explicit_3d_landmark_to_pixel_correspondences',
                'independent_fit_and_check_points','camera_to_frame_alignment_evidence']},
        'state':'closed_verified' if d['complete'] else 'closed_verified_with_gaps',
        'contexts':d['contexts'],'events':rows,'event_counts':dict(counts),'location_status_counts':dict(statuses),
        'terrain_registration_counts':dict(terrain_counts),
        'ground_control_candidates':ground_controls,
        'terrain_patch':{'retained_surface_frames':len(terrain_patch_images),
            'candidate_pairs':terrain_pairs,'pixel_comparisons':terrain_pixel_comparisons,
            'pixel_comparison_enabled':analyze_terrain_patch_pixels,
            'pixel_visibility_verified':False,'world_alignment_verified':False},
        'calibrated_minimap':{'counts':dict(calibrated_counts),'retained_surface_frames':len(calibrated_images),
            'candidate_composited_pairs':minimap_pairs,'pixel_comparisons':pixel_comparisons,
            'pixel_comparison_enabled':analyze_minimap_pixels,'pixel_results_by_transition':pixel_results_by_transition,
            'pixel_visibility_verified':False,'ward_alignment_verified':False},
        'reason_counts':dict(reasons),'surface_counts':dict(surfaces),'image_files_verified':len(files)-1,
        'surface_linkage':_surface_links(locations,surface_events),
        'projection_summary':dict(projection_counts),
        'projection_diagnostics':projection_diagnostics.summary(),
        'registration_error_px':{'reported_samples':len(registration_errors),
            'median':statistics.median(registration_errors) if registration_errors else None,
            'maximum':max(registration_errors) if registration_errors else None,
            'meaning':'reported_image_registration_residual_not_world_coordinate_error'},
        'referenced_images':len(image_refs),'dropped':d.get('dropped'),'limited':d.get('limited'),
        'coordinate_accuracy_verified':False,'os_visibility_verified':False,
        'image_pixels_decoded':False,'declared_image_encoding_counts':dict(encodings),
        'image_interval_s':d.get('image_interval_s'),'schema_version':d['schema_version'],
        'declared_image_sampling_policy':d.get('image_sampling_policy'),
        'declared_image_window_period_s':d.get('image_window_period_s'),
        'declared_image_window_planned_count':d.get('image_window_planned_count'),
        'declared_image_sampling_extensions':d.get('image_sampling_extensions',[]),
        'declared_native_camera_image_limit':d.get('native_camera_image_limit',0),
        'declared_native_camera_image_interval_s':d.get('native_camera_image_interval_s'),
        'native_frame_pixel_hash_verified':False,'time_basis':'original_capture_utc_and_relay_provider_not_index_time'}


def _unpublished_session(run):
    """Read only public session metadata; neither counts nor process liveness are certified."""
    d,raw,_scopes=_header(run,name='session.json',require_closed=False)
    if (run/'CLOSED.json').exists():state='receipt_appeared_resync_required'
    elif d.get('error_type') is not None:
        state='failed_closed_without_receipt' if d['closed'] else 'failure_reported_unclosed'
    else:state='closed_without_receipt' if d['closed'] else 'unclosed_liveness_unknown'
    return {'run_id':d['run_id'],'state':state,'writer_closed_claim':d['closed'],
        'error_type':d.get('error_type'),'metadata_sha256':hashlib.sha256(raw).hexdigest(),
        'evidence_payload_read':False,'process_liveness_verified':False,
        'event_or_image_counts_verified':False}


def discover_and_review(roots,latest=5, *, target_map_sha256=None, analyze_minimap_pixels=False,
                        analyze_ground_ring_pixels=False, analyze_terrain_patch_pixels=False,
                        analyze_alt_motion_pixels=False):
    if type(latest) is not int or not 1<=latest<=25:raise ValueError('ward_camera_scan_limit')
    if target_map_sha256 is not None:
        installed_map_relation(None, target_map_sha256)
    candidates=[];pending=[]
    for key,root,base in history_bases(roots):
        for run in (base/'ward-camera').glob('run_*'):
            if len(candidates)+len(pending)>=2000:raise ValueError('ward_camera_discovery_budget')
            if not re.fullmatch(r'run_[a-f0-9]{32}',run.name):continue
            if not (run/'CLOSED.json').is_file():
                try:stamp=(run/'session.json').lstat().st_mtime_ns
                except OSError:stamp=0
                pending.append((stamp,key,run,root));continue
            candidates.append(((run/'CLOSED.json').stat().st_mtime_ns,key,run,root))
    chosen=sorted(candidates,key=lambda r:(-r[0],str(r[2])))[:latest]
    reports=[];skipped=[];groups={};unpublished=[]
    for _,key,run,root in sorted(pending,key=lambda r:(-r[0],str(r[2])))[:latest]:
        reference={'root_key':key,'source':run.relative_to(root).as_posix()}
        try:unpublished.append({**reference,**_unpublished_session(run)})
        except (OSError,ValueError,TypeError,KeyError) as exc:
            unpublished.append({**reference,'run_id':run.name[4:],'state':'metadata_not_admitted',
                'error_type':type(exc).__name__,'evidence_payload_read':False,'process_liveness_verified':False})
    # Resolve conflicting closed versions before reading either event stream.
    for _,key,run,root in chosen:
        try:
            d,raw,_scopes=_header(run);identity=d['run_id'];digest=hashlib.sha256(raw).hexdigest()
            groups.setdefault(identity,[]).append((key,run,root,digest))
        except (OSError,ValueError,TypeError,KeyError) as exc:
            skipped.append({'state':'not_admitted','error_type':type(exc).__name__,'source':run.relative_to(root).as_posix(),'root_key':key})
    for identity,group in groups.items():
        if len({row[3] for row in group})!=1:
            skipped.append({'state':'conflicting_closed_versions','run_id':identity});continue
        key,run,root,_=group[0]
        try:reports.append({**review(run, analyze_minimap_pixels=analyze_minimap_pixels,
                                     analyze_ground_ring_pixels=analyze_ground_ring_pixels,
                                     analyze_terrain_patch_pixels=analyze_terrain_patch_pixels,
                                     analyze_alt_motion_pixels=analyze_alt_motion_pixels),
            'source':run.relative_to(root).as_posix(),'root_key':key,'duplicate_copies_omitted':len(group)-1})
        except (OSError,ValueError,TypeError,KeyError) as exc:
            skipped.append({'state':'not_admitted','run_id':identity,'error_type':type(exc).__name__})
    result = {'reports':reports,'skipped':skipped,'not_closed_not_read':len(pending),
            'unpublished_sessions':unpublished,'unpublished_metadata_omitted':max(0,len(pending)-latest),
            'state':'reviewed_closed_sources' if candidates else 'no_closed_recording_found_not_zero_accuracy'}
    if target_map_sha256 is not None:
        for report in reports:
            report['installed_map_relation'] = installed_map_relation(report.get('installed_map_evidence'), target_map_sha256)
        result['target_map_review'] = {'map_asset_sha256': target_map_sha256,
            'run_counts': dict(Counter(report['installed_map_relation'] for report in reports)),
            'scope': 'reviewed_closed_runs_disk_boundaries_only', 'capture_map_binding_verified': False}
    return result


def render_markdown(result):
    lines=['','## 眼位镜头自动留证','','落点精度仍待验证。Alt 核对结合留证回执和可配对的桌面像素。',
        f"无闭合收据、正文未读：{result['not_closed_not_read']}；拒收/冲突：{len(result['skipped'])}。",'',
        '|记录|状态|事件|图片|定位状态|','|---|---|---:|---:|---|']
    for row in result['reports']:
        lines.append(f"|{row['run_id']}|{row['state']}|{row['events']}|{row['image_files_verified']}|{row['location_status_counts']}|")
    controls = [row.get('ground_control_candidates',{}) for row in result['reports']]
    motion=[window for row in result['reports'] for window in row.get('alt_motion_sampling',{}).get('windows',[])]
    if motion:
        complete=sum(window['complete'] for window in motion)
        candidates=sum(window.get('pixel_comparison',{}).get('state')=='view_return_candidate' for window in motion)
        lines.extend(['',f'Alt 相邻截图完整 {complete}/{len(motion)} 组，视觉切回候选 {candidates} 组。世界坐标对齐待核对。'])
        attachments=Counter(window['return_attachment']['status'] for window in motion if 'return_attachment' in window)
        if attachments:
            attached=attachments['relative_attachment_observed']
            drifted=attachments['painted_marker_terrain_disagreement']
            lines.append(f'切回后有 {attached} 组确认图标随地形对齐，{drifted} 组图标偏移，其余证据不足。')
    checked = sum(len(row.get('partial_ring_pixel_reviews',[])) for row in controls)
    if checked:
        detected = sum(result['status']=='partial_ring_candidate' for row in controls
                       for result in row.get('partial_ring_pixel_reviews',[]))
        lines.extend(['',f'已核对 {checked} 张前后坐标一致的画面，找到 {detected} 个可见白圈候选。'])
    terrain_reviews = [pair for row in result['reports']
                       for pair in row.get('terrain_patch',{}).get('pixel_comparisons',[])]
    if terrain_reviews:
        aligned = sum(pair['status']=='relative_attachment_observed' for pair in terrain_reviews)
        drifted = sum(pair['status']=='painted_marker_terrain_disagreement' for pair in terrain_reviews)
        lines.extend(['',f'核对 {len(terrain_reviews)} 组切镜画面，其中 {aligned} 组标记与地形相对位移一致，{drifted} 组有偏差。'])
    target = result.get('target_map_review')
    if target is not None:
        counts = target['run_counts']
        matching = counts.get('both_boundaries_match_target', 0)
        different = counts.get('both_boundaries_differ_from_target', 0)
        unknown = sum(counts.values()) - matching - different
        lines.extend(['', f"目标地图 {target['map_asset_sha256']} 的磁盘指纹核对。"
            f"两端相同 {matching} 段，两端不同 {different} 段，变化或证据不足 {unknown} 段。"
            '截图与运行中地图的对应关系仍待核验。'])
    for row in result['reports']:
        linkage=row['surface_linkage']
        diagnostic=row['projection_diagnostics'];windows=diagnostic['sampled_windows']
        native_links=row.get('native_calibration_readiness',{}).get('image_log_linkage',{}).get('counts',{})
        minimap = row.get('calibrated_minimap', {})
        comparisons = minimap.get('pixel_results_by_transition', {})
        status = 'distributed_marker_color_pattern_observed'
        presses, releases = (comparisons.get(key, {}).get(status, 0) for key in ('press', 'release'))
        pairs = len(minimap.get('candidate_composited_pairs', []))
        if minimap.get('pixel_comparison_enabled'):
            alt_summary = (f'Alt 相邻截图 {pairs} 对，已比较 {len(minimap["pixel_comparisons"])} 对。'
                           f'按下 {presses} 对、松开 {releases} 对观察到预期的小地图标记像素变化。')
        else:
            alt_summary = f'Alt 相邻截图 {pairs} 对，本报告仅核验了留证文件。'
        lines.extend(['',f"### 记录 {row['run_id']}",'',f"定位→Tk回执严格配对：{linkage['matched_prior_location']}；未知：{linkage['unknown_reasons']}；矛盾：{linkage['contradictions']}。",
            alt_summary,
            f"Alt原生镜头位置事件：{row.get('native_camera_summary',{}).get('position_events',0)}；仅含镜头XYZ/目标XY，缺目标高度及FOV，不认证同帧、完整相机或贴地精度。",
            f"已保存截图与镜头日志回执：{native_links}。本机单调时钟只说明日志何时可读，镜头采样时刻仍待核验。",
            f"安装目录地图文件状态：{(row.get('installed_map_evidence') or {}).get('status','未记录')}。运行时地图对应关系仍待核验。",
            f"投影记录：{row['projection_summary']}；配准残差（非真实地图误差）：{row['registration_error_px']}。",
            f"投影确认/隐藏原因：{diagnostic['reason_counts']}；本人候选锚点偏差（非独立真值）：{diagnostic['self_anchor_disagreement_px']}。",
            f"三维镜头候选诊断：{diagnostic['plane_camera_diagnostics']['reason_counts']}；只在明确底图镜头且未推断轮廓时尝试，数学候选不认证实际镜头。",
            f"三边＋本人锚点候选：{diagnostic['anchor_plane_diagnostics']['reason_counts']}；其相机约束结果：{diagnostic['anchor_plane_diagnostics']['camera_recovery_counts']}。拟合锚点误差不等于定位准确率，当前未用于显示。",
            f"连续取样窗口：{windows['window_count']}；完整三帧/不完整：{windows['counts']}；无法比较原因：{windows['unmeasured_pair_reasons']}。",
            f"扣除背景运动后原始模型偏差：{windows['raw_model_motion_compensated_residual_px']}；补偿后显示偏差：{windows['motion_compensated_residual_px']}。补偿结果受算法约束，不能当作独立地图精度；空值表示未测得。",
            f"图片编码声明：{row['declared_image_encoding_counts']}；JPEG有损、原尺寸保留，未据图片字节SHA认证原始像素。",''])
    unpublished=result.get('unpublished_sessions',[])
    if unpublished:
        lines.extend(['','### 未发布记录的状态元数据','','以下均未读取事件/图片正文，不能据此判断进程存活或记作零数据。','',
            '|记录|状态|安全错误类型|','|---|---|---|'])
        labels={'failed_closed_without_receipt':'记录器声明失败并已收尾，缺交接收据',
            'failure_reported_unclosed':'记录器声明失败，尚未收尾',
            'closed_without_receipt':'已声明收尾但缺交接收据',
            'unclosed_liveness_unknown':'尚未收尾，进程状态未知',
            'metadata_not_admitted':'状态元数据未准入',
            'receipt_appeared_resync_required':'收据刚发布，待重新同步'}
        for row in unpublished:
            lines.append(f"|{row['run_id']}|{labels.get(row['state'],'状态未知')}|{row.get('error_type') or '无可用错误声明'}|")
        lines.extend(['',f"其余状态元数据未检查：{result.get('unpublished_metadata_omitted',0)}段。"])
    return '\n'.join(lines)+'\n'
