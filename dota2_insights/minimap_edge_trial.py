"""Bounded offline measured-line hypotheses; never a certified camera detector."""
import cv2
import numpy as np


def extract(crop):
    result = dict(version='fragmented_minimap_edges_trial_v1', status='unavailable',
                  reason='invalid_crop', hypotheses=[], coordinate_accuracy_verified=False,
                  outline_identity_verified=False, production_ready=False)
    if (not isinstance(crop, np.ndarray) or crop.dtype != np.uint8
            or crop.ndim != 3 or crop.shape[2] != 3
            or not 100 <= min(crop.shape[:2]) <= max(crop.shape[:2]) <= 1024):
        return result
    image = cv2.resize(crop, (380, 380), interpolation=cv2.INTER_AREA)
    background = cv2.medianBlur(image, 15).astype(np.int16)
    channels = image.astype(np.int16)
    delta = channels - background
    mask = ((2 * delta[:, :, 0] - delta[:, :, 1] - delta[:, :, 2] > 20)
            & (channels[:, :, 0] > 70)
            & (channels[:, :, 0] > channels[:, :, 2] - 5)).astype(np.uint8) * 255
    # Do not close gaps into invented pixels. Hough only proposes lines;
    # support and fits below use original selected pixels.
    hough = cv2.HoughLinesP(mask, 1, np.pi / 360, 8, minLineLength=9, maxLineGap=5)
    result.update(reason='no_line_fragments', selected_pixels=int(np.count_nonzero(mask)))
    if hough is None:
        return result
    yy, xx = np.nonzero(mask)
    pixels = np.column_stack([xx, yy]).astype(float)
    groups = [[], [], []]  # bottom, left, right
    for raw in hough.reshape(-1, 4)[:256]:
        a, b = np.asarray(raw, float).reshape(2, 2)
        dx, dy = b - a
        if abs(dy) <= 2 and abs(dx) >= 15:
            group = 0
        elif abs(dy) >= 8 and .18 <= abs(dx / dy) <= .85:
            group = 1 if dx / dy > 0 else 2
        else:
            continue
        direction = (b - a) / np.linalg.norm(b - a)
        offset = pixels - a
        distance = abs(offset[:, 0] * direction[1] - offset[:, 1] * direction[0])
        along = offset @ direction
        chosen = pixels[(distance <= 1.5) & (along >= -7)
                        & (along <= np.linalg.norm(b - a) + 7)]
        if len(chosen) < 12:
            continue
        vx, vy, cx, cy = cv2.fitLine(chosen.astype(np.float32), cv2.DIST_L1, 0, .01, .01).ravel()
        unit = np.array([vx, vy]); center = np.array([cx, cy])
        offset = chosen - center
        t = offset @ unit
        ends = np.array([center + unit * t.min(), center + unit * t.max()])
        ends = ends[np.argsort(ends[:, 0] if group == 0 else ends[:, 1])]
        length = float(np.linalg.norm(ends[1] - ends[0]))
        if length < (20 if group == 0 else 10):
            continue
        # Actual one-pixel support along the fitted segment, including gaps.
        scan = np.rint(np.linspace(*ends, max(12, int(length)))).astype(int)
        supported = [mask[max(0,y-1):min(380,y+2),max(0,x-1):min(380,x+2)].any() for x,y in scan]
        if np.mean(supported) < .65:
            continue
        if any(np.max(np.linalg.norm(ends - old, axis=1)) < 4 for old in groups[group]):
            continue
        groups[group].append(ends)
        groups[group] = sorted(groups[group], key=lambda e: -np.linalg.norm(e[1]-e[0]))[:24]
    result['fragment_groups'] = dict(zip(('bottom', 'left', 'right'), map(len, groups)))
    result['reason'] = 'incomplete_measured_edges'
    hypotheses = []
    for bottom in groups[0]:
        line = np.cross([*bottom[0], 1], [*bottom[1], 1])
        for left in groups[1]:
            for right in groups[2]:
                corners = []
                for side in (left, right):
                    p = np.cross(line, np.cross([*side[0], 1], [*side[1], 1]))
                    if abs(p[2]) < 1e-8:
                        break
                    corners.append(p[:2]/p[2])
                if len(corners) != 2:
                    continue
                bl, br = corners
                width = br[0] - bl[0]
                if not 25 <= width <= 80:
                    continue
                # Endpoints must be observed near the actual corner. Never
                # extend an off-map or missing side to fill a viewport.
                if (np.linalg.norm(bl-bottom[0]) > 6 or np.linalg.norm(br-bottom[1]) > 6
                        or np.linalg.norm(bl-left[1]) > 8 or np.linalg.norm(br-right[1]) > 8):
                    continue
                quad = np.array([left[0], right[0], br, bl])
                if (np.min(quad) < 4 or np.max(quad) > 375
                        or min(bl[1]-left[0,1], br[1]-right[0,1]) < 10):
                    continue
                if any(np.max(np.linalg.norm(quad - old, axis=1)) < 5 for old in hypotheses):
                    continue
                hypotheses.append(quad)
                if len(hypotheses) >= 12:
                    break
            if len(hypotheses) >= 12:
                break
        if len(hypotheses) >= 12:
            break
    result['hypotheses'] = [(q / 379).tolist() for q in hypotheses]
    if hypotheses:
        # Even a unique shape remains an unverified color/geometry hypothesis.
        result.update(status='candidate_only', reason='unique_measured_hypothesis' if len(hypotheses)==1 else 'ambiguous_measured_hypotheses')
    return result
