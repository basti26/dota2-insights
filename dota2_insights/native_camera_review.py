"""Validate passive Alt camera observations after closed-package admission."""
from datetime import datetime
import math
import re


def _number(value):
    return type(value) in (int,float) and math.isfinite(value)


def validate_position(value):
    if not isinstance(value,dict) or value.get('kind')!='alt_camera_position_v1':
        raise ValueError('native_camera_version')
    for key,length in (('camera_xyz',3),('look_at_xy',2)):
        vector=value.get(key)
        if not isinstance(vector,list) or len(vector)!=length or not all(
                _number(x) and abs(x)<=1e7 for x in vector):
            raise ValueError('native_camera_vector')
    for key in ('look_at_z','pitch_degrees','roll_degrees','fov_degrees','data_time_utc'):
        if value.get(key,'missing') is not None:raise ValueError('native_camera_unknown_geometry_claim')
    for key in ('atomic_pair_verified','request_association_verified','projection_calibrated','live_usable',
                'exact_log_flush_latency_verified','game_control_sent','capture_frame_alignment_verified'):
        if value.get(key) is not False:raise ValueError('native_camera_unverified_claim')
    for key in ('log_observed_monotonic','press_to_log_visible_candidate_ms'):
        if not _number(value.get(key)) or value[key]<0:raise ValueError('native_camera_clock')
    if value['press_to_log_visible_candidate_ms']>750:raise ValueError('native_camera_receive_window')
    if type(value.get('alt_epoch')) is not int or value['alt_epoch']<1:raise ValueError('native_camera_epoch')
    if type(value.get('log_generation')) is not int or value['log_generation']<1:raise ValueError('native_camera_generation')
    bounds=value.get('visible_in_read_byte_range')
    if (not isinstance(bounds,list) or len(bounds)!=2 or any(type(v) is not int for v in bounds)
            or not 0<=bounds[0]<bounds[1] or bounds[1]-bounds[0]>16384):
        raise ValueError('native_camera_byte_range')
    if not isinstance(value.get('read_chunk_sha256'),str) or not re.fullmatch('[a-f0-9]{64}',value['read_chunk_sha256']):
        raise ValueError('native_camera_digest')
    try:
        stamp=datetime.fromisoformat(value['received_at_utc'])
        if stamp.utcoffset() is None:raise ValueError('timezone')
    except (KeyError,TypeError,ValueError):raise ValueError('native_camera_receipt_clock')
    camera,xy=value['camera_xyz'],value['look_at_xy']
    yaw=math.degrees(math.atan2(xy[1]-camera[1],xy[0]-camera[0]))
    if (math.hypot(xy[0]-camera[0],xy[1]-camera[1])<.001 or not _number(value.get('yaw_xy_degrees'))
            or abs(value['yaw_xy_degrees']-yaw)>1e-6):raise ValueError('native_camera_yaw')
    return value


def validate_snapshot(value):
    if value is None:return
    if not isinstance(value,dict) or value.get('version')!='alt_camera_position_v1':
        raise ValueError('native_camera_snapshot_version')
    if any(value.get(k) is not False for k in ('projection_ready','live_usable','game_control_sent')):
        raise ValueError('native_camera_snapshot_claim')
    if type(value.get('candidate_recent')) is not bool:raise ValueError('native_camera_snapshot_recent')
    if value['candidate_recent']:
        validate_position(value.get('candidate'))
        age=value.get('candidate_age_since_log_visible_s')
        if not _number(age) or not 0<=age<=.5:raise ValueError('native_camera_snapshot_age')
    elif value.get('candidate') is not None:raise ValueError('native_camera_stale_candidate')


def summary(count):
    return {'position_events':count,'complete_camera_pairs':0,'projection_ready':False,
            'camera_acquisition_time_verified':False,'log_flush_latency_verified':False,
            'capture_frame_alignment_verified':False,'world_to_screen_verified':False}
