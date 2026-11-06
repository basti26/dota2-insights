"""Explicit-parameter sensitivity preview, not a fitted or live calibration."""
import math
import numpy as np

from .native_camera_calibration import _basis
from .native_camera_review import validate_position


def preview(position, landmarks, size, *, pitch_degrees, vertical_fov_degrees):
    result={'version':'native_landmark_sensitivity_v1','status':'unavailable','reason':'invalid_input',
        'points':[],'live_usable':False,'world_to_screen_verified':False,
        'model_origin_pixel_correspondence_verified':False,'calibration_fitted':False}
    try:
        validate_position(position)
        width,height=size
        if (type(width) is not int or type(height) is not int or not 640<=width<=4096
                or not 480<=height<=2160 or any(type(v) not in (float,int) or not math.isfinite(v)
                    for v in (pitch_degrees,vertical_fov_degrees))
                or not 30<=pitch_degrees<=80 or not 20<=vertical_fov_degrees<=100
                or not isinstance(landmarks,list) or len(landmarks)>128):return result
        camera=np.array(position['camera_xyz']);yaw=position['yaw_xy_degrees']
        rotation=_basis(camera,yaw,pitch_degrees)
        focal=(height-1)/(2*math.tan(math.radians(vertical_fov_degrees)/2))
        center=(np.array(size)-1)/2
        points=[];seen=set()
        for row in landmarks:
            if not isinstance(row,dict):return result
            identity=row.get('entity_ordinal');xyz=row.get('origin_xyz')
            if type(identity) is not int or not 0<=identity<20000 or identity in seen:return result
            seen.add(identity)
            if (not isinstance(xyz,list) or len(xyz)!=3 or any(type(v) not in (float,int)
                    or not math.isfinite(v) or abs(v)>1e7 for v in xyz)):
                return result
            local=rotation@(np.array(xyz)-camera)
            xy=(focal*local[:2]/local[2]+center).tolist() if local[2]>1e-6 else None
            points.append({'entity_ordinal':identity,'targetname':row.get('targetname'),
                'classname':row.get('classname'),'world_xyz':list(xyz),'screen_xy':xy,
                'inside_viewport':bool(xy is not None and 0<=xy[0]<width and 0<=xy[1]<height),
                'depth':float(local[2]),'origin_pixel_correspondence_verified':False})
        result.update(status='sensitivity_preview',reason='explicit_parameters_not_calibration',
            native_viewport=list(size),pitch_degrees=pitch_degrees,
            vertical_fov_degrees=vertical_fov_degrees,focal_candidate_px=focal,points=points)
        return result
    except (TypeError,ValueError,KeyError,OverflowError):return result
