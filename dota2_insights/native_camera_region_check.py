"""Offline rejection diagnostic, never a camera identity or projection certificate."""
import math
import numpy as np


def check(look_at_xy, world_bounds, texture_size, texture_to_crop, region_box, *,
          validation_error_px, margin_px=6., region_identity_verified=False):
    result={'version':'native_camera_region_check_v1','status':'unavailable','reason':'invalid_input',
            'native_target_crop_xy':None,'distance_outside_region_px':None,
            'camera_consistency_verified':False,'safe_for_calibration':False,
            'viewport_identity_verified':region_identity_verified is True,
            'assumptions':['world_window_matches_texture',
                'registered_crop_and_region_are_from_exact_saved_frame',
                'observed_region_bounds_enclose_actual_minimap_viewport',
                'native_look_at_xy_should_lie_inside_visible_viewport']}
    try:
        values=[*look_at_xy,*world_bounds,*texture_size,*region_box,validation_error_px,margin_px]
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):return result
        if (len(look_at_xy)!=2 or len(world_bounds)!=4 or len(texture_size)!=2 or len(region_box)!=4
                or not 0<=validation_error_px<=3 or not 0<=margin_px<=20):return result
        left,bottom,right,top=world_bounds;width,height=texture_size
        x0,y0,x1,y1=region_box
        if not (left<right and bottom<top and width>=2 and height>=2 and x0<x1 and y0<y1):return result
        matrix=np.asarray(texture_to_crop,dtype=float)
        if matrix.shape!=(2,3) or not np.isfinite(matrix).all() or np.linalg.det(matrix[:,:2])<=0:return result
        point=np.array([(look_at_xy[0]-left)/(right-left)*(width-1),
                        (top-look_at_xy[1])/(top-bottom)*(height-1),1.])
        xy=matrix@point
        if not np.isfinite(xy).all():return result
        dx=max(x0-xy[0],0.,xy[0]-x1);dy=max(y0-xy[1],0.,xy[1]-y1)
        distance=math.hypot(dx,dy)
        result.update(status='conditional_conflict' if distance>margin_px else 'conditional_compatible',
            reason='native_target_outside_observed_region' if distance>margin_px else 'inside_expanded_region_not_identity_proof',
            native_target_crop_xy=xy.tolist(),distance_outside_region_px=distance,
            registration_validation_max_error_px=validation_error_px,margin_px=margin_px,
            region_bounds_crop_xy=list(region_box))
        if region_identity_verified is not True:
            result['geometric_status_without_viewport_identity']=result['status']
            result.update(status='unavailable',reason='viewport_region_identity_unverified')
        return result
    except (ValueError,TypeError,np.linalg.LinAlgError):return result
