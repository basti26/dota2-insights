"""Read stationary building positions from a bounded, offline replay window."""
from __future__ import annotations

import importlib.metadata
import math
from pathlib import Path
import re
import sys
import time

from .replay_archive import (GEM_COMMIT, GEM_VERSION, COORDINATE_BASIS,
                             _footer_match_id, _windows_dota_running, sha256_file)


def extract_landmarks(source, *, max_seconds=60):
    if not 1 <= max_seconds <= 120:
        raise ValueError("landmark_wall_time_limit_invalid")
    if sys.platform == "win32":
        if _windows_dota_running():
            raise ValueError("offline_landmarks_refused_while_dota_running")
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        priority_lowered = bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
    else:
        priority_lowered = False
    if importlib.metadata.version("gem-dota") != GEM_VERSION:
        raise ValueError("pinned_gem_version_required")
    from gem.parser import ReplayParser
    from gem.extractors._snapshots import _pos
    source = Path(source).resolve(strict=True)
    if source.suffix.lower() != ".dem":
        raise ValueError("dem_source_required")
    before = source.stat()
    if not 16 <= before.st_size <= 512 * 1024 ** 2:
        raise ValueError("bounded_replay_source_required")
    digest = sha256_file(source)
    match_id = _footer_match_id(source)
    if not match_id:
        raise ValueError("independent_replay_identity_required")
    parser = ReplayParser(source)
    started = time.monotonic()
    last_guard = started
    result = None

    def packet(tick):
        nonlocal result, last_guard
        elapsed = time.monotonic() - started
        if elapsed > max_seconds:
            raise ValueError("landmark_wall_time_limit")
        if sys.platform == "win32" and time.monotonic() - last_guard >= 5:
            last_guard = time.monotonic()
            if _windows_dota_running():
                raise ValueError("landmark_game_started")
        clock = parser.game_clock.game_time_at(tick)
        if clock is None or clock < 0 or result is not None:
            return
        if clock > 1:
            raise ValueError("initial_landmark_window_missing")
        rows = []
        table = parser.string_tables.get_by_name("EntityNames")
        for entity in parser.entity_manager.entities:
            if entity is None:
                continue
            classname = entity.get_class_name()
            if not any(word in classname for word in ("Tower", "Barracks", "Fort")):
                continue
            name = None
            for field in ("m_pEntity.m_nameStringTableIndex", "m_pEntity.m_nameStringableIndex"):
                index = entity.get(field)
                entry = table.items.get(index) if table is not None and type(index) is int else None
                if entry:
                    candidate = entry[0] if isinstance(entry, tuple) else entry
                    if isinstance(candidate, str) and re.fullmatch(r"(?:dota|npc_dota)_[a-z0-9_]{1,110}", candidate):
                        name = candidate
                        break
            pos, team = _pos(entity), entity.get("m_iTeamNum")
            if name is None or pos is None or team not in (2, 3) or any(not math.isfinite(v) for v in pos):
                continue
            rows.append({"name": name, "class_name": classname, "team": team,
                         "entity_index": entity.get_index(), "entity_serial": entity.get_serial(),
                         "world_x": pos[0], "world_y": pos[1],
                         "alive": entity.get("m_lifeState") == 0 if type(entity.get("m_lifeState")) is int else None})
        if not 2 <= len(rows) <= 128:
            raise ValueError("stationary_building_count_invalid")
        result = {"tick": tick, "game_time_s": clock, "landmarks": rows}
        parser.stop_after_tick(tick)

    parser._on_packet_end(packet)
    parser.parse()
    if parser.parse_error or result is None:
        raise ValueError("stationary_building_parse_failed")
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or sha256_file(source) != digest:
        raise ValueError("replay_source_changed")
    import gem.parser as parser_module
    return {"schema_version": 1, "kind": "offline_replay_stationary_landmarks",
            "match_id": match_id, "source_file": source.name, "source_bytes": before.st_size,
            "source_sha256": digest, "source_unchanged": True,
            "parser": {"name": "gem-dota", "version": GEM_VERSION, "upstream_commit": GEM_COMMIT,
                       "runtime_parser_sha256": sha256_file(Path(parser_module.__file__))},
            "tool_source_sha256": sha256_file(Path(__file__)),
            "coordinate_basis": COORDINATE_BASIS,
            "elapsed_seconds": time.monotonic() - started,
            "own_process_below_normal": priority_lowered, "parse_error": None,
            "runtime_integration": False, "pixel_mapping_verified": False, **result}
