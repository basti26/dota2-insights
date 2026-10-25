"""Bounded, offline replay evidence export; never imported by the live assistant.

The optional gem-dota dependency is loaded only by ``parse_archive``.  Output
contains canonical hero state and a small event whitelist, never player names,
accounts, chat, or an unrestricted entity dump.  This adapter pins gem's private
completed-packet callback: the parser version/commit are recorded in its manifest.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GEM_COMMIT = "bae361de1d82e7a0a8457c788bf6f9d90e9c4d86"
GEM_VERSION = "0.13.0"
NULL_HANDLE = 0xFFFFFF
COORDINATE_BASIS = "gem_cell128_plus_vec_raw_coordinates"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def packaged_source(path: Path) -> Path:
    """PyInstaller's .pyc origin has a separately fingerprinted public .py copy."""
    path = Path(path)
    source = path.with_suffix('.py')
    return source if path.suffix == '.pyc' and source.is_file() else path


def _footer_match_id(source: Path) -> str | None:
    """Read only the small indexed DEM_FileInfo packet for independent identity."""
    import struct
    import sys
    if getattr(sys, 'frozen', False):
        from .portable_replay import demo_file_info
        CDemoFileInfo = demo_file_info()
    else:
        from gem.proto.demo_pb2 import CDemoFileInfo
    def varint(stream: Any) -> int:
        value = 0
        for shift in range(0, 70, 7):
            byte = stream.read(1)
            if not byte:
                raise ValueError("truncated replay footer varint")
            value |= (byte[0] & 127) << shift
            if not byte[0] & 128:
                return value
        raise ValueError("oversized replay footer varint")
    with source.open("rb") as stream:
        header = stream.read(16)
        if header[:8] != b"PBDEMS2\0":
            raise ValueError("source does not have Source 2 replay magic")
        offset = struct.unpack_from("<I", header, 8)[0]
        if not 16 <= offset < source.stat().st_size:
            return None
        stream.seek(offset)
        command = varint(stream)
        varint(stream)  # footer tick, not a game-clock sample
        size = varint(stream)
        if command & ~0x40 != 2 or size > 1024 * 1024:
            raise ValueError("invalid or oversized replay footer")
        data = stream.read(size)
        if len(data) != size:
            raise ValueError("truncated replay footer")
        if command & 0x40:
            from io import BytesIO
            import snappy
            if varint(BytesIO(data)) > 1024 * 1024:
                raise ValueError("oversized expanded replay footer")
            data = snappy.decompress(data)
        if len(data) > 1024 * 1024:
            raise ValueError("oversized expanded replay footer")
        info = CDemoFileInfo()
        info.ParseFromString(data)
        value = info.game_info.dota.match_id
        return str(value) if value else None


def _finite(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class ArchiveOptions:
    sample_hz: float = 2.0
    start_game_time: float = 0.0
    end_game_time: float = 5400.0
    max_seconds: float = 600.0
    max_output_mib: int = 128
    cancel_file: str | None = None
    include_modifiers: bool = False
    visibility_transitions: bool = False

    def validate(self) -> None:
        if not 0.1 <= self.sample_hz <= 10:
            raise ValueError("sample_hz must be between 0.1 and 10")
        if not -600 <= self.start_game_time < self.end_game_time <= 10800:
            raise ValueError("game window must satisfy -600 <= start < end <= 10800")
        if not 1 <= self.max_seconds <= 3600 or not 1 <= self.max_output_mib <= 512:
            raise ValueError("wall seconds must be 1..3600 and output MiB 1..512")


class _BoundedStop(Exception):
    pass


def _windows_dota_running() -> bool:
    """Read-only Toolhelp process names, without shelling out or game handles."""
    import ctypes
    from ctypes import wintypes
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("cannot check running processes; replay export refused")
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        if not ok:
            raise OSError("cannot enumerate processes; replay export refused")
        while ok:
            if entry.szExeFile.lower() in ("dota2.exe", "dota.exe"):
                return True
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        kernel.CloseHandle(snapshot)


def parse_archive(source: Path, output: Path, options: ArchiveOptions) -> dict[str, Any]:
    """Reconstruct preceding state and export only the requested game window.

    A wall/output/cancel stop leaves an explicit partial manifest.  A bounded
    game-window stop is intentional, not evidence of a complete replay parse.
    Existing output directories are refused so two runs cannot mix evidence.
    """
    options.validate()
    import sys
    if sys.platform == "win32":
        if _windows_dota_running():
            raise ValueError("Dota is running; offline replay export refused")
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        priority_lowered = bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
    else:
        priority_lowered = False
    source = source.resolve(strict=True)
    if source.suffix.lower() != ".dem" or not source.is_file():
        raise ValueError("source must be an existing .dem file")
    if output.exists():
        raise ValueError("output directory already exists; choose a new directory")
    if importlib.metadata.version("gem-dota") != GEM_VERSION:
        raise ValueError(f"this adapter requires gem-dota {GEM_VERSION}")
    from gem.catalog.heroes import HEROES
    from gem.catalog.items import ITEMS
    from gem.extractors._snapshots import _pos, scan_player_resource
    from gem.parser import ReplayParser
    from gem.proto.dota_shared_enums_pb2 import (
        DOTA_GAMERULES_STATE_GAME_IN_PROGRESS, DOTA_GAMERULES_STATE_POST_GAME)
    from .replay_death_semantics import capture_reincarnation_presence
    from .replay_phase_evidence import IN_PROGRESS, POST_GAME, PhaseTracker
    if (DOTA_GAMERULES_STATE_GAME_IN_PROGRESS, DOTA_GAMERULES_STATE_POST_GAME) != (IN_PROGRESS, POST_GAME):
        raise ValueError("pinned_game_state_enums_differ_from_phase_contract")

    stat_before = source.stat()
    source_hash = sha256_file(source)
    with source.open("rb") as stream:
        if stream.read(8) != b"PBDEMS2\0":
            raise ValueError("source does not have Source 2 replay magic")
    match_id = _footer_match_id(source)
    if not match_id:
        raise ValueError("replay footer does not independently identify the match")
    output.mkdir(parents=True)
    started = time.monotonic()
    parser = ReplayParser(source)
    phase_tracker = PhaseTracker(in_progress=DOTA_GAMERULES_STATE_GAME_IN_PROGRESS,
                                 post_game=DOTA_GAMERULES_STATE_POST_GAME)
    tracked: dict[str, Any] = {}
    last_tick = -1000000
    counts = {"hero_samples": 0, "events": 0, "sample_packets": 0, "unresolved_hero_handles": 0, "incomplete_sample_packets": 0, "minimum_rows_per_packet": 10, "maximum_rows_per_packet": 0}
    sample_ticks = max(1, round(30 / options.sample_hz))
    total_bytes = 0
    stop_reason = "replay_eof"
    last_process_check = started
    first_game_time: float | None = None
    last_game_time: float | None = None
    upgrades: dict[tuple[str, str], dict[str, Any]] = {}
    modifier_cache: dict[int, tuple[bytes, dict[str, Any] | None]] = {}
    event_types = {"MODIFIER_ADD", "MODIFIER_REMOVE", "DEATH", "ABILITY", "ITEM"}
    samples_path = output / "hero_samples.jsonl"
    events_path = output / "events.jsonl"
    visibility_path = output / "visibility_transitions.jsonl"
    visibility_recorder = None
    if options.visibility_transitions:
        from .replay_visibility_recording import ReplayVisibilityRecorder
        visibility_recorder = ReplayVisibilityRecorder(match_id)

    def guard() -> None:
        nonlocal last_process_check
        if sys.platform == "win32" and time.monotonic() - last_process_check >= 5:
            last_process_check = time.monotonic()
            if _windows_dota_running():
                raise _BoundedStop("stopped_game_started")
        if time.monotonic() - started > options.max_seconds:
            raise _BoundedStop("wall_time_limit")
        if total_bytes >= options.max_output_mib * 1024 * 1024:
            raise _BoundedStop("output_size_limit")
        if options.cancel_file and Path(options.cancel_file).is_file():
            raise _BoundedStop("cancel_file")

    def write(stream: Any, row: dict[str, Any]) -> None:
        nonlocal total_bytes
        data = json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        encoded = data.encode("utf-8")
        if total_bytes + len(encoded) > options.max_output_mib * 1024 * 1024:
            raise _BoundedStop("output_size_limit")
        stream.write(data)
        total_bytes += len(encoded)

    def entity_name(entity: Any) -> str | None:
        table = parser.string_tables.get_by_name("EntityNames")
        if table is None:
            return None
        for field in ("m_pEntity.m_nameStringTableIndex", "m_pEntity.m_nameStringableIndex"):
            index = _integer(entity.get(field))
            item = table.items.get(index) if index is not None else None
            if item:
                name = item[0] if isinstance(item, tuple) else item
                if isinstance(name, str):
                    return name[:128]
        return None

    def on_entity(entity: Any, op: Any) -> None:
        name = entity.get_class_name()
        if name not in ("CDOTA_PlayerResource", "CDOTADataRadiant", "CDOTA_DataRadiant", "CDOTADataDire", "CDOTA_DataDire", "CDOTAGamerulesProxy"):
            return
        from gem.state.entities import EntityOp
        key = "radiant" if "Radiant" in name else "dire" if "Dire" in name else name
        if op.has(EntityOp.DELETED):
            if tracked.get(key) is entity:
                tracked.pop(key, None)
        else:
            tracked[key] = entity

    def inventory(hero: Any) -> list[dict[str, Any]]:
        rows = []
        for slot in range(17):
            raw = _integer(hero.get(f"m_hItems.{slot:04d}"))
            handle = None if raw is None else raw & 0xFFFFFFFF
            item = parser.entity_manager.find_by_handle(handle) if handle not in (None, NULL_HANDLE) else None
            name = entity_name(item) if item is not None else None
            key = name.removeprefix("item_") if name and name.startswith("item_") else None
            meta = ITEMS.get(key, {}) if key else {}
            rows.append({"slot": slot, "status": "empty" if handle == NULL_HANDLE else "occupied" if name else "unknown", "item_name": name, "item_key": key, "item_id": _integer(meta.get("id")), "charges": _integer(item.get("m_iCurrentCharges")) if item is not None else None, "raw_handle": handle, "field_present": raw is not None, "entity_resolved": item is not None, "name_resolved": name is not None})
        return rows

    def visibility(hero: Any) -> dict[str, Any]:
        index = hero.get_index()
        word_index, bit = index >> 6, index & 63
        result: dict[str, Any] = {"basis": "team_entity_m_bNPCVisibleState_raw_bit; player_minimap_semantics_unverified", "boundary": "completed_packet"}
        for team_name in ("radiant", "dire"):
            team_data = tracked.get(team_name)
            word = _integer(team_data.get(f"m_bNPCVisibleState.{word_index:04d}")) if team_data is not None else None
            word = word & ((1 << 64) - 1) if word is not None else None
            result[team_name] = {"state": "unknown" if word is None else "visible" if word & (1 << bit) else "hidden", "word_index": word_index, "bit": bit, "word_value": word}
        return result

    def packet_visibility_observations() -> list[dict[str, Any]]:
        resource = tracked.get("CDOTA_PlayerResource")
        scan = scan_player_resource(resource) if resource is not None else None
        observations = []
        for player_id in range(10):
            resource_index = scan.index_by_id.get(player_id) if scan else None
            raw_handle = _integer(resource.get(f"m_vecPlayerTeamData.{resource_index:04d}.m_hSelectedHero")) if resource_index is not None else None
            handle = None if raw_handle is None else raw_handle & 0xFFFFFFFF
            hero = parser.entity_manager.find_by_handle(handle) if parser.entity_manager is not None and handle not in (None, NULL_HANDLE) else None
            if hero is not None and not hero.get_class_name().startswith("CDOTA_Unit_Hero_"):
                hero = None
            name = entity_name(hero) if hero is not None else None
            if name is not None and not name.startswith("npc_dota_hero_"):
                name = None
            life = _integer(hero.get("m_lifeState")) if hero is not None else None
            unknown_visibility = {
                team: {"state": "unknown", "word_index": None, "bit": None, "word_value": None}
                for team in ("radiant", "dire")
            }
            observations.append({
                "player_slot": player_id, "hero_id": name,
                "team": scan.team_by_id.get(player_id) if scan else None,
                "entity_index": hero.get_index() if hero is not None else None,
                "entity_serial": hero.get_serial() if hero is not None else None,
                "entity_active": hero.active if hero is not None else None,
                "alive": None if life is None else life == 0,
                "life_state_raw": life,
                "visibility": visibility(hero) if hero is not None else unknown_visibility,
            })
        return observations

    def modifier_snapshot(hero: Any) -> dict[str, Any]:
        """Preserve current hero modifier names and optional raw flags, bounded."""
        from gem.proto.dota_modifiers_pb2 import CDOTAModifierBuffTableEntry
        table = parser.string_tables.get_by_name("ActiveModifiers")
        names = parser.string_tables.get_by_name("ModifierNames")
        rows = []
        if table is None or names is None:
            return {"status": "unavailable", "rows": []}
        for index, (_, raw) in table.items.items():
            cached = modifier_cache.get(index)
            if cached is not None and cached[0] == raw:
                data = cached[1]
            else:
                data = None
                if raw and len(raw) <= 65536:
                    message = CDOTAModifierBuffTableEntry()
                    message.ParseFromString(raw)
                    if message.HasField("parent") and message.HasField("modifier_class"):
                        named = names.items.get(message.modifier_class)
                        data = {"table_index": index, "parent_handle": message.parent, "modifier_class": message.modifier_class, "name": named[0] if named else None, "entry_type_raw": message.entry_type if message.HasField("entry_type") else None, "active_raw": message.active if message.HasField("active") else None, "has_scepter_raw": message.has_scepter if message.HasField("has_scepter") else None, "has_shard_raw": message.has_shard if message.HasField("has_shard") else None, "serial_num": message.serial_num if message.HasField("serial_num") else None}
                modifier_cache[index] = (raw, data)
            if data is None:
                continue
            parent = parser.entity_manager.find_by_handle(data["parent_handle"])
            if parent is hero:
                rows.append(data)
        return {"status": "table_snapshot", "basis": "ActiveModifiers protobuf parent resolved by full entity handle; has_scepter/shard raw does not imply consumption", "table_entries": len(table.items), "rows": rows[:64], "truncated": len(rows) > 64}

    def on_packet(tick: int) -> None:
        nonlocal last_tick, first_game_time, last_game_time
        guard()
        game_time = parser.game_clock.game_time_at(tick)
        # Observe every completed packet, including missing rules/clocks and
        # pregame. Hero sampling and the requested export window must not move
        # the native last-active / first-post-game boundary.
        rules = tracked.get("CDOTAGamerulesProxy")
        phase_tracker.observe(tick, game_time,
            _integer(rules.get("m_pGameRules.m_nGameState")) if rules is not None else None)
        if game_time is not None and game_time > options.end_game_time:
            parser.stop_after_tick(tick)
            return
        if game_time is None or game_time < options.start_game_time:
            return
        if visibility_recorder is not None:
            changes = visibility_recorder.observe_packet(
                tick=tick, net_tick=parser.net_tick, game_time_s=game_time,
                observations=packet_visibility_observations(),
            )
            for change in changes:
                write(visibility_stream, change)
                visibility_recorder.record_written(change)
        if tick - last_tick < sample_ticks:
            return
        last_tick = tick
        resource = tracked.get("CDOTA_PlayerResource")
        if resource is None or parser.entity_manager is None:
            return
        scan = scan_player_resource(resource)
        counts["sample_packets"] += 1
        packet_row_count = 0
        first_game_time = game_time if first_game_time is None else first_game_time
        last_game_time = game_time
        for player_id, resource_index in scan.index_by_id.items():
            raw_handle = _integer(resource.get(f"m_vecPlayerTeamData.{resource_index:04d}.m_hSelectedHero"))
            handle = None if raw_handle is None else raw_handle & 0xFFFFFFFF
            hero = parser.entity_manager.find_by_handle(handle) if handle not in (None, NULL_HANDLE) else None
            if hero is None or not hero.get_class_name().startswith("CDOTA_Unit_Hero_"):
                counts["unresolved_hero_handles"] += 1
                continue
            name = entity_name(hero)
            if not name or not name.startswith("npc_dota_hero_"):
                counts["unresolved_hero_handles"] += 1
                continue
            pos = _pos(hero)
            life = _integer(hero.get("m_lifeState"))
            rules = tracked.get("CDOTAGamerulesProxy")
            paused_raw = rules.get("m_pGameRules.m_bGamePaused") if rules is not None else None
            row: dict[str, Any] = {"kind": "hero_sample", "schema_version": SCHEMA_VERSION, "match_id": match_id, "tick": tick, "net_tick": parser.net_tick, "game_time_s": game_time, "game_time_basis": "gem_pause_aware_engine_game_clock", "packet_boundary": "after_entity_updates", "hero_id": name, "hero_name": name, "hero_numeric_id": _integer(HEROES.get(name, {}).get("id")), "player_slot": player_id, "team": scan.team_by_id.get(player_id), "entity_index": hero.get_index(), "entity_serial": hero.get_serial(), "world_x": _finite(pos[0]) if pos else None, "world_y": _finite(pos[1]) if pos else None, "coordinate_basis": COORDINATE_BASIS, "alive": None if life is None else life == 0, "life_state_raw": life, "health": _integer(hero.get("m_iHealth")), "level": _integer(resource.get(f"m_vecPlayerTeamData.{resource_index:04d}.m_iLevel")), "paused": paused_raw if isinstance(paused_raw, bool) else None, "inventory": inventory(hero), "visibility": visibility(hero), "consumed_scepter": None, "consumed_shard": None, "upgrade_evidence": {}}
            row["position_sample_tick"] = tick
            # Whitelisted offline lane counters. Missing fields remain null;
            # never use upstream snapshot defaults of zero as measured values.
            team_slot = scan.team_slot_by_id.get(player_id)
            team_data = tracked.get({2: "radiant", 3: "dire"}.get(scan.team_by_id.get(player_id)))
            lane_fields = {"net_worth": "m_iNetWorth", "total_earned_gold": "m_iTotalEarnedGold",
                           "total_earned_xp": "m_iTotalEarnedXP", "last_hits": "m_iLastHitCount",
                           "denies": "m_iDenyCount"}
            row["lane_metrics"] = {
                key: _integer(team_data.get(f"m_vecDataTeam.{team_slot:04d}.{field}"))
                if team_data is not None and type(team_slot) is int and 0 <= team_slot < 5 else None
                for key, field in lane_fields.items()}
            row["lane_metrics_basis"] = "same_packet_team_data_team_slot_nullable_v1_not_live_GSI"
            row["lane_metrics"] = {key: value if value is not None and value >= 0 else None
                                   for key, value in row["lane_metrics"].items()}
            row["position_sample_age_ticks"] = 0
            row["position_sample_basis"] = "current_reconstructed_entity_state_at_completed_packet; no_future_sample_or_interpolation"
            for upgrade in ("scepter", "shard"):
                proof = upgrades.get((name, upgrade))
                row[f"consumed_{upgrade}"] = proof["active"] if proof else None
                row["upgrade_evidence"][upgrade] = proof
            if options.include_modifiers:
                row["modifier_snapshot"] = modifier_snapshot(hero)
                for modifier in row["modifier_snapshot"]["rows"]:
                    upgrade = {"modifier_item_ultimate_scepter_consumed": "scepter", "modifier_item_aghanims_shard": "shard"}.get(modifier["name"])
                    from gem.proto.dota_modifiers_pb2 import DOTA_MODIFIER_ENTRY_TYPE_ACTIVE
                    if upgrade and modifier["entry_type_raw"] == DOTA_MODIFIER_ENTRY_TYPE_ACTIVE and modifier["active_raw"] is not False:
                        row[f"consumed_{upgrade}"] = True
                        row["upgrade_evidence"][upgrade] = {"basis": "exact_active_modifier_snapshot_full_parent_handle", "tick": tick, "game_time_s": game_time, **modifier}
            write(sample_stream, row)
            counts["hero_samples"] += 1
            packet_row_count += 1
        counts["minimum_rows_per_packet"] = min(counts["minimum_rows_per_packet"], packet_row_count)
        counts["maximum_rows_per_packet"] = max(counts["maximum_rows_per_packet"], packet_row_count)
        if packet_row_count != 10:
            counts["incomplete_sample_packets"] += 1

    def on_combat(entry: Any) -> None:
        guard()
        kind = entry.log_type
        if kind not in event_types:
            return
        target = str(entry.target_name or "")
        attacker = str(entry.attacker_name or "")
        inflictor = str(entry.inflictor_name or "")
        game_time = parser.game_clock.game_time_at(parser.tick)
        explicit_real_hero = entry.target_is_hero_present and entry.target_is_hero and entry.target_is_illusion_present and not entry.target_is_illusion
        if kind == "MODIFIER_ADD" and target.startswith("npc_dota_hero_") and explicit_real_hero:
            # Only exact consumed-upgrade modifier names imply ownership.
            upgrade = {"modifier_item_ultimate_scepter_consumed": "scepter", "modifier_item_aghanims_shard": "shard", "modifier_item_aghanims_shard_consumed": "shard"}.get(inflictor)
            if upgrade:
                upgrades[(target, upgrade)] = {"active": True, "tick": entry.tick, "packet_tick": parser.tick, "game_time_s": game_time, "event_game_time_s": entry.game_time_s, "timestamp_raw": _finite(entry.timestamp_s), "basis": "exact_combat_log_modifier_explicit_real_hero; no_entity_serial_available", "modifier": inflictor}
        if game_time is None or not options.start_game_time <= game_time <= options.end_game_time:
            return
        if not (target.startswith("npc_dota_hero_") or attacker.startswith("npc_dota_hero_")):
            return
        row = {"kind": "combat_event", "schema_version": SCHEMA_VERSION, "match_id": match_id, "tick": parser.tick, "game_time_s": game_time, "game_time_basis": "packet_game_clock; combat_event_timestamp_raw_retained", "event_type": kind, "attacker": attacker[:128], "target": target[:128], "inflictor": inflictor[:128], "target_is_hero": bool(entry.target_is_hero), "target_is_illusion": bool(entry.target_is_illusion), "value": _integer(entry.value), "timestamp_raw": _finite(entry.timestamp_s), "source": str(entry.source)}
        row.update({"event_tick": entry.tick, "packet_tick": parser.tick, "event_game_time_s": entry.game_time_s, "target_is_hero_present": entry.target_is_hero_present, "target_is_illusion_present": entry.target_is_illusion_present, "visible_radiant_raw": entry.visible_radiant, "visible_dire_raw": entry.visible_dire})
        row.update(read_combat_context(entry))
        write(event_stream, row)
        counts["events"] += 1

    parser.on_entity(on_entity)
    parser._on_packet_end(on_packet)
    parser.on_combat_log_entry(on_combat)
    with ExitStack() as streams:
        # The pinned decoder maps an absent optional bool to False. Keep the
        # original protobuf presence while the synchronous callback is active;
        # only this processor instance is wrapped, and ExitStack restores it.
        read_combat_context = streams.enter_context(
            capture_reincarnation_presence(parser.combat_log))
        sample_stream = streams.enter_context(samples_path.open("x", encoding="utf-8", newline="\n"))
        event_stream = streams.enter_context(events_path.open("x", encoding="utf-8", newline="\n"))
        visibility_stream = streams.enter_context(visibility_path.open("x", encoding="utf-8", newline="\n")) if visibility_recorder is not None else None
        try:
            parser.parse()
            if last_game_time is not None and parser.game_clock.game_time_at(parser.tick) > options.end_game_time:
                stop_reason = "requested_game_window_end"
        except _BoundedStop as exc:
            stop_reason = str(exc)
        except KeyboardInterrupt:
            stop_reason = "keyboard_interrupt"
        except Exception as exc:
            stop_reason = "adapter_or_parser_error"
            error = {"type": type(exc).__name__, "message": str(exc)[:300]}
        else:
            error = None
    error = locals().get("error")
    stat_after = source.stat()
    rules = tracked.get("CDOTAGamerulesProxy")
    match_id_64 = _integer(rules.get("m_pGameRules.m_unMatchID64")) if rules is not None else None
    observed_match_id = str(match_id_64) if match_id_64 else str(parser.match_id) if parser.match_id else None
    # Gem's early-stop fallback reads m_unMatchID64 with a uint32 accessor.
    # Preserve that discrepancy, independently validate identity with the footer.
    early_id_is_low32 = observed_match_id is not None and int(observed_match_id) == (int(match_id) & 0xFFFFFFFF)
    if early_id_is_low32:
        observed_match_id = match_id
    raw_parser_match_id = str(parser.match_id) if parser.match_id else None
    if observed_match_id and match_id and observed_match_id != match_id:
        stop_reason = "match_id_mismatch"
    manifest = {"schema_version": SCHEMA_VERSION, "kind": "offline_replay_archive", "created_at_utc": datetime.now(timezone.utc).isoformat(), "source_file": source.name, "source_bytes": stat_before.st_size, "source_sha256": source_hash, "source_unchanged_stat": (stat_before.st_size, stat_before.st_mtime_ns) == (stat_after.st_size, stat_after.st_mtime_ns), "match_id": match_id, "parsed_match_id": observed_match_id, "parser": {"name": "gem-dota", "version": GEM_VERSION, "upstream_commit": GEM_COMMIT, "adapter_private_api": "ReplayParser._on_packet_end and gem.extractors._snapshots helpers"}, "options": asdict(options), "elapsed_seconds": time.monotonic() - started, "stop_reason": stop_reason, "parse_error": {"type": type(parser.parse_error).__name__, "message": str(parser.parse_error)[:300]} if parser.parse_error else error, "truncated_at_tick": parser.truncated_at_tick, "last_tick": parser.tick, "first_exported_game_time_s": first_game_time, "last_exported_game_time_s": last_game_time, "counts": counts, "output_bytes": total_bytes, "files": {p.name: {"bytes": p.stat().st_size, "sha256": sha256_file(p)} for p in (samples_path, events_path)}, "game_clock": asdict(parser.game_clock), "limitations": ["Omniscient replay positions are offline truth, never live assistant input", "Visibility is a raw replay team bit, not yet validated against player minimap", "Unknown upgrade ownership is null; no final-state backfill", "World coordinates retain gem cell*128+vec values; GSI transform must be checked", "Inventory slots: 0..5 main, 6..8 backpack, 9..16 reserve/stash; slot semantics beyond 8 require replay-version check", "Complete packets sampled at a tick interval; sampled rate is requested maximum, not interpolation", "No account IDs, player names, chat, raw entities, or full parser model exported"]}
    if parser.parse_error:
        manifest["stop_reason"] = "replay_data_error"
    manifest["source_identity_basis"] = "independent_indexed_DEM_FileInfo_uint64"
    post_game_tick = _integer(parser.post_game_tick)
    manifest["match_phase_boundary"] = {
        "post_game_tick": post_game_tick,
        "post_game_game_clock_s": parser.game_clock.game_time_at(post_game_tick)
            if post_game_tick is not None else None,
        "combat_log_match_duration_s": _integer(parser.duration_s),
        "basis": "Gem observed POST_GAME combat-log marker; unknown until observed",
        "export_may_include_postgame_tail": True,
    }
    manifest["parser_raw_match_id"] = raw_parser_match_id
    manifest["early_stop_uint32_match_id_quirk"] = early_id_is_low32 and raw_parser_match_id != match_id
    manifest["process_resource_guard"] = {"own_process_below_normal": priority_lowered, "dota_process_name_check_seconds": 5, "dota_checked_before_start": sys.platform == "win32"}
    manifest["adapter_sha256"] = sha256_file(packaged_source(Path(__file__)))
    import gem.parser as gem_parser_module
    runtime_root = Path(gem_parser_module.__file__).resolve().parent
    checkout_root = Path(__file__).resolve().parents[1] / "diagnostics" / "replay-tools" / "gem-dota" / "src" / "gem"
    source_fingerprints = {}
    for relative in ("parser.py", "state/entities.py", "state/game_clock.py", "state/string_table.py", "extractors/_snapshots.py", "combat/log.py", "proto/dota_modifiers_pb2.py", "proto/dota_shared_enums_pb2.py"):
        runtime_file = runtime_root / relative
        checkout_file = checkout_root / relative
        runtime_sha = sha256_file(runtime_file)
        checkout_sha = sha256_file(checkout_file) if checkout_file.is_file() else None
        source_fingerprints[relative] = {"runtime_sha256": runtime_sha, "downloaded_checkout_sha256": checkout_sha, "matches_checkout": runtime_sha == checkout_sha}
    manifest["parser"]["runtime_source_fingerprints"] = source_fingerprints
    manifest["string_table_presence"] = {name: len(parser.string_tables.get_by_name(name).items) for name in ("ActiveModifiers", "ModifierNames", "EntityNames") if parser.string_tables.get_by_name(name) is not None}
    manifest["limitations"] += ["Position sample age is archive-snapshot age, not time since last coordinate delta", "m_bHasScepter-style effects do not imply consumption; unknown ownership remains null", "Gem early-stop match id may lose high bits; independent footer is authoritative", "Raw item slots 15/16 are preserved separately; do not count all slots >=9 as stash economics"]
    manifest["export_complete"] = stop_reason in ("replay_eof", "requested_game_window_end") and not manifest["parse_error"] and manifest["source_unchanged_stat"] and counts["hero_samples"] > 0 and counts["incomplete_sample_packets"] == 0
    if not manifest["source_unchanged_stat"]:
        manifest["stop_reason"] = "source_changed"
        manifest["export_complete"] = False
    elif not counts["hero_samples"]:
        manifest["stop_reason"] = "no_hero_samples_in_window"
        manifest["export_complete"] = False
    manifest["phase_evidence"] = phase_tracker.manifest(
        observation_complete=manifest["export_complete"], stop_reason=manifest["stop_reason"])
    manifest["evidence_collectors"] = {
        name: sha256_file(packaged_source(Path(__file__).with_name(name)))
        for name in ("replay_phase_evidence.py", "replay_death_semantics.py")}
    manifest["limitations"].append(
        "Raw will_reincarnate presence is retained; a missing/false flag does not certify a scoreboard death")
    if visibility_recorder is not None:
        manifest["files"][visibility_path.name] = {
            "bytes": visibility_path.stat().st_size, "sha256": sha256_file(visibility_path),
        }
        manifest["visibility_capture"] = visibility_recorder.manifest(
            export_succeeded=manifest["export_complete"],
        )
        manifest["visibility_capture"]["collector_sha256"] = sha256_file(
            packaged_source(Path(__file__).with_name("replay_visibility_recording.py")),
        )
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest
