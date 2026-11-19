"""Offline pixel review for a partly occluded white selection ring."""

import math


def locate(image, screen_xy):
    import cv2
    import numpy as np

    height,width = image.shape[:2]
    scale = height/1440.
    x,y = screen_xy
    x0,y0 = max(0,round(x-130*scale)),max(0,round(y-105*scale))
    x1,y1 = min(width,round(x+190*scale)),min(height,round(y+190*scale))
    if x1-x0<160*scale or y1-y0<145*scale:
        return {'status':'ring_roi_clipped','candidate':None}
    roi = cv2.resize(image[y0:y1,x0:x1],(round((x1-x0)/scale),round((y1-y0)/scale)))
    # Images in review use RGB. Narrow neutral lines remain visible
    # even against pale terrain, where thresholding intensity alone merges them.
    _,saturation,value = cv2.split(cv2.cvtColor(roi,cv2.COLOR_RGB2HSV))
    prominence = cv2.subtract(value,cv2.medianBlur(value,5))
    neutral = (saturation<=105) & (value>=135)
    detail = neutral & (prominence>=9)
    phases = np.arange(72)*2*np.pi/72
    unit = np.stack((np.cos(phases),np.sin(phases)),axis=1)
    proposals = []

    for threshold in (12,20):
        mask = (255*(neutral & (prominence>=threshold))).astype(np.uint8)
        contours,_ = cv2.findContours(mask,cv2.RETR_LIST,cv2.CHAIN_APPROX_NONE)
        for contour in sorted(contours,key=len,reverse=True)[:32]:
            if len(contour)<45:
                break
            ellipse = cv2.fitEllipse(contour)
            (cx,cy),(a,b),angle = ellipse
            if (not all(math.isfinite(v) for v in (cx,cy,a,b,angle))
                    or not 0<=cx<roi.shape[1] or not 0<=cy<roi.shape[0]
                    or not 95<=max(a,b)<=185 or not 75<=min(a,b)<=150
                    or min(a,b)/max(a,b)<.66
                    or math.dist((cx,cy),((x-x0)/scale,(y-y0)/scale))>100):
                continue
            theta = math.radians(angle)
            rotation = np.array([[math.cos(theta),math.sin(theta)],[-math.sin(theta),math.cos(theta)]])
            perimeter = unit*(a/2,b/2)
            hits = np.zeros(len(unit),dtype=bool)
            for fraction in (.97,1.,1.03):
                px,py = np.rint(perimeter*fraction@rotation+(cx,cy)).astype(np.intp).T
                inside = (0<=px) & (px<roi.shape[1]) & (0<=py) & (py<roi.shape[0])
                hits[inside] |= detail[py[inside],px[inside]]
            if hits.sum()<35 or np.count_nonzero(hits.reshape(8,9).sum(axis=1)>=3)<5:
                continue
            inner = np.zeros(mask.shape,np.uint8)
            cv2.ellipse(inner,((cx,cy),(.55*a,.55*b),angle),255,-1)
            if neutral[inner!=0].mean()>.28:
                continue
            proposals.append((int(hits.sum()),threshold,cx,cy,a,b,angle))

    if not proposals:
        return {'status':'ring_unconfirmed','candidate':None}
    hits,_,cx,cy,a,b,angle = max(proposals)
    nearby = [p for p in proposals if math.dist(p[2:4],(cx,cy))<=7]
    if len({p[1] for p in nearby})<2 or any(p[0]>=hits-5 for p in proposals if p not in nearby):
        return {'status':'ring_ambiguous','candidate':None}
    return {'status':'partial_ring_candidate','candidate':{
        'center_xy':[x0+cx*scale,y0+cy*scale],
        'ellipse_size_px':[a*scale,b*scale],'angle_degrees':angle,
        'supported_arc_samples':hits,'total_arc_samples':len(unit),
        'ownership_verified':False,'world_alignment_verified':False}}
