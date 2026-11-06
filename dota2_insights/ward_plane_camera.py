"""Conditional plane-camera lift, never independent evidence of a game's camera."""
import math
import numpy as np


def recover(matrix,size):
    result={'version':'centered_square_pixel_plane_camera_v1','status':'unavailable',
            'reason':'invalid_input','matrix_3d':None,'coordinate_accuracy_verified':False,
            'camera_model_verified':False,'ground_plane_z':0.,
            'assumptions':['perspective_camera','square_pixels','zero_skew',
                           'principal_point_at_native_client_center','input_homography_is_metric_Z0_plane'],
            'provisional_limits':{'maximum_rotation_error':.02,'focal_over_long_side':[.2,8.]}}
    try:
        h=np.asarray(matrix,float)
        width,height=size
        if (h.shape!=(3,3) or not np.isfinite(h).all()
                or type(width) is not int or type(height) is not int
                or not 640<=width<=4096 or not 480<=height<=2160):return result
        scale=float(np.linalg.norm(h[:,:2]))
        if not math.isfinite(scale) or scale<1e-12:return result
        h=h/scale
        center=np.array([(width-1)/2,(height-1)/2,1.])
        origin=np.linalg.solve(h,center)
        if not np.isfinite(origin).all() or abs(origin[2])<1e-12:return result
        origin=origin[:2]/origin[2]
        if (h @ [*origin,1.])[2]<0:h=-h
        if np.linalg.norm(h[2,:2])<1e-12:
            result['reason']='affine_plane_does_not_identify_focal_length';return result
        q=h[:2,:2]-center[:2,None]*h[2,:2]
        # Orthogonality and equal norms of K^-1*h1 and K^-1*h2.
        a=np.array([np.dot(q[:,0],q[:,1]),np.dot(q[:,0],q[:,0])-np.dot(q[:,1],q[:,1])])
        b=np.array([h[2,0]*h[2,1],h[2,0]**2-h[2,1]**2])
        denominator=float(a@a)
        if denominator<1e-24:
            result['reason']='focal_constraints_degenerate';return result
        inverse_focal_squared=-float(a@b)/denominator
        if not math.isfinite(inverse_focal_squared) or inverse_focal_squared<=0:
            result['reason']='no_positive_focal_solution';return result
        focal=math.sqrt(1/inverse_focal_squared)
        result['focal_candidate_px']=focal
        if not .2*max(size)<=focal<=8*max(size):
            result['reason']='focal_outside_provisional_limits';return result
        k=np.array([[focal,0,center[0]],[0,focal,center[1]],[0,0,1.]])
        local=h @ np.array([[1,0,origin[0]],[0,1,origin[1]],[0,0,1.]])
        g=np.linalg.solve(k,local)
        n1,n2=np.linalg.norm(g[:,0]),np.linalg.norm(g[:,1])
        if min(n1,n2)<1e-12:return result
        r1,r2=g[:,0]/n1,g[:,1]/n2
        orthogonality=abs(float(r1@r2));scale_error=abs(float(n1/n2-1))
        result.update(orthogonality_error=orthogonality,axis_scale_error=scale_error)
        if max(orthogonality,scale_error)>.02:
            result['reason']='plane_inconsistent_with_camera_assumptions';return result
        r3=np.cross(r1,r2);r3/=np.linalg.norm(r3)
        lam=2/(n1+n2)
        camera_height=-float(r3 @ (g[:,2]*lam))
        result['camera_height_candidate']=camera_height
        if not math.isfinite(camera_height) or not 1<camera_height<100000:
            result['reason']='camera_not_above_reference_plane';return result
        lifted=np.column_stack([h[:,0],h[:,1],k@r3/lam,h[:,2]])
        if not np.isfinite(lifted).all():return result
        result.update(status='conditional_candidate',reason='conditional_metric_plane_camera',
                      matrix_3d=lifted.tolist(),plane_origin_xy=origin.tolist())
        return result
    except (ValueError,TypeError,OverflowError,np.linalg.LinAlgError,FloatingPointError):
        return result


def project(candidate,xyz):
    if candidate.get('status')!='conditional_candidate':return None
    try:
        value=np.asarray(xyz,float)
        if value.shape!=(3,) or not np.isfinite(value).all():return None
        p=np.asarray(candidate['matrix_3d'],float) @ [*value,1.]
        if not np.isfinite(p).all() or p[2]<=1e-9:return None
        screen=p[:2]/p[2]
        return screen.tolist() if np.isfinite(screen).all() else None
    except (ValueError,TypeError,KeyError):return None
