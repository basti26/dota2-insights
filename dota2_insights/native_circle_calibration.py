"""Bounded offline ground-circle fit; alternate angles are not pixel truth."""
import math
import cv2
import numpy as np

from .native_camera_calibration import _basis
from .native_camera_review import validate_position


def fit(image, position, circles):
    result={'version':'native_ground_circle_fit_v1','status':'unavailable','reason':'invalid_input',
        'live_usable':False,'world_to_screen_verified':False,'circle_identity_verified':False,
        'capture_frame_alignment_verified':False,'assumptions':['constant_z_circle_per_building',
            'declared_attack_range_equals_visible_ring_radius','zero_roll','square_pixels',
            'principal_point_at_full_native_client_center'],
        'check_basis':'alternate_angles_not_independent_pixel_truth'}
    try:
        validate_position(position)
        if (not isinstance(image,np.ndarray) or image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3
                or not 480<=image.shape[0]<=2160 or not 640<=image.shape[1]<=4096
                or not isinstance(circles,list) or not 2<=len(circles)<=6):return result
        height,width=image.shape[:2];scale=min(1.,960/width)
        small=cv2.resize(image,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
        base=cv2.medianBlur(small,9).astype(np.float32);pixels=small.astype(np.float32)
        delta=pixels-base
        mask=((pixels[:,:,1]>150)&(delta[:,:,1]>20)&
              (delta[:,:,1]-(delta[:,:,0]+delta[:,:,2])/2>18)).astype(np.uint8)
        mask[:round(mask.shape[0]*.1)]=0;mask[round(mask.shape[0]*.82):]=0
        mask[:,:round(mask.shape[1]*.04)]=0;mask[:,round(mask.shape[1]*.96):]=0
        if np.count_nonzero(mask)<80:
            result['reason']='insufficient_green_line_evidence';return result
        if np.count_nonzero(mask)>.05*mask.size:
            result['reason']='green_mask_not_sparse_lines';return result
        distances=cv2.distanceTransform(1-mask,cv2.DIST_L2,cv2.DIST_MASK_PRECISE)/scale
        angles=np.arange(72)*2*np.pi/72
        world=[]
        for circle in circles:
            if not isinstance(circle,dict):return result
            xyz=circle.get('center_xyz');radius=circle.get('radius');sha=circle.get('source_sha256')
            if (not isinstance(xyz,list) or len(xyz)!=3 or any(type(v) not in (int,float)
                    or not math.isfinite(v) or abs(v)>1e7 for v in xyz)
                    or type(radius) not in (int,float) or not 100<=radius<=2000
                    or not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha)):
                return result
            world.append(np.array(xyz)+np.column_stack([radius*np.cos(angles),radius*np.sin(angles),np.zeros(72)]))
        world=np.array(world);camera=np.array(position['camera_xyz']);yaw=position['yaw_xy_degrees']
        center=(np.array([width,height])-1)/2
        def evaluate(pitch,fov,check=False):
            local=(world-camera)@_basis(camera,yaw,pitch).T
            focal=(height-1)/(2*math.tan(math.radians(fov)/2))
            xy=focal*local[:,:,:2]/np.maximum(local[:,:,2:],1e-9)+center
            offset=1 if check else 0;selected=xy[:,offset::2];depth=local[:,offset::2,2]
            samples=[];coverage=[]
            for points,z in zip(selected,depth):
                q=np.rint(np.clip(points*scale,-1e6,1e6)).astype(np.int64)
                good=(z>1e-6)&(q[:,0]>=0)&(q[:,0]<small.shape[1])&\
                    (q[:,1]>=round(small.shape[0]*.1))&(q[:,1]<round(small.shape[0]*.82))
                if np.count_nonzero(good)<12:return None
                samples.extend(distances[q[good,1],q[good,0]].tolist())
                coverage.append(int(np.count_nonzero(good)))
            return np.array(samples),coverage,xy
        best=None
        for pitch in np.linspace(50,70,41):
            for fov in np.linspace(45,80,71):
                evaluated=evaluate(pitch,fov)
                if evaluated is None:continue
                loss=float(np.mean(np.minimum(evaluated[0],30)**2))
                if best is None or loss<best[0]:best=(loss,float(pitch),float(fov))
        if best is None:result['reason']='insufficient_visible_circle_arcs';return result
        coarse=best
        for pitch in np.linspace(max(50,coarse[1]-.5),min(70,coarse[1]+.5),21):
            for fov in np.linspace(max(45,coarse[2]-.5),min(80,coarse[2]+.5),21):
                evaluated=evaluate(pitch,fov)
                if evaluated is None:continue
                loss=float(np.mean(np.minimum(evaluated[0],30)**2))
                if loss<best[0]:best=(loss,float(pitch),float(fov))
        training=evaluate(best[1],best[2]);checked=evaluate(best[1],best[2],True)
        if checked is None:result['reason']='insufficient_check_arcs';return result
        result.update(pitch_candidate_degrees=best[1],vertical_fov_candidate_degrees=best[2],
            focal_candidate_px=(height-1)/(2*math.tan(math.radians(best[2])/2)),
            fit_median_distance_px=float(np.median(training[0])),
            check_median_distance_px=float(np.median(checked[0])),
            check_p90_distance_px=float(np.percentile(checked[0],90)),
            check_fraction_within_4px=float(np.mean(checked[0]<=4)),
            fit_visible_samples_per_circle=training[1],check_visible_samples_per_circle=checked[1],
            selected_green_pixels=int(np.count_nonzero(mask)),
            projected_circle_xy=checked[2].tolist())
        if best[1]<=50.01 or best[1]>=69.99 or best[2]<=45.01 or best[2]>=79.99:
            result['reason']='parameter_search_boundary';return result
        if (result['fit_median_distance_px']>3 or result['check_median_distance_px']>3
                or result['check_p90_distance_px']>8 or result['check_fraction_within_4px']<.65):
            result['reason']='circle_evidence_not_sufficient';return result
        result.update(status='conditional_circle_candidate',reason='alternate_angle_checks_passed_not_independent_accuracy')
        return result
    except (ValueError,TypeError,KeyError,OverflowError,cv2.error):return result
