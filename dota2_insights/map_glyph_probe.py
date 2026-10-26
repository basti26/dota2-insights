# Detector adapted from the pre-split helper minimap_icon_probe.py; see docs/MAP_GLYPH_REVIEW.md.
"""Image-only official-glyph observations; no hero identity or map fitting."""
import math

THRESHOLD = .92
SCALES = tuple(range(8, 33))


def detect_glyph(image_rgb, template_rgba, *, sizes=SCALES,minimum_center_distance=14,per_scale_limit=8):
    """Search the entire native crop, without any world/predicted coordinates."""
    import cv2
    import numpy as np
    if (not isinstance(image_rgb, np.ndarray) or image_rgb.dtype != np.uint8
            or image_rgb.ndim != 3 or image_rgb.shape[2] != 3
            or not 64 <= min(image_rgb.shape[:2]) <= max(image_rgb.shape[:2]) <= 1024
            or not isinstance(template_rgba, np.ndarray) or template_rgba.dtype != np.uint8
            or template_rgba.ndim != 3 or template_rgba.shape[2] != 4
            or not 8 <= min(template_rgba.shape[:2]) <= max(template_rgba.shape[:2]) <= 256
            or not sizes or len(sizes) > 32 or len(set(sizes)) != len(sizes)
            or any(type(s) is not int or not 8 <= s <= 64 for s in sizes)
            or type(minimum_center_distance) is not int or not 4<=minimum_center_distance<=32
            or type(per_scale_limit) is not int or not 1<=per_scale_limit<=32):
        raise ValueError('bounded_native_image_and_rgba_reference_required')
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    raw, overflow = [], False
    for size in sizes:
        t = cv2.resize(template_rgba, (size, size), interpolation=cv2.INTER_AREA)
        mask = (t[:, :, 3] > 16).astype(np.uint8) * 255
        needle = cv2.cvtColor(t[:, :, :3], cv2.COLOR_RGB2GRAY)
        if (mask > 0).sum() < 16 or np.std(needle[mask > 0]) < 8:
            raise ValueError('reference_has_no_distinct_masked_shape')
        scores = cv2.matchTemplate(gray, needle, cv2.TM_CCOEFF_NORMED, mask=mask)
        scores = np.nan_to_num(scores, nan=-1, posinf=-1, neginf=-1)
        for index in range(per_scale_limit+1):
            _, score, _, position = cv2.minMaxLoc(scores)
            if score < THRESHOLD:
                break
            if index == per_scale_limit:
                overflow = True
                break
            x, y = position
            raw.append({'pixel_xy': [x + (size - 1)/2, y + (size - 1)/2],
                        'score': float(score), 'size_px': size, 'rect': [x, y, size, size]})
            radius = max(8, size // 2)
            scores[max(0,y-radius):y+radius+1, max(0,x-radius):x+radius+1] = -1
    candidates = []
    for row in sorted(raw, key=lambda r: (-r['score'], r['pixel_xy'], r['size_px'])):
        if any(math.dist(row['pixel_xy'], p['pixel_xy']) < minimum_center_distance for p in candidates):
            continue
        candidates.append(row)
    return {'schema_version': 1, 'method': 'full_crop_masked_shape_ncc_v1',
        'sizes_px': list(sizes), 'threshold': THRESHOLD,
        'minimum_center_distance_px':minimum_center_distance,'per_scale_candidate_limit':per_scale_limit,
        'candidates': candidates, 'overflow': overflow, 'predictions_used': False,
        'identity_confirmed': False, 'score_is_probability': False}


def detect_building_silhouette(image_rgb,template_rgba,*,sizes=SCALES):
    """Match render-tinted foreground shape, not grayscale shading or map locations."""
    import cv2
    import numpy as np
    if (not isinstance(image_rgb,np.ndarray) or image_rgb.dtype!=np.uint8
            or image_rgb.ndim!=3 or image_rgb.shape[2]!=3 or not 64<=min(image_rgb.shape[:2])<=max(image_rgb.shape[:2])<=1024
            or not isinstance(template_rgba,np.ndarray) or template_rgba.dtype!=np.uint8
            or template_rgba.ndim!=3 or template_rgba.shape[2]!=4
            or not 8<=min(template_rgba.shape[:2])<=max(template_rgba.shape[:2])<=256
            or not sizes or len(sizes)>32 or len(set(sizes))!=len(sizes)
            or any(type(s) is not int or not 8<=s<=64 for s in sizes)):
        raise ValueError('bounded_building_silhouette_inputs_required')
    channels=image_rgb.astype(np.float32)
    r,g,b=channels[:,:,0],channels[:,:,1],channels[:,:,2]
    observed=(((r>60)&(r>1.5*g)&(r>1.5*b))|((g>60)&(g>1.5*r)&(g>1.5*b))).astype(np.uint8)
    raw=[];overflow=False;skipped=[]
    for size in sizes:
        resized=cv2.resize(template_rgba,(size,size),interpolation=cv2.INTER_AREA)
        mask=((resized[:,:,3]>16)&(resized[:,:,:3].max(axis=2)>40)).astype(np.uint8)
        if mask.sum()<8 or mask.sum()>=size*size-4:
            skipped.append(size);continue
        scores=cv2.matchTemplate(observed,mask,cv2.TM_CCOEFF_NORMED)
        scores=np.nan_to_num(scores,nan=-1,posinf=-1,neginf=-1)
        for index in range(33):
            _,score,_,(x,y)=cv2.minMaxLoc(scores)
            if score<THRESHOLD:break
            if index==32:overflow=True;break
            patch=observed[y:y+size,x:x+size]
            union=np.count_nonzero(patch|mask)
            iou=np.count_nonzero(patch&mask)/union if union else 0
            if iou>=.8:
                raw.append({'pixel_xy':[x+(size-1)/2,y+(size-1)/2],'score':float(score),
                    'silhouette_iou':float(iou),'size_px':size,'rect':[x,y,size,size]})
            radius=max(4,size//3)
            scores[max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1]=-1
    candidates=[]
    for row in sorted(raw,key=lambda p:(-p['score'],p['pixel_xy'],p['size_px'])):
        if not any(math.dist(row['pixel_xy'],p['pixel_xy'])<6 for p in candidates):candidates.append(row)
    return {'schema_version':1,'method':'full_crop_tinted_building_silhouette_v1',
        'sizes_px':list(sizes),'skipped_degenerate_sizes':skipped,'threshold':THRESHOLD,
        'minimum_silhouette_iou':.8,'tint_rule':'red_or_green_above_60_and_1p5_other_channels',
        'candidates':candidates,'overflow':overflow,'predictions_used':False,
        'identity_confirmed':False,'score_is_probability':False}
