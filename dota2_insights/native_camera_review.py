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


def validate_orientation(value):
    """Recompute a non-atomic reported target candidate, never authorize projection."""
    receipt_keys = ('alt_epoch', 'log_generation', 'log_observed_monotonic',
        'press_to_log_visible_candidate_ms', 'read_chunk_sha256', 'received_at_utc',
        'visible_in_read_byte_range', 'exact_log_flush_latency_verified',
        'game_control_sent', 'capture_frame_alignment_verified')
    expected = {'kind', 'position_candidate', 'look_at_xyz', 'orientation_candidate',
        'data_time_utc', 'atomic_pair_verified', 'request_association_verified',
        'projection_calibrated', 'live_usable', *receipt_keys}
    if (not isinstance(value, dict) or set(value) != expected
            or value.get('kind') != 'alt_camera_orientation_candidate_v1'):
        raise ValueError('native_orientation_version_or_fields')
    base = validate_position(value['position_candidate'])
    for key in ('atomic_pair_verified', 'request_association_verified', 'projection_calibrated',
                'live_usable', 'exact_log_flush_latency_verified', 'game_control_sent',
                'capture_frame_alignment_verified'):
        if value[key] is not False:
            raise ValueError('native_orientation_unverified_claim')
    if value['data_time_utc'] is not None:
        raise ValueError('native_orientation_acquisition_claim')
    validate_position({**base, **{key: value[key] for key in receipt_keys}})
    if (value['alt_epoch'] != base['alt_epoch'] or value['log_generation'] != base['log_generation']
            or value['log_observed_monotonic'] < base['log_observed_monotonic']
            or value['press_to_log_visible_candidate_ms'] < base['press_to_log_visible_candidate_ms']
            or abs((value['log_observed_monotonic']-base['log_observed_monotonic'])*1000
                   - (value['press_to_log_visible_candidate_ms']-base['press_to_log_visible_candidate_ms'])) > 1e-5
            or any(value['visible_in_read_byte_range'][i] < base['visible_in_read_byte_range'][i]
                   for i in (0, 1))):
        raise ValueError('native_orientation_receipt_mismatch')
    target = value['look_at_xyz']
    if (not isinstance(target, list) or len(target) != 3
            or any(not _number(x) or abs(x) > 1e7 for x in target)
            or any(abs(target[i]-base['look_at_xy'][i]) > .1 for i in (0, 1))):
        raise ValueError('native_orientation_target_mismatch')
    delta = [target[i]-base['camera_xyz'][i] for i in range(3)]
    distance = math.hypot(*delta)
    if distance < .001:
        raise ValueError('native_orientation_degenerate')
    expected_derived = {'camera_to_target_distance': distance,
        'view_direction_unit': [x/distance for x in delta],
        'downward_pitch_degrees': math.degrees(math.atan2(-delta[2], math.hypot(*delta[:2]))),
        'yaw_xy_degrees': math.degrees(math.atan2(delta[1], delta[0])),
        'roll_degrees': None, 'fov_degrees': None, 'viewport': None, 'projection_calibrated': False}
    derived = value['orientation_candidate']
    if not isinstance(derived, dict) or set(derived) != set(expected_derived):
        raise ValueError('native_orientation_geometry_fields')
    for key in ('camera_to_target_distance', 'downward_pitch_degrees', 'yaw_xy_degrees'):
        if not _number(derived[key]) or abs(derived[key]-expected_derived[key]) > 1e-6:
            raise ValueError('native_orientation_geometry_mismatch')
    unit = derived['view_direction_unit']
    if (not isinstance(unit, list) or len(unit) != 3
            or any(not _number(x) or abs(x-expected_derived['view_direction_unit'][i]) > 1e-6
                   for i, x in enumerate(unit))
            or any(derived[key] is not None for key in ('roll_degrees', 'fov_degrees', 'viewport'))
            or derived['projection_calibrated'] is not False):
        raise ValueError('native_orientation_geometry_claim')
    return value


def summary(count, orientation_count=0):
    return {'position_events':count,'complete_camera_pairs':0,'projection_ready':False,
            'orientation_candidate_events': orientation_count,
            'camera_acquisition_time_verified':False,'log_flush_latency_verified':False,
            'capture_frame_alignment_verified':False,'world_to_screen_verified':False}
