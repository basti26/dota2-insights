"""Reuse completed same-DEM archives with explicit legacy coverage limits."""
from __future__ import annotations

import json
from pathlib import Path

from .postgame_store import bounded,sha
from .replay_postgame_context import normalize


def load_archive(folder,ward_manifest):
    folder=Path(folder).resolve()
    manifest_path=folder/'manifest.json';event_path=folder/'events.jsonl'
    raw=bounded(manifest_path,1024**2);manifest=json.loads(raw)
    mid=str(manifest.get('match_id'));parser=manifest.get('parser') or {}
    if (manifest.get('kind')!='offline_replay_archive' or mid!=str(ward_manifest['match_id'])
            or str(manifest.get('parsed_match_id'))!=mid or manifest.get('source_sha256')!=ward_manifest['source_sha256']
            or manifest.get('source_unchanged_stat') is not True or manifest.get('stop_reason')!='replay_eof'
            or manifest.get('parse_error') or manifest.get('truncated_at_tick') is not None
            or parser.get('version')!=ward_manifest['parser_version']
            or parser.get('upstream_commit')!=ward_manifest['parser_commit']
            or manifest.get('game_clock')!=ward_manifest['game_clock']):
        raise ValueError('legacy_context_same_dem_complete_parser_clock_required')
    body=bounded(event_path,64*1024**2);source=manifest.get('files',{}).get('events.jsonl',{})
    if source.get('sha256')!=sha(body) or source.get('bytes')!=len(body):
        raise ValueError('legacy_context_events_digest_mismatch')
    rows=[];lines=0
    for lines,line in enumerate(body.splitlines(),1):
        if lines>250_000 or len(line)>16*1024:
            raise ValueError('legacy_context_line_budget')
        saved=json.loads(line)
        if str(saved.get('match_id'))!=mid:raise ValueError('legacy_context_row_identity')
        row=normalize(saved)
        if row is None:continue
        rows.append({'match_id':mid,'event_kind':'legacy_combat','event_index':lines,
            'time_s':row['packet_game_time_s'],'team':row['attacker_team'],'actor_hero_numeric':None,
            'payload':json.dumps(row,allow_nan=False),'context_sha256':sha(raw),
            'time_basis':'same_DEM_packet_clock_manifest_equal_combat_event_time_retained_separately'})
        if len(rows)>5000:raise ValueError('legacy_context_selected_event_budget')
    if sha(bounded(manifest_path,1024**2))!=sha(raw) or sha(bounded(event_path,64*1024**2))!=sha(body):
        raise ValueError('legacy_context_source_changed')
    return rows,{'match_id':mid,'manifest_sha256':sha(raw),'events_sha256':sha(body),
        'replay_sha256':manifest['source_sha256'],'selected_records_before_game_boundary_filter':len(rows),
        'raw_event_lines':lines,'game_clock_equal_to_ward_export':True,
        'coverage':'legacy_hero_involvement_filter_partial_objectives_no_buyback_export',
        'death_flags':'missing_reincarnation_not_inferred','metrics':'not_imported_from_legacy_archive'}
