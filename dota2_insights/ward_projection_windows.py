"""Strictly linked sampled windows, not inferred continuous gameplay coverage."""
from collections import Counter
import math
import re
import numpy as np


def _project(matrix,point):
    p=np.asarray(matrix,float) @ point
    if not np.isfinite(p).all() or p[2]<=1e-9:
        raise ValueError('projection_invalid_depth')
    result=p[:2]/p[2]
    if not np.isfinite(result).all():raise ValueError('projection_nonfinite')
    return result


def distribution(values):
    ordered=sorted(values)
    n=len(ordered)
    return {'samples':n,'median':(ordered[n//2] if n%2 else (ordered[n//2-1]+ordered[n//2])/2) if n else None,
            'p95':ordered[math.ceil(.95*n)-1] if n else None,'maximum':ordered[-1] if n else None}


class ProjectionWindows:
    def __init__(self):
        self.windows={}
        self.missing=0

    def add(self,row):
        window=row.get('sampling_window')
        if window is None:
            self.missing+=1
            return
        if (not isinstance(window,dict) or window.get('version')!='ward_projection_window_v1'
                or not isinstance(window.get('id'),str) or not re.fullmatch('[a-f0-9]{32}',window['id'])
                or type(window.get('index')) is not int or not 0<=window['index']<=2
                or type(window.get('planned_count')) is not int or window['planned_count']!=3
                or type(window.get('period_s')) not in (int,float) or window['period_s']!=15
                or type(window.get('max_gap_s')) not in (int,float) or window['max_gap_s']!=1.2):
            raise ValueError('ward_projection_window_metadata')
        at=row.get('captured_monotonic');frame=row.get('frame_id')
        if type(at) not in (int,float) or not math.isfinite(at) or not isinstance(frame,str) or not frame:
            raise ValueError('ward_projection_window_frame')
        context=row.get('context') or {}
        scope=tuple(context.get(k) for k in ('match_id','game_session_id','record_session_id'))
        # Reference, client size, and projection method must not silently change mid-pair.
        scope+=(row.get('reference_epoch'),row.get('reference_packet_sha256'),row.get('width'),row.get('height'),row.get('camera_method'))
        group=self.windows.setdefault(window['id'],{})
        if window['index'] in group:
            raise ValueError('ward_projection_window_duplicate_index')
        group[window['index']]={'at':at,'frame':frame,'scope':scope,
            'points':{p['id']:(p['x'],p['y']) for p in row.get('projected_markers',[])},
            'applied_matrix':row.get('world_to_screen'),
            'stability':row.get('projection_stability') or {}}

    def summary(self):
        counts=Counter();skipped=Counter();movement=[];residual=[];raw_residual=[]
        examples=[]
        for identity,group in self.windows.items():
            complete=set(group)=={0,1,2}
            if complete:
                complete=(len({v['frame'] for v in group.values()})==3
                          and all(group[k]['scope']==group[0]['scope'] for k in (1,2))
                          and all(0<group[k]['at']-group[k-1]['at']<=1.2 for k in (1,2)))
            counts['complete_three_frame_windows' if complete else 'incomplete_or_discontinuous_windows']+=1
            for k in (1,2):
                if k not in group or k-1 not in group:
                    continue
                previous,current=group[k-1],group[k]
                if (previous['scope']!=current['scope'] or previous['frame']==current['frame']
                        or not 0<current['at']-previous['at']<=1.2):
                    skipped['scope_or_capture_discontinuity']+=1
                    continue
                diagnostic=current['stability']
                if (diagnostic.get('accepted') is not True
                        or diagnostic.get('previous_captured_monotonic')!=previous['at']):
                    skipped['missing_exact_previous_transport']+=1
                    continue
                transform=diagnostic.get('previous_to_current')
                # Already checked by the projection diagnostic admission layer.
                if not isinstance(transform,list) or len(transform)!=3:
                    skipped['missing_transport_matrix']+=1
                    continue
                common=previous['points'].keys() & current['points'].keys()
                if not common:
                    skipped['no_shared_visible_main_marker']+=1
                    continue
                counts['pairs_with_shared_markers']+=1
                for marker in sorted(common):
                    x,y=previous['points'][marker];cx,cy=current['points'][marker]
                    p=[sum(a*b for a,b in zip(v,[x,y,1])) for v in transform]
                    if not all(math.isfinite(v) for v in p) or p[2]<=1e-9:
                        skipped['invalid_transported_marker']+=1
                        continue
                    delta=math.hypot(cx-x,cy-y)
                    error=math.hypot(cx-p[0]/p[2],cy-p[1]/p[2])
                    if not math.isfinite(error):
                        skipped['invalid_transported_marker']+=1
                        continue
                    movement.append(delta);residual.append(error)
                    raw_error=None
                    try:
                        world=np.linalg.solve(np.asarray(previous['applied_matrix'],float),[x,y,1.])
                        if not np.isfinite(world).all() or abs(world[2])<1e-9:raise ValueError('raw_world_invalid')
                        world=world/world[2]
                        before=_project(previous['stability']['raw_world_to_screen'],world)
                        after=_project(diagnostic['raw_world_to_screen'],world)
                        expected=_project(transform,[*before,1.])
                        raw_error=float(np.linalg.norm(after-expected))
                        if not math.isfinite(raw_error):raise ValueError('raw_nonfinite')
                        raw_residual.append(raw_error)
                    except (ValueError,TypeError,KeyError,np.linalg.LinAlgError):
                        skipped['raw_model_comparison_unavailable']+=1
                    if len(examples)<10:
                        examples.append({'window_id':identity,'index':k,'marker_id':marker,
                            'previous_frame_id':previous['frame'],'frame_id':current['frame'],
                            'screen_movement_px':delta,'motion_compensated_residual_px':error,
                            'raw_model_motion_compensated_residual_px':raw_error})
        return {'window_count':len(self.windows),'frames_without_window':self.missing,
                'counts':dict(counts),'unmeasured_pair_reasons':dict(skipped),
                'screen_movement_px':distribution(movement),
                'motion_compensated_residual_px':distribution(residual),'examples':examples,
                'raw_model_motion_compensated_residual_px':distribution(raw_residual),
                'meaning':'sampled_main_marker_consistency_after_measured_background_motion',
                'caveat':'applied_transport_is_algorithm_dependent; neither_metric_is_independent_ground_truth',
                'continuous_gameplay_coverage_verified':False,'coordinate_accuracy_verified':False}
