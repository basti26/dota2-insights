"""Image-only terrain registration with a preassigned spatial development split.

No world origins, tower fit, glyph labels or predicted map locations are inputs.
This is not a final landmark holdout or a world-to-pixel certificate.
"""
import math

from .map_evaluation import summary


POLICY = {'version': 'terrain-image-development-v1', 'feature_limit': 3000,
          'ratio': .75, 'split_grid': 6, 'minimum_border_px': 6,
          'keypoint_border_size_multiplier': 1.5, 'minimum_train_matches': 12,
          'minimum_train_inliers': 12, 'minimum_train_inlier_fraction': .6,
          'ransac_threshold_native_px': 2, 'ransac_iterations': 2000,
          'ransac_confidence': .99, 'refine_iterations': 10, 'seed': 17}


def image(value):
    import numpy as np
    if (not isinstance(value, np.ndarray) or value.dtype != np.uint8 or value.ndim != 3
            or value.shape[2] != 3 or not 64 <= min(value.shape[:2]) <= max(value.shape[:2]) <= 1024):
        raise ValueError('registration_bounded_native_rgb_required')
    return value


def split_point(x, y, size, width, height):
    """Source-only 6x6 checkerboard; skip border-crossing feature footprints."""
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y, size, width, height))
            or size <= 0 or width <= 0 or height <= 0 or not 0 <= x < width or not 0 <= y < height):
        raise ValueError('registration_keypoint_invalid')
    cell_w, cell_h = width/POLICY['split_grid'], height/POLICY['split_grid']
    col, row = int(x/cell_w), int(y/cell_h)
    border = max(POLICY['minimum_border_px'], size*POLICY['keypoint_border_size_multiplier'])
    distance = min(x-col*cell_w, (col+1)*cell_w-x, y-row*cell_h, (row+1)*cell_h-y)
    if distance < border:
        return 'excluded_border'
    return 'train' if (col+row) % 2 == 0 else 'validation'


def register(source, target):
    import cv2
    import numpy as np
    source, target = image(source), image(target)
    detector = cv2.SIFT_create(nfeatures=POLICY['feature_limit'])
    a, da = detector.detectAndCompute(cv2.cvtColor(source, cv2.COLOR_RGB2GRAY), None)
    b, db = detector.detectAndCompute(cv2.cvtColor(target, cv2.COLOR_RGB2GRAY), None)
    matches = []
    if da is not None and db is not None and len(a) >= 2 and len(b) >= 2:
        matcher = cv2.BFMatcher()
        forward, backward = matcher.knnMatch(da, db, k=2), matcher.knnMatch(db, da, k=2)
        reverse = {p[0].queryIdx: p[0].trainIdx for p in backward
                   if len(p) == 2 and p[0].distance < POLICY['ratio']*p[1].distance}
        matches = [p[0] for p in forward if len(p) == 2 and p[0].distance < POLICY['ratio']*p[1].distance
                   and reverse.get(p[0].trainIdx) == p[0].queryIdx]
    rows = []
    for m in sorted(matches, key=lambda m: (m.queryIdx, m.trainIdx)):
        x, y = map(float, a[m.queryIdx].pt)
        role = split_point(x, y, float(a[m.queryIdx].size), source.shape[1], source.shape[0])
        rows.append({'source_keypoint_id': m.queryIdx, 'target_keypoint_id': m.trainIdx,
                     'source_xy': [x, y], 'target_xy': list(map(float, b[m.trainIdx].pt)),
                     'source_size_px': float(a[m.queryIdx].size), 'role': role,
                     'region': [min(2, int(y/source.shape[0]*3)), min(2, int(x/source.shape[1]*3))]})
    train = [r for r in rows if r['role'] == 'train']
    validation = [r for r in rows if r['role'] == 'validation']
    result = {'schema_version': 1, 'kind': 'image_only_spatial_development_registration',
              'policy': dict(POLICY), 'opencv_version': cv2.__version__,
              'source_size': [source.shape[1], source.shape[0]], 'target_size': [target.shape[1], target.shape[0]],
              'source_keypoints': len(a), 'target_keypoints': len(b), 'mutual_matches': len(rows),
              'train_matches': len(train), 'validation_matches': len(validation),
              'excluded_border_matches': sum(r['role'] == 'excluded_border' for r in rows),
              'fit_basis': 'mutual_descriptor_train_matches_only', 'matrix': None,
              'status': 'insufficient_training_evidence', 'rows': rows,
              'validation_errors_native_px': summary([]), 'validation_errors_1024px': summary([]),
              'final_holdout_used': False, 'world_coordinates_read': False,
              'formal_p2_pass': False, 'production_ready': False}
    if len(train) < POLICY['minimum_train_matches']:
        return result
    # Match and split are complete before the robust model sees any pixel pairs.
    cv2.setRNGSeed(POLICY['seed'])
    matrix, mask = cv2.estimateAffine2D(np.float32([r['source_xy'] for r in train]),
        np.float32([r['target_xy'] for r in train]), method=cv2.RANSAC,
        ransacReprojThreshold=POLICY['ransac_threshold_native_px'], maxIters=POLICY['ransac_iterations'],
        confidence=POLICY['ransac_confidence'], refineIters=POLICY['refine_iterations'])
    if matrix is None or mask is None or not np.isfinite(matrix).all() or np.linalg.det(matrix[:, :2]) <= 0:
        result['status'] = 'no_valid_orientation_preserving_affine'
        return result
    inliers = int(mask.sum())
    result['train_inliers'] = inliers
    if inliers < POLICY['minimum_train_inliers'] or inliers/len(train) < POLICY['minimum_train_inlier_fraction']:
        result['status'] = 'insufficient_training_consensus'
        return result
    result['matrix'] = matrix.tolist()
    errors, normalized_errors = [], []
    for row in rows:
        prediction = matrix @ np.array([*row['source_xy'], 1])
        delta = prediction-np.array(row['target_xy'])
        row['error_native_px'] = float(np.linalg.norm(delta))
        row['error_1024px'] = float(np.linalg.norm(delta/np.array([target.shape[1], target.shape[0]]))*1024)
        if row['role'] == 'validation':
            # Every preassigned validation match is counted, including gross
            # mismatches. There is no RANSAC/outlier filtering on validation.
            errors.append(row['error_native_px']); normalized_errors.append(row['error_1024px'])
    result['status'] = 'development_candidate_estimated'
    result['validation_errors_native_px'] = summary(errors)
    result['validation_errors_1024px'] = summary(normalized_errors)
    result['validation_regions'] = {f'{r}_{c}': summary([x['error_native_px'] for x in validation if x['region'] == [r, c]])
                                    for r in range(3) for c in range(3)}
    corners = np.float64([[0, 0, 1], [source.shape[1], 0, 1],
                          [source.shape[1], source.shape[0], 1], [0, source.shape[0], 1]])
    result['texture_edge_corners_in_target'] = (corners @ matrix.T).tolist()
    result['texture_edge_bounds_in_target'] = {
        'left': min(p[0] for p in result['texture_edge_corners_in_target']),
        'top': min(p[1] for p in result['texture_edge_corners_in_target']),
        'right': max(p[0] for p in result['texture_edge_corners_in_target']),
        'bottom': max(p[1] for p in result['texture_edge_corners_in_target'])}
    return result
