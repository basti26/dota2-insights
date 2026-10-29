"""Read closed automatic camera evidence; no images decoded or map truth invented."""
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

MAX_BYTES=128*1024**2
KINDS={'location','surface','waiting','capture_error','surface_error','capture_unavailable','reference_added'}


def _digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def _file(folder,name):
    if not (name=='events.jsonl' or re.fullmatch(r'frame-[0-9]{4}\.(?:png|jpg)',name)):
        raise ValueError('ward_camera_file_name')
    p=folder/name
    if p.is_symlink() or p.is_junction() or not p.resolve(strict=True).is_relative_to(folder.resolve(strict=True)):
        raise ValueError('ward_camera_link_or_escape')
    return p


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


def review(folder):
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
    locations={};surface_events=[];projection_counts=Counter();registration_errors=[];encodings=Counter()
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
                projection_counts['frames_with_points']+=int(count>0)
                projection_counts['stored_marker_observations']+=saved
                projection_counts['omitted_marker_observations']+=omitted
                if error is not None and status=='experimental_visual_reference':registration_errors.append(error)
                key=_frame_key(row)
                if key is not None:locations.setdefault(key,[]).append({'line':rows,'status':status,'points':count})
                statuses[status]+=1
            if row['kind']=='surface':
                if type(row.get('shown')) is not bool or row.get('os_visibility_verified') is not False:
                    raise ValueError('ward_camera_surface_claim')
                surfaces['shown' if row['shown'] else 'hidden']+=1
                surface_events.append((rows,_frame_key(row),row['shown'],row.get('reason')))
            reason=row.get('location_reason',row.get('reason'))
            if isinstance(reason,str) and re.fullmatch('[a-z_]{1,80}',reason):reasons[reason]+=1
            image=row.get('image')
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
            counts[row['kind']]+=1
    if type(d.get('written')) is not int or rows!=d['written'] or d.get('images_saved')!=len(files)-1:
        raise ValueError('ward_camera_declared_counts')
    if d['complete'] and any(d.get(k) for k in ('dropped','limited','shutdown_timed_out')):
        raise ValueError('ward_camera_complete_with_loss')
    # Revalidate after inspection; active or rewritten inputs cannot publish a successful report.
    if (folder/'CLOSED.json').read_bytes()!=header or session.read_bytes()!=header:
        raise ValueError('ward_camera_header_changed')
    for name,p in paths.items():
        p=_file(folder,name)
        if p.stat().st_size!=files[name]['bytes'] or _digest(p)!=files[name]['sha256']:
            raise ValueError('ward_camera_source_changed')
    return {'run_id':d['run_id'],'package_id':hashlib.sha256(header).hexdigest(),
        'state':'closed_verified' if d['complete'] else 'closed_verified_with_gaps',
        'contexts':d['contexts'],'events':rows,'event_counts':dict(counts),'location_status_counts':dict(statuses),
        'reason_counts':dict(reasons),'surface_counts':dict(surfaces),'image_files_verified':len(files)-1,
        'surface_linkage':_surface_links(locations,surface_events),
        'projection_summary':dict(projection_counts),
        'registration_error_px':{'reported_samples':len(registration_errors),
            'median':statistics.median(registration_errors) if registration_errors else None,
            'maximum':max(registration_errors) if registration_errors else None,
            'meaning':'reported_image_registration_residual_not_world_coordinate_error'},
        'referenced_images':len(image_refs),'dropped':d.get('dropped'),'limited':d.get('limited'),
        'coordinate_accuracy_verified':False,'os_visibility_verified':False,
        'image_pixels_decoded':False,'declared_image_encoding_counts':dict(encodings),
        'image_interval_s':d.get('image_interval_s'),'schema_version':d['schema_version'],
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


def discover_and_review(roots,latest=5):
    if type(latest) is not int or not 1<=latest<=25:raise ValueError('ward_camera_scan_limit')
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
        try:reports.append({**review(run),'source':run.relative_to(root).as_posix(),'root_key':key,'duplicate_copies_omitted':len(group)-1})
        except (OSError,ValueError,TypeError,KeyError) as exc:
            skipped.append({'state':'not_admitted','run_id':identity,'error_type':type(exc).__name__})
    return {'reports':reports,'skipped':skipped,'not_closed_not_read':len(pending),
            'unpublished_sessions':unpublished,'unpublished_metadata_omitted':max(0,len(pending)-latest),
            'state':'reviewed_closed_sources' if candidates else 'no_closed_recording_found_not_zero_accuracy'}


def render_markdown(result):
    lines=['','## 眼位镜头自动留证','','记录不等于地图落点正确；Tk应用标志不认证OS可见性，图片只核验字节。',
        f"无闭合收据、正文未读：{result['not_closed_not_read']}；拒收/冲突：{len(result['skipped'])}。",'',
        '|记录|状态|事件|图片|定位状态|','|---|---|---:|---:|---|']
    for row in result['reports']:
        lines.append(f"|{row['run_id']}|{row['state']}|{row['events']}|{row['image_files_verified']}|{row['location_status_counts']}|")
    for row in result['reports']:
        linkage=row['surface_linkage']
        lines.extend(['',f"### 记录 {row['run_id']}",'',f"定位→Tk回执严格配对：{linkage['matched_prior_location']}；未知：{linkage['unknown_reasons']}；矛盾：{linkage['contradictions']}。",
            f"投影记录：{row['projection_summary']}；配准残差（非真实地图误差）：{row['registration_error_px']}。",
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
