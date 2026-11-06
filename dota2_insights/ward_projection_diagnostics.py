"""Diagnostics for admitted ward frames; neither screen truth nor release approval."""
from collections import Counter
import math
import statistics
import re
from .ward_projection_windows import ProjectionWindows
from .ward_plane_camera import recover as recover_plane_camera


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _matrix(value):
    return (isinstance(value, list) and len(value) == 3
            and all(isinstance(row, list) and len(row) == 3
                    and all(_number(v) for v in row) for row in value))


def _error(matrix, world, anchor):
    p = [sum(a*b for a, b in zip(row, [*world, 1])) for row in matrix]
    if not all(math.isfinite(v) for v in p) or p[2] <= 1e-9:
        return None
    error=math.hypot(p[0]/p[2]-anchor['x'], p[1]/p[2]-anchor['y'])
    return error if math.isfinite(error) else None


class ProjectionDiagnostics:
    def __init__(self):
        self.counts = Counter()
        self.reasons = Counter()
        self.corrections = []
        self.errors = {}
        self.windows = ProjectionWindows()
        self.plane_camera_counts=Counter();self.plane_camera_examples=[]
        self.anchor_plane_counts=Counter();self.anchor_camera_counts=Counter();self.anchor_plane_examples=[]

    def add(self, row):
        self.counts['location_frames'] += 1
        self.windows.add(row)
        trial=row.get('anchor_plane_candidate')
        if trial is not None:
            if (not isinstance(trial,dict) or trial.get('version')!='ward_three_edge_self_anchor_v1'
                    or trial.get('status') not in ('unavailable','conditional_candidate')
                    or not isinstance(trial.get('reason'),str) or not re.fullmatch('[a-z_]{1,80}',trial['reason'])
                    or any(trial.get(k,False) is not False for k in ('coordinate_accuracy_verified','full_viewport_verified','coplanarity_verified'))):
                raise ValueError('ward_anchor_plane_metadata')
            self.anchor_plane_counts[trial['reason']]+=1
            candidate_matrix=trial.get('raw_world_to_screen')
            if trial['status']=='conditional_candidate':
                world,screen=trial.get('source_world_anchor'),trial.get('source_screen_anchor')
                own=row.get('self_world_xy');anchor=row.get('self_screen_anchor') or {}
                pose=(row.get('context') or {}).get('own_hero_pose') or {}
                age=row.get('self_position_age_s')
                residual=trial.get('anchor_fit_residual_px')
                if (not _matrix(candidate_matrix) or not isinstance(world,list) or len(world)!=2
                        or not isinstance(screen,list) or len(screen)!=2
                        or not all(_number(v) for v in world+screen)
                        or not isinstance(own,list) or len(own)!=2 or not all(_number(v) for v in own)
                        or world!=[v+16384 for v in own] or screen!=[anchor.get('x'),anchor.get('y')]
                        or pose.get('coordinate_basis')!='gsi_centered_world_xy' or pose.get('alive') is not True
                        or own!=[pose.get('xpos'),pose.get('ypos')] or not _number(age) or not -1<=age<=2.5
                        or not _number(residual) or residual<0
                        or trial.get('residual_is_training_fit_not_accuracy') is not True):
                    raise ValueError('ward_anchor_plane_fit_source')
                actual=_error(candidate_matrix,world,{'x':screen[0],'y':screen[1]})
                if actual is None or actual>.01 or abs(actual-residual)>.01:
                    raise ValueError('ward_anchor_plane_fit_residual')
                camera=recover_plane_camera(candidate_matrix,(row.get('width'),row.get('height')))
                self.anchor_camera_counts[camera['reason']]+=1
                if len(self.anchor_plane_examples)<8:
                    self.anchor_plane_examples.append({'frame_id':row.get('frame_id'),'fit_residual_px':actual,
                        'camera_recovery_reason':camera['reason'],'camera_model_verified':False})
            elif candidate_matrix is not None:raise ValueError('ward_anchor_plane_unavailable_matrix')
        if row.get('world_to_screen') is not None:
            if row.get('camera_method')!='ward_terrain_trial_v1' or row.get('camera_geometry_inferred') is not False:
                self.plane_camera_counts['source_not_explicit_metric_terrain_camera']+=1
            else:
                camera=recover_plane_camera(row['world_to_screen'],(row.get('width'),row.get('height')))
                self.plane_camera_counts[camera['reason']]+=1
                if camera['status']=='conditional_candidate' and len(self.plane_camera_examples)<8:
                    self.plane_camera_examples.append({'frame_id':row.get('frame_id'),**camera})
        stability = row.get('projection_stability')
        if stability is None:
            self.counts['legacy_without_stability'] += 1
        else:
            if (not isinstance(stability, dict)
                    or stability.get('version') != 'ward_motion_confirmation_v1'
                    or type(stability.get('accepted')) is not bool
                    or stability.get('coordinate_accuracy_verified') is not False):
                raise ValueError('ward_projection_stability_metadata')
            reason = stability.get('reason')
            allowed = {'projection_missing', 'projection_confirming', 'projection_method_confirming',
                       'projection_camera_reconfirming', 'projection_jump_confirming',
                       'projection_lower_tier_hidden', 'projection_waiting_for_alt',
                       'motion_compensated_projection'}
            if reason not in allowed:
                raise ValueError('ward_projection_stability_reason')
            self.reasons[reason] += 1
            accepted = stability['accepted']
            if (accepted != (reason == 'motion_compensated_projection')
                    or accepted != (row.get('world_to_screen') is not None)):
                raise ValueError('ward_projection_stability_claim')
            raw = stability.get('raw_world_to_screen')
            if raw is not None and not _matrix(raw):
                raise ValueError('ward_projection_stability_matrix')
            correction = stability.get('correction_max_px')
            if correction is not None:
                if not _number(correction) or correction < 0:
                    raise ValueError('ward_projection_stability_correction')
                self.corrections.append(correction)
            if accepted and (raw is None or not _matrix(stability.get('previous_to_current'))
                             or correction is None):
                raise ValueError('ward_projection_stability_accepted_evidence')
            self.counts['accepted' if accepted else 'hidden'] += 1

        # Same admitted frame, explicit centered GSI basis. No nearest-time join.
        context = row.get('context') or {}
        pose = context.get('own_hero_pose') or {}
        xy, anchor = row.get('self_world_xy'), row.get('self_screen_anchor')
        age = row.get('self_position_age_s')
        if (not isinstance(xy, list) or len(xy) != 2 or not all(_number(v) for v in xy)
                or not isinstance(anchor, dict) or not all(_number(anchor.get(k)) for k in ('x', 'y'))
                or not _number(age) or not -1 <= age <= 2.5
                or pose.get('coordinate_basis') != 'gsi_centered_world_xy'
                or pose.get('alive') is not True or xy != [pose.get('xpos'), pose.get('ypos')]):
            self.counts['anchor_comparison_unavailable'] += 1
            return
        world = [v+16384 for v in xy]
        independent = row.get('camera_method') == 'ward_terrain_trial_v1'
        group = 'terrain_vs_visual_anchor_candidate' if independent else 'anchor_dependent_not_accuracy'
        for label, matrix in [('applied', row.get('world_to_screen')),
                              ('raw', (stability or {}).get('raw_world_to_screen'))]:
            if matrix is None:
                continue
            if not _matrix(matrix):
                raise ValueError('ward_projection_anchor_matrix')
            error = _error(matrix, world, anchor)
            if error is None:
                self.counts['anchor_projection_invalid_or_behind_camera'] += 1
                continue
            key = group + ':' + label
            self.errors.setdefault(key, []).append(error)

    def summary(self):
        def distribution(values):
            ordered = sorted(values)
            return {'samples': len(values), 'median': statistics.median(values) if values else None,
                    'p95': ordered[math.ceil(.95*len(ordered))-1] if ordered else None,
                    'maximum': max(values) if values else None}
        return {'counts': dict(self.counts), 'reason_counts': dict(self.reasons),
                'model_correction_px': distribution(self.corrections),
                'self_anchor_disagreement_px': {k: distribution(v) for k, v in self.errors.items()},
                'meaning': 'same_frame_model_consistency_and_estimated_visual_anchor_disagreement',
                'coordinate_accuracy_verified': False, 'complete_overlap_verified': False,
                'sampled_windows': self.windows.summary(),
                'plane_camera_diagnostics':{'reason_counts':dict(self.plane_camera_counts),
                    'examples':self.plane_camera_examples,'camera_model_verified':False,
                    'meaning':'conditional_centered_square_pixel_model_not_game_camera_calibration'},
                'anchor_plane_diagnostics':{'reason_counts':dict(self.anchor_plane_counts),
                    'camera_recovery_counts':dict(self.anchor_camera_counts),'examples':self.anchor_plane_examples,
                    'fit_residual_is_accuracy':False,'live_display_uses_candidate':False},
                'caveat': 'anchor_identity_and_foot_estimated; repeated_frames_not_independent_landmarks'}
