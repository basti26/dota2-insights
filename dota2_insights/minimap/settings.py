"""Private, explicitly calibrated minimap recording settings."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
from dataclasses import asdict, dataclass

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/minimap_learning.local.json"
RECORDS = ROOT / "diagnostics/minimap-learning"
LIBRARY = ROOT / "diagnostics/minimap-learning/template-library"
DEFAULT_TOTAL_BYTES = 8 * 1024 ** 3
MAX_RECORD_MINUTES = 240
MAX_RECORD_SECONDS = MAX_RECORD_MINUTES * 60
RECORDING_MODES = ("capture_only", "experimental_detection")


@dataclass(frozen=True)
class LabSettings:
    client_size: tuple[int, int]
    roi: tuple[int, int, int, int]
    sample_hz: float = 2.0
    max_minutes: int = 60
    enemy_heroes: tuple[str, ...] = ()
    valid_polygon: tuple[tuple[int, int], ...] = ()
    recording_mode: str = "capture_only"
    max_total_bytes: int = DEFAULT_TOTAL_BYTES
    record_every_attempt: bool = True
    record_context: bool = True
    context_interval_s: float = 15.0
    context_max_images: int = 1024
    context_max_bytes: int = 512 * 1024 ** 2
    rejected_max_images: int = 32
    record_marker_candidates: bool = True

    def __post_init__(self):
        if (not isinstance(self.client_size, tuple) or len(self.client_size) != 2
                or any(type(n) is not int or n <= 0 for n in self.client_size)
                or self.client_size[0] > 8192 or self.client_size[1] > 4320
                or math.prod(self.client_size) > 16_000_000):
            raise ValueError("invalid_client_size")
        if (not isinstance(self.roi, tuple) or len(self.roi) != 4
                or any(type(n) is not int for n in self.roi)):
            raise ValueError("invalid_roi")
        x, y, width, height = self.roi
        if (x < 0 or y < 0 or not 64 <= width <= 1024 or not 64 <= height <= 1024
                or x + width > self.client_size[0] or y + height > self.client_size[1]):
            raise ValueError("roi_outside_client_or_too_large")
        if type(self.sample_hz) not in {int, float} or not math.isfinite(self.sample_hz) or not .5 <= self.sample_hz <= 5:
            raise ValueError("sample_hz_must_be_0_5_to_5")
        if type(self.max_minutes) is not int or not 1 <= self.max_minutes <= MAX_RECORD_MINUTES:
            raise ValueError("invalid_record_duration")
        if (not isinstance(self.enemy_heroes, tuple) or len(self.enemy_heroes) > 5
                or len(set(self.enemy_heroes)) != len(self.enemy_heroes)
                or any(not isinstance(name, str) or not name.strip() or name != name.strip()
                       or len(name) > 64 or any(ord(c) < 32 for c in name) for name in self.enemy_heroes)):
            raise ValueError("invalid_manual_enemy_hero_pool")
        if (not isinstance(self.valid_polygon, tuple) or self.valid_polygon and not 3 <= len(self.valid_polygon) <= 16
                or any(not isinstance(point, tuple) or len(point) != 2
                       or any(type(n) is not int for n in point)
                       or not 0 <= point[0] < width or not 0 <= point[1] < height for point in self.valid_polygon)):
            raise ValueError("invalid_minimap_valid_polygon")
        if self.recording_mode not in RECORDING_MODES:
            raise ValueError("invalid_minimap_recording_mode")
        if (type(self.max_total_bytes) is not int
                or not 16 * 1024 ** 2 <= self.max_total_bytes <= 16 * 1024 ** 3):
            raise ValueError("invalid_minimap_storage_budget")
        if type(self.record_every_attempt) is not bool or type(self.record_context) is not bool:
            raise ValueError("invalid_minimap_evidence_switch")
        if type(self.record_marker_candidates) is not bool:
            raise ValueError("invalid_marker_recording_switch")
        if (type(self.context_interval_s) not in {int, float}
                or not math.isfinite(self.context_interval_s) or not 5 <= self.context_interval_s <= 60):
            raise ValueError("invalid_minimap_context_interval")
        if type(self.context_max_images) is not int or not 0 <= self.context_max_images <= 2048:
            raise ValueError("invalid_minimap_context_count")
        if type(self.context_max_bytes) is not int or not 1024 ** 2 <= self.context_max_bytes <= 1024 ** 3:
            raise ValueError("invalid_minimap_context_budget")
        if type(self.rejected_max_images) is not int or not 0 <= self.rejected_max_images <= 128:
            raise ValueError("invalid_minimap_rejected_count")


def load_settings():
    with CONFIG.open("rb") as handle:
        content = handle.read(16385)
    if len(content) > 16384:
        raise ValueError("minimap_config_too_large")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_minimap_key")
            result[key] = value
        return result
    raw = json.loads(content.decode("utf-8-sig"), object_pairs_hook=pairs)
    evidence_keys = {"record_every_attempt", "record_context", "context_interval_s", "context_max_images",
                     "context_max_bytes", "rejected_max_images", "record_marker_candidates"}
    if (not isinstance(raw, dict) or set(raw) - {"schema_version", "client_size", "roi", "sample_hz", "max_minutes", "enemy_heroes", "valid_polygon", "recording_mode", "max_total_bytes", *evidence_keys}
            or not {"schema_version", "client_size", "roi", "sample_hz", "max_minutes"} <= set(raw)
            or type(raw["schema_version"]) is not int or raw["schema_version"] not in {1, 2, 3, 4, 5, 6}
            or not isinstance(raw["client_size"], list) or not isinstance(raw["roi"], list)):
        raise ValueError("invalid_minimap_config")
    heroes = raw.get("enemy_heroes", [])
    if not isinstance(heroes, list):
        raise ValueError("invalid_manual_enemy_hero_pool")
    polygon = raw.get("valid_polygon", [])
    if not isinstance(polygon, list) or any(not isinstance(point, list) for point in polygon):
        raise ValueError("invalid_minimap_valid_polygon")
    return LabSettings(tuple(raw["client_size"]), tuple(raw["roi"]), raw["sample_hz"], raw["max_minutes"], tuple(heroes),
                       tuple(tuple(point) for point in polygon), raw.get("recording_mode", "capture_only"),
                       raw.get("max_total_bytes", DEFAULT_TOTAL_BYTES),
                       **{key: raw[key] for key in evidence_keys if key in raw})


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".minimap-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_settings(settings):
    checked = LabSettings(**asdict(settings))
    atomic_json(CONFIG, {"schema_version": 6, **asdict(checked)})
