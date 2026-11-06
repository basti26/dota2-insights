"""Conditional pinhole calibration from native position and explicit 3D anchors.

No default pitch, target height or FOV. This offline fit never authorizes a live
overlay: camera/frame alignment and independent anchor truth remain external.
"""
import math
import numpy as np

from .native_camera_review import validate_position


VERSION='native_position_anchor_calibration_v1'


def _basis(camera, yaw, pitch):
    yaw,pitch=np.radians([yaw,pitch])
    right=np.array([np.sin(yaw),-np.cos(yaw),0.])
    forward=np.array([np.cos(pitch)*np.cos(yaw),np.cos(pitch)*np.sin(yaw),-np.sin(pitch)])
    up=np.cross(right,forward)
    return np.stack([right,-up,forward])


def _normalized(world,camera,yaw,pitch):
    local=(world-camera) @ _basis(camera,yaw,pitch).T
    if np.any(local[:,2]<=1e-6):return None
    return local[:,:2]/local[:,2,None]


def fit(position, anchors, size, *, region_check=None):
    """6+ fit anchors and 4+ separately declared checks; no role auto-selection."""
    result={'version':VERSION,'status':'unavailable','reason':'invalid_input',
        'matrix_3d':None,'pitch_candidate_degrees':None,'focal_candidate_px':None,
        'vertical_fov_candidate_degrees':None,'look_at_z_candidate':None,
        'live_usable':False,'coordinate_accuracy_verified':False,
        'capture_frame_alignment_verified':False,
        'assumptions':['perspective_camera','zero_roll','square_pixels',
                       'principal_point_at_native_client_center','native_yaw_transfers_to_frame',
                       'anchors_use_centered_world_xyz_and_exact_ground_or_model_points']}
    try:
        if region_check is not None:
            if (not isinstance(region_check,dict) or region_check.get('version')!='native_camera_region_check_v1'
                    or region_check.get('status') not in ('conditional_conflict','conditional_compatible','unavailable')):
                result['reason']='invalid_region_check';return result
            if region_check['status']=='conditional_conflict':
                if region_check.get('viewport_identity_verified') is not True:
                    result['reason']='invalid_region_check';return result
                result['reason']='native_camera_frame_region_conflict';return result
            if region_check['status']=='unavailable':
                result['reason']='native_camera_frame_region_unavailable';return result
            if region_check.get('viewport_identity_verified') is not True:
                result['reason']='invalid_region_check';return result
        validate_position(position)
        width,height=size
        if (type(width) is not int or type(height) is not int or not 640<=width<=4096
                or not 480<=height<=2160 or not isinstance(anchors,list) or not 10<=len(anchors)<=32):return result
        identities=set();world=[];screen=[];roles=[]
        for anchor in anchors:
            if (not isinstance(anchor,dict) or not isinstance(anchor.get('id'),str) or not anchor['id']
                    or anchor['id'] in identities or anchor.get('role') not in ('fit','check')
                    or anchor.get('coordinate_basis')!='centered_world_xyz'
                    or not isinstance(anchor.get('source_sha256'),str)
                    or len(anchor['source_sha256'])!=64
                    or any(c not in '0123456789abcdef' for c in anchor['source_sha256'])):
                result['reason']='anchor_provenance_or_identity';return result
            for key,n in [('world_xyz',3),('screen_xy',2)]:
                value=anchor.get(key)
                if not isinstance(value,list) or len(value)!=n or any(
                        type(v) not in (int,float) or not math.isfinite(v) for v in value):
                    result['reason']='anchor_numeric_or_height_missing';return result
            identities.add(anchor['id']);world.append(anchor['world_xyz']);screen.append(anchor['screen_xy'])
            roles.append(anchor['role'])
        world=np.array(world);screen=np.array(screen);train=np.array([r=='fit' for r in roles])
        if train.sum()<6 or (~train).sum()<4:
            result['reason']='separate_fit_and_check_anchors_required';return result
        if np.any(abs(world)>1e7) or np.any(screen<0) or np.any(screen>np.array(size)-1):
            result['reason']='anchor_outside_limits';return result
        # Shared coordinates cannot masquerade as independent checks.
        if len(set(map(tuple,world)))!=len(world) or len(set(map(tuple,screen)))!=len(screen):
            result['reason']='duplicate_anchor_coordinates';return result
        for mask in (train,~train):
            normalized=screen[mask]/np.array(size)
            singular=np.linalg.svd(normalized-normalized.mean(axis=0),compute_uv=False)
            if np.min(np.ptp(normalized,axis=0))<.15 or singular[-1]<.08:
                result['reason']='anchors_spatially_degenerate';return result
        camera=np.array(position['camera_xyz']);yaw=position['yaw_xy_degrees']
        center=(np.array(size)-1)/2;observed=screen[train]-center
        def evaluate(pitch):
            normalized=_normalized(world[train],camera,yaw,pitch)
            if normalized is None:return None
            denominator=float(np.sum(normalized**2))
            if denominator<1e-9:return None
            focal=float(np.sum(normalized*observed)/denominator)
            if not .2*max(size)<=focal<=8*max(size):return None
            error=float(np.mean(np.sum((focal*normalized-observed)**2,axis=1)))
            return error,float(pitch),focal
        # Search range is a refusal boundary, not a fixed assumed pitch.
        choices=[v for p in np.linspace(30,80,501) if (v:=evaluate(p)) is not None]
        if not choices:result['reason']='no_positive_camera_fit';return result
        best=min(choices)
        choices=[v for p in np.linspace(max(30,best[1]-.1),min(80,best[1]+.1),201)
                 if (v:=evaluate(p)) is not None]
        error,pitch,focal=min(choices)
        if pitch<=30.001 or pitch>=79.999:
            result['reason']='pitch_search_boundary';return result
        normalized=_normalized(world,camera,yaw,pitch)
        if normalized is None:result['reason']='check_anchor_behind_camera';return result
        residual=np.linalg.norm(normalized*focal+center-screen,axis=1)
        scale=height/1080
        result.update(fit_anchor_count=int(train.sum()),check_anchor_count=int((~train).sum()),
            fit_rms_px=float(np.sqrt(np.mean(residual[train]**2))),
            check_max_error_px=float(max(residual[~train])),
            pitch_candidate_degrees=pitch,focal_candidate_px=focal,
            vertical_fov_candidate_degrees=math.degrees(2*math.atan((height-1)/(2*focal))),
            check_limit_px=4*scale)
        if result['fit_rms_px']>2*scale or result['check_max_error_px']>4*scale:
            result['reason']='anchor_reprojection_failed';return result
        rotation=_basis(camera,yaw,pitch)
        intrinsic=np.array([[focal,0,center[0]],[0,focal,center[1]],[0,0,1.]])
        matrix=intrinsic @ np.column_stack([rotation,-rotation@camera])
        target_distance=math.hypot(*(position['look_at_xy'][i]-camera[i] for i in (0,1)))
        result.update(status='conditional_candidate',reason='fit_and_separate_checks_passed',
            matrix_3d=matrix.tolist(),camera_xyz=camera.tolist(),yaw_xy_degrees=yaw,
            native_viewport=list(size),
            look_at_z_candidate=float(camera[2]-target_distance*math.tan(math.radians(pitch))))
        return result
    except (ValueError,TypeError,KeyError,OverflowError,np.linalg.LinAlgError):return result


def project(candidate, world_xyz):
    if candidate.get('status')!='conditional_candidate':return None
    try:
        if (not isinstance(world_xyz,list) or len(world_xyz)!=3 or any(
                type(v) not in (int,float) or not math.isfinite(v) for v in world_xyz)):return None
        point=np.array(candidate['matrix_3d']) @ [*world_xyz,1.]
        if not np.isfinite(point).all() or point[2]<=1e-6:return None
        xy=point[:2]/point[2]
        width,height=candidate['native_viewport']
        return xy.tolist() if 0<=xy[0]<width and 0<=xy[1]<height else None
    except (ValueError,TypeError,KeyError):return None
