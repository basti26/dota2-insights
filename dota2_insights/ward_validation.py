"""Reject ward identities contradicted by their own crop's clear color.

This is a veto only. Color alone establishes neither an observer, a sentry nor
a combined ward identity. Dim/monochrome crops gain no identity from this check.
"""
import cv2
import numpy as np

WARD_COLOR_GUARD_VERSION = 'ward_tone_conflict_v2'


def _ward_tones(cell):
    height, width = cell.shape[:2]
    if height < 8 or width < 8:
        return None
    hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
    mask = np.zeros((height, width), dtype=bool)
    # Exclude frame edges and the lower-right charge number.
    mask[round(height * .1):round(height * .9), round(width * .1):round(width * .9)] = True
    mask[round(height * .65):, round(width * .65):] = False
    area = int(mask.sum())
    if not area:
        return None
    saturated = mask & (hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 45)
    gold = np.count_nonzero(saturated & (hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40))
    blue = np.count_nonzero(saturated & (hsv[:, :, 0] >= 65) & (hsv[:, :, 0] <= 115))
    return gold / area, blue / area


def sentry_color_conflict(cell):
    tones = _ward_tones(cell)
    return bool(tones is not None and tones[0] >= .20 and tones[1] <= .05)


def observer_color_conflict(cell):
    """A clearly blue crop cannot gain a free observer identity."""
    tones = _ward_tones(cell)
    return bool(tones is not None and tones[1] >= .20 and tones[0] <= .05)
