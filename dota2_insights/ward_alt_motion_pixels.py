"""Find visual return candidates in admitted three-image Alt windows."""
import numpy as np

from .ward_minimap_pixels import read_rgb


def _features(cv, rgb):
    height,width=rgb.shape[:2]
    if min(height,width)<64:
        return np.empty((0,2)),None,1.
    scale=min(1.,1280./width)
    image=cv.cvtColor(rgb,cv.COLOR_RGB2GRAY)
    if scale<1.:
        image=cv.resize(image,(1280,round(height*scale)),interpolation=cv.INTER_AREA)
    rows,cols=image.shape
    mask=np.zeros_like(image)
    mask[round(.15*rows):round(.80*rows),round(.12*cols):round(.91*cols)]=255
    detector=cv.SIFT_create(nfeatures=1100,contrastThreshold=.018)
    keys,desc=detector.detectAndCompute(image,mask)
    xy=np.asarray([key.pt for key in keys],float).reshape(-1,2)
    return xy,desc,2560./cols


def _image_link(cv, first, second):
    a,f,scale=first
    b,g,_=second
    if min(len(a),len(b))<48:
        return {'state':'insufficient_texture','matches':0}
    bf=cv.BFMatcher()

    def ratio(d,e):
        return {x[0].queryIdx:x[0].trainIdx for x in bf.knnMatch(d,e,k=2)
                if len(x)==2 and x[0].distance < .74*x[1].distance}

    forward,back=ratio(f,g),ratio(g,f)
    shared=[(i,j) for i,j in forward.items() if back.get(j)==i]
    if len(shared)<8:
        return {'state':'very_low_reciprocal_overlap','matches':len(shared)}
    i,j=np.asarray(shared).T
    p,q=a[i],b[j]
    _,ind=np.unique(np.rint(p).astype(int),axis=0,return_index=True)
    p,q=p[ind],q[ind]
    _,ind=np.unique(np.rint(q).astype(int),axis=0,return_index=True)
    p,q=p[ind],q[ind]
    if len(p)<max(8,.03*min(len(a),len(b))):
        return {'state':'very_low_reciprocal_overlap','matches':len(p)}
    cells=np.floor(p*scale/192.).astype(int)
    check=cells.sum(axis=1)%3==1
    result=dict(matches=len(p),heldout_matches=int(check.sum()),state='overlap_unresolved')
    if check.sum()<8 or (~check).sum()<12:
        return result
    matrix,inliers=cv.estimateAffinePartial2D(p[~check],q[~check],method=cv.RANSAC,
        ransacReprojThreshold=4./scale,maxIters=1200)
    if matrix is None or inliers.sum()<12:
        return result
    residual=np.linalg.norm((p[check]@matrix[:,:2].T+matrix[:,2])-q[check],axis=1)*scale
    agreeing=residual<=5.33
    spread=len(np.unique(cells[check][agreeing],axis=0))
    movement=np.linalg.norm(p[check]-q[check],axis=1)*scale
    p90=float(np.quantile(residual,.9))
    supported=spread>=3 and int(agreeing.sum())>=8 and agreeing.mean()>=.85
    state='overlap_unresolved'
    if supported:
        state='near_same_view' if np.median(movement[agreeing])<=20. else 'shifted_overlapping_view'
    return {**result,'state':state,'heldout_agreement':int(agreeing.sum()),
            'heldout_p90_px_at_2560':p90,
            'heldout_agreeing_p90_px_at_2560':float(np.quantile(residual[agreeing],.9)) if agreeing.any() else None,
            'heldout_spread_cells':spread,
            'camera_motion_verified':False}


def classify(paths, viewport):
    try:
        import cv2 as cv
        if not hasattr(cv,'SIFT_create'):
            return {'state':'vision_backend_unavailable'}
    except ImportError:
        return {'state':'vision_backend_unavailable'}
    frames=[_features(cv,read_rgb(path,viewport)) for path in paths]
    start_middle=_image_link(cv,frames[0],frames[1])
    middle_end=_image_link(cv,frames[1],frames[2])
    start_end=_image_link(cv,frames[0],frames[2])
    if (start_end['state']=='near_same_view' and
            start_middle['state']==middle_end['state']=='very_low_reciprocal_overlap'):
        kind='view_return_candidate'
    elif all(pair['state']=='near_same_view' for pair in (start_middle,middle_end,start_end)):
        kind='near_same_view_throughout'
    elif any(pair['state']=='insufficient_texture' for pair in (start_middle,middle_end,start_end)):
        kind='insufficient_texture'
    else:
        kind='view_return_unresolved'
    return dict(state=kind,start_middle=start_middle,middle_end=middle_end,start_end=start_end,
                camera_cut_verified=False,ward_world_alignment_verified=False)
