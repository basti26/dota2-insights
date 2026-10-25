"""Bounded sample reads and human annotations with immutable image references."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re

from .settings import atomic_json


class SampleDataset:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        with (self.folder / "session.json").open("rb") as handle:
            content = handle.read(256 * 1024 + 1)
        if len(content) > 256 * 1024:
            raise ValueError("minimap_session_too_large")
        self.manifest = json.loads(content)
        if not isinstance(self.manifest, dict) or self.manifest.get("scope") != "standalone_minimap_collection":
            raise ValueError("not_a_minimap_sample_session")
        self.rows = []
        self.cache = OrderedDict()
        checksum = hashlib.sha256()
        path = self.folder / "samples.jsonl"
        if path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("sample_index_too_large")
        last_sequence = 0
        last_elapsed = -1
        capture_gap = False
        with path.open("rb") as handle:
            for line_number, line in enumerate(handle, 1):
                if len(line) > 256 * 1024 or line_number > 100_000:
                    raise ValueError("sample_index_limit")
                checksum.update(line)
                try:
                    row = json.loads(line)
                except ValueError:
                    # A final half-written line is not a usable sample.
                    if not line.endswith(b"\n"):
                        break
                    raise
                if not isinstance(row, dict):
                    raise ValueError("invalid_sample_row")
                if row.get("kind") == "capture_status":
                    capture_gap = True
                    continue
                if row.get("kind") != "sample":
                    raise ValueError("unknown_minimap_row_kind")
                sequence = row.get("sequence")
                elapsed = row.get("elapsed_s")
                if (type(sequence) is not int or sequence <= last_sequence
                        or type(elapsed) not in {int, float} or not last_elapsed < elapsed < 24 * 3600
                        or not re.fullmatch(r"frames/frame-[0-9]{6}\.png", str(row.get("image", "")))
                        or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("image_sha256", "")))):
                    raise ValueError("invalid_sample_reference_or_order")
                validity = row.get("map_validity")
                if validity is None and "map_validity" not in row and self.manifest.get("schema_version", 1) == 1:
                    validity = {"state": "legacy_unchecked", "accepted": None,
                                "reason": "legacy_record_has_no_per_frame_validity"}
                if (not isinstance(validity, dict)
                        or validity.get("state") not in {"valid_map", "invalid_map", "uncertain", "legacy_unchecked"}
                        or validity.get("state") != "legacy_unchecked" and (
                            type(validity.get("accepted")) is not bool
                            or validity["accepted"] != (validity["state"] == "valid_map"))
                        or validity.get("state") == "legacy_unchecked" and (
                            self.manifest.get("schema_version", 1) != 1 or validity.get("accepted") is not None)):
                    raise ValueError("invalid_sample_map_validity")
                self.rows.append({**row, "capture_gap_before": capture_gap, "map_validity": validity})
                capture_gap = False
                last_sequence, last_elapsed = sequence, elapsed
                if len(self.rows) > 72000:
                    raise ValueError("too_many_minimap_samples")
        self.index_sha256 = checksum.hexdigest()
        if not self.rows:
            raise ValueError("no_saved_minimap_samples")

    def image(self, row):
        import cv2
        import numpy as np
        path = (self.folder / row["image"]).resolve()
        if path.parent != (self.folder / "frames").resolve():
            raise ValueError("image_reference_outside_session")
        signature = (path.stat().st_size, path.stat().st_mtime_ns, row["image_sha256"])
        cached = self.cache.get(path)
        if cached is not None and cached[0] == signature:
            self.cache.move_to_end(path)
            return cached[1].copy()
        with path.open("rb") as handle:
            content = handle.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != row["image_sha256"]:
            raise ValueError("sample_image_changed")
        # Bound dimensions before decoding.
        if content[:8] != b"\x89PNG\r\n\x1a\n" or len(content) < 24:
            raise ValueError("sample_is_not_png")
        width, height = int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
        if not 64 <= width <= 1024 or not 64 <= height <= 1024:
            raise ValueError("sample_image_size_limit")
        image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (height, width):
            raise ValueError("sample_image_decode_failed")
        roi = row.get("roi")
        if not isinstance(roi, list) or len(roi) != 4 or roi[2:] != [width, height]:
            raise ValueError("sample_geometry_mismatch")
        if row.get("pixel_sha256") and hashlib.sha256(memoryview(image)).hexdigest() != row["pixel_sha256"]:
            raise ValueError("sample_pixels_changed")
        self.cache[path] = (signature, image)
        while len(self.cache) > 4:
            self.cache.popitem(last=False)
        return image.copy()


def read_annotations(folder):
    path = Path(folder) / "annotations.json"
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        content = handle.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("annotations_too_large")
    value = json.loads(content)
    if not isinstance(value, dict) or value.get("schema_version") not in {1, 2} or not isinstance(value.get("frames"), dict):
        raise ValueError("invalid_minimap_annotations")
    return value["frames"]


MAP_VALIDITY = {"valid_map", "invalid_map", "uncertain"}
DISPLAY_MODES = {"names", "dots", "portraits", "arrows", "mixed", "unknown"}
OBJECT_KINDS = {"hero_position", "name_text", "nonhero", "uncertain_region"}


def hero_position_box(obj):
    """Return only position evidence; a name's text box is never a position."""
    if obj.get("kind") not in {None, "hero_position"}:
        return None
    return obj.get("position_box", obj.get("box"))


def complete_human_review(frame):
    """Legacy reviewed=true and partial/assistant labels cannot establish truth."""
    return bool(isinstance(frame, dict) and frame.get("review_scope") == "complete_frame"
                and frame.get("confirmation_status") == "human_confirmed"
                and frame.get("source") == "manual_annotation"
                and frame.get("map_validity") == "valid_map"
                and isinstance(frame.get("objects"), list)
                and all(isinstance(obj, dict) and obj.get("source") == "manual_annotation"
                        and obj.get("confirmation_status") == "human_confirmed"
                        for obj in frame["objects"]))


def _checked_box(box, image):
    if not isinstance(box, list) or len(box) != 4 or any(type(n) is not int for n in box):
        raise ValueError("invalid_annotation_box")
    x, y, width, height = box
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > image.shape[1] or y + height > image.shape[0]:
        raise ValueError("annotation_outside_image")
    return list(box)


def save_annotation(dataset, row, objects, *, review_scope="partial", map_validity="uncertain",
                    display_mode="unknown", evidence=""):
    """Save labels entered by a human; full-frame review must be explicit.

    Assistant proposals belong in a separate derived reference-set notes file.
    Existing callers remain partial until the human explicitly reviews the frame.
    """
    image = dataset.image(row)
    if not isinstance(objects, list) or len(objects) > 100:
        raise ValueError("invalid_manual_objects")
    if (review_scope not in {"partial", "complete_frame"} or map_validity not in MAP_VALIDITY
            or display_mode not in DISPLAY_MODES or not isinstance(evidence, str) or len(evidence) > 2000):
        raise ValueError("invalid_annotation_review_scope")
    clean = []
    for obj in objects:
        if not isinstance(obj, dict):
            raise ValueError("invalid_manual_object")
        kind, name = obj.get("kind", "hero_position"), obj.get("hero_name")
        item_evidence = obj.get("evidence", "")
        if (kind not in OBJECT_KINDS or name is not None and (not isinstance(name, str) or not 1 <= len(name) <= 64)
                or not isinstance(item_evidence, str) or len(item_evidence) > 2000):
            raise ValueError("invalid_manual_object")
        item = {"kind": kind, "source": "manual_annotation", "confirmation_status": "human_confirmed",
                "evidence": item_evidence, "hero_name": name, "hero_id": None}
        if kind == "hero_position":
            if (obj.get("state") not in {"visible", "occluded", "missing_fog", "dead"}
                    or obj.get("team_relation") not in {"ally", "enemy", "unknown"}):
                raise ValueError("invalid_manual_object")
            position = _checked_box(hero_position_box(obj), image)
            item.update(position_box=position, box=position, state=obj["state"], team_relation=obj["team_relation"],
                        uncertain=bool(obj.get("uncertain", False)),
                        position_uncertain=bool(obj.get("position_uncertain", False)))
            if obj.get("text_box") is not None:
                item["text_box"] = _checked_box(obj["text_box"], image)
        elif kind == "name_text":
            item.update(text_box=_checked_box(obj.get("text_box", obj.get("box")), image),
                        uncertain=bool(obj.get("uncertain", name is None)))
        else:
            item.update(box=_checked_box(obj.get("box"), image), uncertain=kind == "uncertain_region")
            item["category"] = str(obj.get("category", "unknown"))[:64]
        clean.append(item)
    annotations = read_annotations(dataset.folder)
    annotations[str(row["sequence"])] = {"sequence": row["sequence"], "image_sha256": row["image_sha256"],
        "elapsed_s": row["elapsed_s"], "objects": clean, "reviewed": review_scope == "complete_frame",
        "review_scope": review_scope, "map_validity": map_validity, "display_mode": display_mode,
        "confirmation_status": "human_confirmed", "evidence": evidence,
        "source": "manual_annotation", "coordinate_basis": "original_minimap_pixels"}
    atomic_json(dataset.folder / "annotations.json", {"schema_version": 2, "frames": annotations})
