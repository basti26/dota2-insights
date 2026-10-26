"""Competing resource-family silhouette matches; no semantic hero classification."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re

import cv2
import numpy as np

from .match_journals import _json, _read
from .minimap_component_probe import BANDS

FAMILIES = {
    'hero_marker': ('minimap_herocircle', 'minimap_herocircle_self', 'minimap_herocircle_self_highlight',
                    'minimap_heroinvis', 'minimap_enemyicon', 'minimap_enemyimage'),
    'hero_portrait_border': ('minimap_heroimage',),
    'ward_invis': ('minimap_ward_invis',), 'ward_obs': ('minimap_ward_obs',),
    'creep_dot': ('minimap_creep', 'minimap_siege'),
    'controlled_creep': ('minimap_controlledcreep',),
    'building_shapes': ('minimap_tower90', 'minimap_tower45', 'minimap_racks90', 'minimap_racks45', 'minimap_miscbuilding'),
    'camp_triangle': ('minimap_creepcamp', 'minimap_creepcamp_mid', 'minimap_creepcamp_big', 'minimap_creepcamp_ancient'),
    'courier': ('minimap_courier', 'minimap_courier_dire', 'minimap_courier_flying', 'minimap_courier_dire_flying'),
}
SIZES = tuple(range(8, 25, 2))
ANGLES = tuple(range(0, 360, 45))
PARAMETERS = {'sizes': SIZES, 'hero_angles_degrees': ANGLES, 'reference_alpha_threshold': 32,
    'reference_channel_mean_threshold': 80, 'minimum_display_pixels': 8,
    'minimum_shape_dice': .82, 'minimum_family_gap': .08,
    'score_scope': 'whole_patch_display_color_dice_not_probability'}


def variants(rgba, *, angles=(0,), sizes=SIZES):
    if (not isinstance(rgba, np.ndarray) or rgba.dtype != np.uint8 or rgba.ndim != 3
            or rgba.shape[2] != 4 or not 1 <= min(rgba.shape[:2]) <= max(rgba.shape[:2]) <= 256
            or not 1 <= len(sizes) <= 16 or any(type(s) is not int or not 4 <= s <= 32 for s in sizes)
            or not 1 <= len(angles) <= 16 or any(type(a) is not int or not 0 <= a < 360 for a in angles)):
        raise ValueError('template_match_bounded_rgba_variants_required')
    raw = ((rgba[:, :, 3] > 32) & (rgba[:, :, :3].mean(axis=2) > 80)).astype(np.uint8)
    if cv2.countNonZero(raw) < 4:
        return []
    padded = np.pad(raw, max(raw.shape))
    center = ((padded.shape[1] - 1) / 2, (padded.shape[0] - 1) / 2)
    result, seen = [], set()
    for angle in angles:
        rotated = cv2.warpAffine(padded, cv2.getRotationMatrix2D(center, angle, 1),
                                 (padded.shape[1], padded.shape[0]), flags=cv2.INTER_NEAREST)
        x, y, w, h = cv2.boundingRect(rotated)
        if not w or not h:
            continue
        shape = rotated[y:y + h, x:x + w]
        for size in sizes:
            width, height = max(1, round(w * size / max(w, h))), max(1, round(h * size / max(w, h)))
            mask = cv2.resize(shape, (width, height), interpolation=cv2.INTER_NEAREST)
            key = width, height, mask.tobytes()
            if key in seen or cv2.countNonZero(mask) < 4:
                continue
            seen.add(key)
            result.append({'angle_degrees': angle, 'longest_side_px': size, 'mask': mask})
    return result


def load_bank(folder):
    from PIL import Image
    folder = Path(folder).resolve(strict=True)
    raw = _read(folder / 'catalog.json', 2 * 1024**2)
    catalog = _json(raw)
    if catalog.get('kind') != 'minimap_original_icon_catalog' or type(catalog.get('schema_version')) is not int or catalog['schema_version'] != 1:
        raise ValueError('template_match_catalog_invalid')
    entries = catalog.get('entries')
    if not isinstance(entries, list) or len(entries) > 512:
        raise ValueError('template_match_catalog_limit')
    by_name = {}
    for entry in entries:
        name = entry.get('name')
        if not isinstance(name, str) or name in by_name:
            raise ValueError('template_match_catalog_duplicate_name')
        by_name[name] = entry
    bank, missing, bindings = [], [], []
    for family, names in FAMILIES.items():
        for name in names:
            if name not in by_name:
                missing.append(name)
                continue
            entry = by_name[name]
            if entry.get('file') != 'icons/' + name + '.png' or not re.fullmatch('[a-f0-9]{64}', str(entry.get('png_sha256', ''))):
                raise ValueError('template_match_icon_reference_invalid')
            path = folder / entry['file']
            if path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction() or not path.resolve().is_relative_to(folder):
                raise ValueError('template_match_icon_link_refused')
            content = _read(path, 2 * 1024**2)
            if hashlib.sha256(content).hexdigest() != entry['png_sha256']:
                raise ValueError('template_match_icon_sha_changed')
            with Image.open(BytesIO(content)) as icon:
                if icon.mode != 'RGBA' or not 1 <= min(icon.size) <= max(icon.size) <= 256 or list(icon.size) != entry.get('size'):
                    raise ValueError('template_match_icon_geometry_changed')
                rgba = np.asarray(icon).copy()
                if hashlib.sha256(icon.tobytes()).hexdigest() != entry.get('rgba_pixel_sha256'):
                    raise ValueError('template_match_icon_pixels_changed')
            generated = variants(rgba, angles=ANGLES if family == 'hero_marker' else (0,))
            if not generated:
                missing.append(name)
            for item in generated:
                bank.append({**item, 'family': family, 'resource_name': name})
            bindings.append({'resource_name': name, 'png_sha256': entry['png_sha256']})
    if len(bank) > 4096:
        raise ValueError('template_match_bank_limit')
    if _read(folder / 'catalog.json', 2 * 1024**2) != raw:
        raise ValueError('template_match_catalog_changed')
    return {'templates': bank, 'missing_resources': missing, 'catalog_sha256': hashlib.sha256(raw).hexdigest(),
            'bindings': bindings, 'coverage_complete': not missing, 'parameters': PARAMETERS}


def match_patch(image_bgr, bank):
    if (not isinstance(image_bgr, np.ndarray) or image_bgr.dtype != np.uint8 or image_bgr.ndim != 3
            or image_bgr.shape[2] != 3 or not 4 <= min(image_bgr.shape[:2]) <= max(image_bgr.shape[:2]) <= 128
            or not isinstance(bank.get('templates'), list) or len(bank['templates']) > 4096):
        raise ValueError('template_match_bounded_patch_required')
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    observed = np.zeros(image_bgr.shape[:2], np.uint8)
    for low, high in BANDS.values():
        observed |= (cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8)) > 0).astype(np.uint8)
    foreground = int(cv2.countNonZero(observed))
    result = {'state': 'unknown', 'resource_family_candidate': None, 'ranked_families': [],
        'display_foreground_pixels': foreground, 'catalog_coverage_complete': bank.get('coverage_complete') is True,
        'family_gap': None, 'hero_id': None, 'team_relation': 'unknown', 'semantic_identity_verified': False,
        'score_is_probability': False, 'online_changes_applied': False}
    if foreground < PARAMETERS['minimum_display_pixels']:
        return {**result, 'reason': 'insufficient_display_color_pixels'}
    best = {}
    for template in bank['templates']:
        mask = template['mask']
        height, width = mask.shape
        if height > observed.shape[0] or width > observed.shape[1]:
            continue
        scores = cv2.matchTemplate(observed.astype(np.float32), mask.astype(np.float32), cv2.TM_CCORR)
        _, intersection, _, (x, y) = cv2.minMaxLoc(scores)
        score = min(1.0, max(0.0, 2 * intersection / (foreground + int(cv2.countNonZero(mask)))))
        family = template['family']
        row = {'family': family, 'resource_name': template['resource_name'], 'score': round(score, 6),
            'angle_degrees': template['angle_degrees'], 'longest_side_px': template['longest_side_px'],
            'rect': [x, y, width, height]}
        if family not in best or row['score'] > best[family]['score']:
            best[family] = row
    ranked = sorted(best.values(), key=lambda row: (-row['score'], row['family']))
    result['ranked_families'] = ranked
    if len(ranked) < 2:
        return {**result, 'reason': 'insufficient_competing_families'}
    gap = ranked[0]['score'] - ranked[1]['score']
    result['family_gap'] = round(gap, 6)
    if bank.get('coverage_complete') is not True:
        return {**result, 'reason': 'incomplete_reference_bank'}
    if ranked[0]['score'] < PARAMETERS['minimum_shape_dice']:
        return {**result, 'reason': 'weak_shape_match'}
    if gap < PARAMETERS['minimum_family_gap']:
        return {**result, 'reason': 'competing_family_ambiguity'}
    return {**result, 'state': 'shape_candidate_only', 'resource_family_candidate': ranked[0]['family'],
            'reason': 'unverified_resource_shape_and_margin'}
