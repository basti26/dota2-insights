"""Compare painted ward footprints to independently matched ground pixels."""

import math

import cv2
import numpy as np


TINTS = np.array(((140,222,255),(255,220,117)),dtype=float)
OPACITY = .58


def _features(image, sample, detector):
    gray = cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
    width,height = gray.shape[1]//2,gray.shape[0]//2
    reduced = cv2.resize(gray,(width,height),interpolation=cv2.INTER_AREA)
    anchors = np.asarray(list(sample['points'].values()))/2
    left,top = np.maximum(np.floor(anchors.min(axis=0)-205).astype(int),(width//12,height//10))
    right,bottom = np.minimum(np.ceil(anchors.max(axis=0)+205).astype(int),(width*11//12,height*4//5))
    if right-left<32 or bottom-top<32:
        return (),None
    allowed = np.full((bottom-top,right-left),255,np.uint8)
    for key,xy in sample['points'].items():
        corners = np.asarray(sample.get('footprints',{}).get(key,[xy]))/2
        lo = np.floor(corners.min(axis=0)-16).astype(int)-(left,top)
        hi = np.ceil(corners.max(axis=0)+16).astype(int)-(left,top)
        cv2.rectangle(allowed,tuple(lo),tuple(hi),0,-1)
    points,descriptors = detector.detectAndCompute(reduced[top:bottom,left:right],allowed)
    for point in points:
        point.pt = (point.pt[0]+left,point.pt[1]+top)
    return points,descriptors


def _register(first,second,old,new):
    detector = cv2.SIFT_create(nfeatures=3200,contrastThreshold=.012)
    a,da = _features(first,old,detector)
    b,db = _features(second,new,detector)
    if da is None or db is None or min(len(a),len(b))<2:
        return None
    matcher = cv2.BFMatcher()

    def matches(source,target):
        return {m.queryIdx:m.trainIdx for pair in matcher.knnMatch(source,target,k=2) if len(pair)==2
                for m,n in (pair,) if m.distance<.74*n.distance}

    reverse = matches(db,da)
    pairs = [(a[i].pt,b[j].pt) for i,j in matches(da,db).items() if reverse.get(j)==i]
    if len(pairs)<24:
        return None
    source,target = np.asarray(sorted(pairs),dtype=np.float32).transpose(1,0,2)
    check = np.arange(len(source))%3==1
    matrix,labels = cv2.findHomography(source[~check],target[~check],cv2.USAC_MAGSAC,2.)
    if matrix is None or labels is None or not np.isfinite(matrix).all():
        return None
    support = source[~check][labels.ravel()==1]
    agree = np.linalg.norm(cv2.perspectiveTransform(source[check,None,:],matrix)[:,0]-target[check],axis=1)<=2.5
    if len(support)<12 or agree.sum()<8 or agree.mean()<.6:
        return None
    contour = cv2.convexHull(support)
    if cv2.contourArea(contour)<1800:
        return None
    return matrix,contour,source[check][agree]


def _outline_colors(image, vertices):
    """Check for thin cyan/gold painted edges at the declared viewport pixels."""
    if not vertices:
        return set()
    height,width = image.shape[:2]
    vertices = np.asarray(vertices,dtype=float)
    matches = np.zeros((4,len(TINTS)),dtype=int)
    for side,(start,end) in enumerate(zip(vertices,np.roll(vertices,-1,axis=0),strict=True)):
        delta = end-start
        length = np.linalg.norm(delta)
        if length<5:
            continue
        normal = np.array([-delta[1],delta[0]])/length
        for fraction in (.2,.4,.6,.8):
            at = start+fraction*delta
            coords = np.rint(at+np.arange(-6,7)[:,None]*normal).astype(int)
            if not np.all((0<=coords) & (coords<[width,height])):
                continue
            samples = image[coords[:,1],coords[:,0]].astype(float)
            background = samples[[0,1,-2,-1]].mean(axis=0)
            predicted = TINTS*OPACITY+background*(1-OPACITY)
            contrast = np.linalg.norm(predicted-background,axis=1)
            ringing = samples[4:9]
            error = np.linalg.norm(ringing[:,None,:]-predicted,axis=2)
            addition = np.linalg.norm(ringing-background,axis=1)[:,None]
            good = (contrast>=30) & (error<=35) & (addition-error>=12)
            matches[side] += good.any(axis=0)
    return {i for i in range(len(TINTS)) if matches[:,i].sum()>=6
            and np.count_nonzero(matches[:,i])>=3}


def compare(first,second,old,new,*,min_terrain_motion_px=12.,attachment_gate_px=6.):
    if first.shape!=second.shape:
        raise ValueError('terrain_patch_image_size_mismatch')
    result = {'status':'independent_terrain_unconfirmed','markers':[],
              'absolute_world_alignment_verified':False}
    registration = _register(first,second,old,new)
    if registration is None:
        return result
    matrix,hull,check_points = registration
    for key in sorted(old['points'].keys() & new['points'].keys()):
        origin = np.array(old['points'][key])/2
        if (cv2.pointPolygonTest(hull,tuple(origin),False)<0
                or np.linalg.norm(check_points-origin,axis=1).min()>65):
            continue
        predicted = cv2.perspectiveTransform(origin.reshape(1,1,2),matrix)[0,0]*2
        terrain_motion = math.dist(predicted,old['points'][key])
        if terrain_motion<min_terrain_motion_px:
            continue
        first_outline = old.get('footprints',{}).get(key)
        second_outline = new.get('footprints',{}).get(key)
        first_colors = _outline_colors(first,first_outline)
        second_colors = _outline_colors(second,second_outline)
        corner_error = None
        if first_outline and second_outline:
            points = np.asarray(first_outline,dtype=float)/2
            if all(cv2.pointPolygonTest(hull,tuple(point),False)>=0 for point in points):
                footprint = cv2.perspectiveTransform(points[:,None,:],matrix)[:,0]*2
                corner_error = round(float(np.linalg.norm(footprint-second_outline,axis=1).max()),2)
        result['markers'].append({'id':key,'terrain_motion_px':round(terrain_motion,2),
            'displacement_error_px':round(math.dist(predicted,new['points'][key]),2),
            'footprint_corner_error_max_px':corner_error,
            'outline_color_supported_in_both':bool(first_colors & second_colors)})
    markers = [row for row in result['markers'] if row['outline_color_supported_in_both']
               and row['footprint_corner_error_max_px'] is not None]
    if len(markers)>=2:
        largest = max(max(m['displacement_error_px'],m['footprint_corner_error_max_px']) for m in markers)
        result['status'] = ('relative_attachment_observed' if largest<=attachment_gate_px
                            else 'painted_marker_terrain_disagreement')
    elif result['markers']:
        result['status'] = 'terrain_motion_found_outline_pixels_unconfirmed'
    return result
