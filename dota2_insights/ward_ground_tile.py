"""Fit a tile-local ground plane with separate world-position checks."""

import math


CHECK_LIMIT_PX = 2.0
PLANE_HEIGHT_LIMIT = 4.0


def _project(world_xy, matrix):
    import numpy as np

    mapped = np.column_stack((world_xy,np.ones(len(world_xy))))@matrix.T
    if not np.isfinite(mapped).all() or np.any(np.abs(mapped[:,2])<1e-9):
        return None
    return mapped[:,:2]/mapped[:,2,None]


def fit(controls, plane_z=None):
    """Controls use centered world XYZ and half-resolution tile pixels."""
    import cv2
    import numpy as np

    if (plane_z is not None and not math.isfinite(plane_z)) or not 0<=len(controls)<=128:
        raise ValueError('ground_tile_control_scope')
    if any(item['role'] not in ('fit','check') for item in controls):
        raise ValueError('ground_tile_control_role')
    result = {'status':'insufficient_ground_controls','world_xy_to_tile_xy':None,
              'plane_z':plane_z,'world_alignment_verified':False,'live_usable':False}
    rows = {role:[row for row in controls if row['role']==role] for role in ('fit','check')}
    result['fit_count'],result['check_count'] = (len(rows[role]) for role in ('fit','check'))
    if len(rows['fit'])<4 or len(rows['check'])<2:
        return result
    world = np.asarray([row['world_xyz'] for row in controls],dtype=float)
    tile = np.asarray([row['tile_xy'] for row in controls],dtype=float)
    if (world.shape!=(len(controls),3) or tile.shape!=(len(controls),2)
            or not np.isfinite(world).all() or not np.isfinite(tile).all()
            or np.any(np.abs(world[:,:2])>16384) or np.any((tile<0) | (tile>=1024))
            or len(np.unique(world[:,:2],axis=0))!=len(controls)):
        raise ValueError('ground_tile_independent_positions')
    training = np.array([row['role']=='fit' for row in controls])
    source, checks = world[training,:2],world[~training,:2]
    hull = cv2.convexHull(source.astype(np.float32))
    if cv2.contourArea(hull)<10000 or any(cv2.pointPolygonTest(hull,tuple(xy),False)<0 for xy in checks):
        return dict(result,status='insufficient_ground_plane_coverage')
    origin = source.mean(axis=0)
    design = np.column_stack((world[:,:2]-origin,np.ones(len(world))))
    if plane_z is None:
        coefficients = np.linalg.lstsq(design[training],world[training,2],rcond=None)[0]
    else:
        coefficients = np.array((0.,0.,plane_z))
    height_errors = np.abs(world[:,2]-design@coefficients)
    result['plane_world_xy_origin'] = origin.tolist()
    result['height_coefficients'] = coefficients.tolist()
    result['check_height_error_max_world_units'] = float(height_errors[~training].max())
    if np.any(height_errors>PLANE_HEIGHT_LIMIT):
        return dict(result,status='controls_off_ground_plane')
    matrix,_ = cv2.findHomography(source,tile[training],0)
    if matrix is None or not np.isfinite(matrix).all() or np.linalg.matrix_rank(matrix)<3:
        return dict(result,status='invalid_ground_plane')
    estimated = _project(world[:,:2],matrix)
    if estimated is None:
        return dict(result,status='invalid_ground_plane')
    errors = np.linalg.norm(estimated-tile,axis=1)
    result['fit_error_max_px'] = float(errors[training].max())
    result['check_error_max_px'] = float(errors[~training].max())
    if not np.isfinite(errors).all() or np.any(errors>CHECK_LIMIT_PX):
        return dict(result,status='ground_plane_controls_disagree')
    return dict(result,status='candidate_ground_plane',world_xy_to_tile_xy=matrix.tolist(),
                fit_hull_world_xy=hull[:,0].tolist())


def project_footprint(candidate, vertices):
    """Project four terrain corners and a centre within measured support."""
    import cv2
    import numpy as np

    if candidate['status']!='candidate_ground_plane':
        return None
    world = np.asarray(vertices,dtype=float)
    if world.shape!=(5,3) or not np.isfinite(world).all():
        return None
    origin = np.asarray(candidate.get('plane_world_xy_origin',(0.,0.)))
    coefficients = candidate.get('height_coefficients',(0.,0.,candidate['plane_z']))
    design = np.column_stack((world[:,:2]-origin,np.ones(len(world))))
    if np.any(np.abs(world[:,2]-design@coefficients)>PLANE_HEIGHT_LIMIT):
        return None
    hull = np.asarray(candidate['fit_hull_world_xy'],dtype=np.float32)
    if any(cv2.pointPolygonTest(hull,tuple(xy),False)<0 for xy in world[:,:2]):
        return None
    matrix = np.asarray(candidate['world_xy_to_tile_xy'],dtype=float)
    projected = _project(world[:,:2],matrix)
    if projected is None or not np.all((0<=projected) & (projected<1024)):
        return None
    return {'center_xy':projected[4].tolist(),'footprint_xy':projected[:4].tolist()}
