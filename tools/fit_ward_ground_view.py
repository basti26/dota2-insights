"""Fit an offline two-view ground-plane transform from fixed train/check features."""

import argparse
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.history import checked_input, checked_output
from dota2_insights.minimap.offline_io import new_binary_output
from dota2_insights.native_camera_calibration import _basis
from dota2_insights.native_camera_review import validate_position


def plane_matrix(position, yaw, pitch, focal, center, z):
    """Project homogeneous [world_x, world_y, 1] at the specified height."""
    rotation = _basis(position,yaw,pitch)
    offset = rotation @ (np.array([0.,0.,z])-position)
    model = np.column_stack((rotation[:,:2],offset))
    intrinsic = np.array([[focal,0.,center[0]],[0.,focal,center[1]],[0.,0.,1.]])
    return intrinsic@model


def apply(matrix, xy):
    values=np.column_stack((xy,np.ones(len(xy))))@matrix.T
    if not np.isfinite(values).all() or np.any(np.abs(values[:,2])<1e-9):
        raise ValueError('ground_view_invalid_projection')
    return values[:,:2]/values[:,2,None]


def fit_map_offset(world_to_image, anchors):
    """Fit a single XY frame correction from declared landmarks in two views."""
    matrices=[np.asarray(matrix,dtype=float) for matrix in world_to_image]
    roles={role:[row for row in anchors if row['role']==role] for role in ('fit','check')}
    if (len(matrices)!=2 or len(roles['fit'])<3 or not roles['check']
            or len(roles['fit'])+len(roles['check'])!=len(anchors)
            or len({row['id'] for row in anchors})!=len(anchors)
            or np.linalg.matrix_rank(np.asarray([row['world_xy'] for row in roles['fit']])-roles['fit'][0]['world_xy'])<2):
        raise ValueError('map_offset_independent_landmarks_required')
    votes=[]
    for row in roles['fit']:
        for matrix,observed in zip(matrices,row['observed_xy'],strict=True):
            votes.append(np.asarray(row['world_xy'])-apply(np.linalg.inv(matrix),[observed])[0])
    delta=np.median(votes,axis=0)
    translate=np.array([[1.,0.,-delta[0]],[0.,1.,-delta[1]],[0.,0.,1.]])
    corrected=[matrix@translate for matrix in matrices]
    results=[]
    for row in anchors:
        predicted=[apply(matrix,[row['world_xy']])[0] for matrix in corrected]
        errors=np.linalg.norm(np.asarray(predicted)-row['observed_xy'],axis=1)
        results.append({'id':row['id'],'role':row['role'],'predicted_xy':np.asarray(predicted).tolist(),
                        'observed_xy':row['observed_xy'],'error_px':errors.tolist()})
    return {'candidate_world_xy_offset':delta.tolist(),'world_to_image':[matrix.tolist() for matrix in corrected],
            'landmark_residuals':results,'absolute_map_alignment_verified':False,'live_usable':False}


def fit(data, ground_z, *, allow_vertical_shift):
    width,height=data['viewport']
    if not (640<=width<=4096 and 480<=height<=2160 and math.isfinite(ground_z)):
        raise ValueError('ground_view_viewport_or_height')
    p0,p1=(view['position'] for view in data['views'])
    for position in (p0,p1):validate_position(position)
    cameras=[np.array(p['camera_xyz']) for p in (p0,p1)]
    yaws=[math.degrees(math.atan2(*(np.array(p['look_at_xy'])-c[:2])[::-1]))
          for p,c in zip((p0,p1),cameras)]
    if np.linalg.norm(cameras[1]-cameras[0])<32:
        raise ValueError('ground_view_camera_baseline_required')
    pairs={}
    for role in ('fit','check'):
        item=data['matches'][role]
        first,second=(np.array(item[key],dtype=float) for key in ('first_xy','second_xy'))
        if (first.shape!=second.shape or first.ndim!=2 or first.shape[1]!=2 or not 6<=len(first)<=128
                or any(not np.isfinite(a).all() or np.any(a<0) or np.any(a>=[width,height])
                       or np.linalg.matrix_rank(a-a.mean(axis=0))<2 for a in (first,second))):
            raise ValueError('ground_view_invalid_feature_pairs')
        pairs[role]=(first,second)
    if set(map(tuple,pairs['fit'][0]))&set(map(tuple,pairs['check'][0])):
        raise ValueError('ground_view_feature_roles_overlap')

    def matrices(params):
        pitch,focal,cy=params
        return [plane_matrix(c,yaw,pitch,focal,(width/2,cy),ground_z) for c,yaw in zip(cameras,yaws)]

    def residuals(params,role):
        first,second=pairs[role]
        world_a,world_b=matrices(params)
        transfer=world_b@np.linalg.inv(world_a)
        return apply(transfer,first)-second

    def score(params):
        return np.mean(residuals(params,'fit')**2)

    pitch=np.arange(42.,77.,2.)
    focal=np.arange(.62,1.32,.045)*height
    vertical=np.arange(.35,.67,.035)*height if allow_vertical_shift else [height/2]
    best=min(product(pitch,focal,vertical),key=score)
    offsets=list(product(range(-2,3),range(-2,3),range(-2,3) if allow_vertical_shift else [0]))
    steps=((.8,24,16),(.25,8,5),(.075,2,1.5),(.02,.6,.4))
    for step in steps:
        scaled=np.array(step)*[1,height/1440,height/1440]
        best=min((np.array(best)+scaled*t for t in offsets),key=score)
    # Refine along correlated pitch/focal/center directions rather than
    # assuming that a small change to any single axis improves the fit.
    steps=np.diag([.01,.1,.1])[:2+allow_vertical_shift]
    for _ in range(12):
        residual=residuals(best,'fit').ravel()
        jacobian=np.column_stack([(residuals(best+d,'fit').ravel()-residual)/np.linalg.norm(d)
                                  for d in steps])
        update=np.linalg.lstsq(jacobian,-residual,rcond=1e-8)[0]
        if not allow_vertical_shift:update=np.r_[update,0.]
        candidates=[best+update*step for step in (1.,.5,.25,.125)]
        improved=min([best]+[p for p in candidates if 30<=p[0]<=82 and .45*height<=p[1]<=1.8*height
                             and .25*height<=p[2]<=.75*height],key=score)
        if np.linalg.norm(improved-best)<1e-5:break
        best=improved
    first,second=matrices(best)
    return {'ground_z':ground_z,'pitch_degrees':float(best[0]),'focal_px':float(best[1]),
            'principal_point_xy':[width/2,float(best[2])],
            'world_to_image':[first.tolist(),second.tolist()],
            'residuals':{role:{'count':len(e),'rmse_px':float(np.sqrt(np.mean(e**2))),
                         'max_px':float(e.max()),'errors_px':e.tolist()}
                         for role in pairs for e in (np.linalg.norm(residuals(best,role),axis=1),)},
            'absolute_map_alignment_verified':False,'live_usable':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--ground-z',type=float,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    path=checked_input(ROOT,args.input)
    with path.open('rb') as stream:raw=stream.read(128*1024+1)
    if len(raw)>128*1024:raise ValueError('ground_view_input_size')
    data=json.loads(raw)
    result={'kind':'relative_ground_view_fit','input_sha256':hashlib.sha256(raw).hexdigest(),
            'centered':fit(data,args.ground_z,allow_vertical_shift=False),
            'vertical_principal_point_free':fit(data,args.ground_z,allow_vertical_shift=True),
            'absolute_anchor_checks_required':True,'live_usable':False}
    with new_binary_output(checked_output(ROOT,args.output)) as stream:
        stream.write((json.dumps(result,indent=2,allow_nan=False)+'\n').encode())
    print(json.dumps({key:result[key]['residuals']['check']['max_px']
                      for key in ('centered','vertical_principal_point_free')}))


if __name__=='__main__':main()
