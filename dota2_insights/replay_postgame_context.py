"""Bounded optional context collected in the existing single replay pass."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

SCHEMA='replay_postgame_context_v1'
METRICS={'net_worth':'m_iNetWorth','total_earned_gold':'m_iTotalEarnedGold',
         'total_earned_xp':'m_iTotalEarnedXP','last_hits':'m_iLastHitCount','denies':'m_iDenyCount'}
TRACKED=('CDOTA_PlayerResource','CDOTADataRadiant','CDOTA_DataRadiant','CDOTADataDire','CDOTA_DataDire')


class ContextLimit(ValueError):
    pass


def numeric(value):
    return value if type(value) in (int,float) and math.isfinite(value) else None


def integer(value):
    return value if type(value) is int and value>=0 else None


def unit(value):
    return value if isinstance(value,str) and re.fullmatch(r'(npc_dota_|dota_)[a-z0-9_]{1,110}',value) else None


def boolean(value):
    return value if type(value) is bool else None


def family(kind,target):
    if kind=='BUYBACK':return 'buyback'
    if kind!='DEATH' or not target:return None
    if target.startswith('npc_dota_hero_'):return 'hero_death_candidate'
    if re.fullmatch(r'npc_dota_(goodguys|badguys)_tower[1-4](_(top|mid|bot))?',target):return 'tower_destroyed'
    if re.fullmatch(r'npc_dota_(goodguys|badguys)_(melee|range)_rax_(top|mid|bot)',target):return 'barracks_destroyed'
    if target=='npc_dota_roshan':return 'roshan_death_record'
    if target in ('npc_dota_goodguys_fort','npc_dota_badguys_fort'):return 'ancient_destroyed'
    return None


def normalize(record):
    """One whitelist shared by live callbacks and saved archive projections."""
    kind=record.get('event_type');target=unit(record.get('target'))
    group=family(kind,target)
    if group is None:return None
    hero=boolean(record.get('target_is_hero'));illusion=boolean(record.get('target_is_illusion'))
    hero_present=boolean(record.get('target_is_hero_present'));illusion_present=boolean(record.get('target_is_illusion_present'))
    reincarnate=boolean(record.get('will_reincarnate'))
    explicit_real=(hero_present is True and illusion_present is True and hero is True and illusion is False)
    if group=='hero_death_candidate':
        status=('reincarnation_flagged' if reincarnate is True else
                'illusion_flagged' if illusion is True else
                'explicit_real_hero_not_reincarnation_flagged' if explicit_real and reincarnate is False else
                'insufficient_hero_or_reincarnation_flags')
    else:status='source_record_not_tactical_verdict'
    return {'family':group,'event_type':kind,'packet_tick':integer(record.get('packet_tick',record.get('tick'))),
        'event_tick':integer(record.get('event_tick')),
        'packet_game_time_s':numeric(record.get('game_time_s')),
        'event_game_time_s':numeric(record.get('event_game_time_s')),
        'timestamp_raw_s':numeric(record.get('timestamp_raw')),
        'source':record.get('source') if record.get('source') in ('s1_game_event','s2_direct','s2_bulk','unknown') else 'unknown',
        'attacker':unit(record.get('attacker')),'damage_source':unit(record.get('damage_source')),
        'target':target,'attacker_team':record.get('attacker_team') if record.get('attacker_team') in (2,3) else None,
        'target_team':record.get('target_team') if record.get('target_team') in (2,3) else None,
        'value_raw':numeric(record.get('value')),'target_is_hero_raw':hero,'target_is_illusion_raw':illusion,
        'target_is_hero_present':hero_present,'target_is_illusion_present':illusion_present,
        'will_reincarnate_raw':reincarnate,'classification':status,
        'location_x_raw':numeric(record.get('location_x')),'location_y_raw':numeric(record.get('location_y')),
        'location_basis':'raw_combat_log_coordinates_not_merged_with_ward_or_map_coordinates',
        'time_basis':'event_and_packet_clock_retained_separately_no_silent_fallback'}


class Collector:
    """Optional callbacks; class filters and sparse 30-second metric sampling."""
    def __init__(self,match_id, *, max_events=5000,max_metric_packets=720,max_bytes=8*1024**2):
        self.match_id=match_id;self.max_events=max_events;self.max_metric_packets=max_metric_packets;self.max_bytes=max_bytes
        self.events=[];self.metrics=[];self.tracked={};self.field_coverage=Counter();self.bytes=0
        self.last_sample=None;self.parser=None;self.seen_kinds=Counter();self.filtered=Counter()

    def _append(self,target,row,limit):
        size=len(json.dumps(row,ensure_ascii=False,allow_nan=False).encode('utf-8'))
        if len(target)>=limit or self.bytes+size>self.max_bytes:
            raise ContextLimit('context_record_or_byte_limit')
        target.append(row);self.bytes+=size

    def attach(self,parser):
        self.parser=parser
        parser._on_entity_filtered(self._entity,class_names=TRACKED)
        parser.on_combat_log_entry(self._combat)
        parser._on_packet_end(self._sample)

    def _entity(self,entity,op):
        from gem.state.entities import EntityOp
        name=entity.get_class_name();key='radiant' if 'Radiant' in name else 'dire' if 'Dire' in name else name
        if op.has(EntityOp.DELETED):
            if self.tracked.get(key) is entity:self.tracked.pop(key,None)
        else:self.tracked[key]=entity

    def _combat(self,entry):
        if entry.log_type not in ('DEATH','BUYBACK'):return
        record={'event_type':str(entry.log_type),'packet_tick':self.parser.tick,'event_tick':entry.tick,
            'game_time_s':self.parser.game_clock.game_time_at(self.parser.tick),
            'event_game_time_s':entry.game_time_s,'timestamp_raw':entry.timestamp_s,'source':str(entry.source),
            'attacker':entry.attacker_name,'damage_source':entry.damage_source_name,'target':entry.target_name,
            'attacker_team':entry.attacker_team,'target_team':entry.target_team,'value':entry.value,
            'target_is_hero':entry.target_is_hero,'target_is_illusion':entry.target_is_illusion,
            'target_is_hero_present':entry.target_is_hero_present,'target_is_illusion_present':entry.target_is_illusion_present,
            # S1 defaults do not prove an absent reincarnation flag was false.
            'will_reincarnate':entry.will_reincarnate if str(entry.source) in ('s2_direct','s2_bulk') else None,
            'location_x':entry.location_x,'location_y':entry.location_y}
        row=normalize(record)
        if row is None:return
        if row['classification']=='illusion_flagged':
            self.filtered['explicit_illusion_death']+=1
            return
        self._append(self.events,row,self.max_events);self.seen_kinds[row['family']]+=1

    def _sample(self,*_args):
        tick=self.parser.tick;stamp=self.parser.game_clock.game_time_at(tick)
        if stamp is None or stamp<0:return
        if self.last_sample is not None and stamp-self.last_sample<30:return
        self.last_sample=stamp
        from gem.extractors._snapshots import scan_player_resource,_hero_npc_name
        resource=self.tracked.get('CDOTA_PlayerResource')
        scan=scan_player_resource(resource) if resource is not None else None
        valid=(scan is not None and scan.resolved and
            Counter(scan.team_by_id.values())=={2:5,3:5} and
            len({(scan.team_by_id[pid],slot) for pid,slot in scan.team_slot_by_id.items() if 0<=slot<5})==10)
        rows=[]
        for pid in range(10):
            side=scan.team_by_id.get(pid) if scan else None
            team_slot=scan.team_slot_by_id.get(pid) if scan else None
            team_data=self.tracked.get({2:'radiant',3:'dire'}.get(side))
            values={k:integer(team_data.get(f'm_vecDataTeam.{team_slot:04d}.{field}'))
                    if valid and team_data is not None and type(team_slot) is int and 0<=team_slot<5 else None
                    for k,field in METRICS.items()}
            resource_index=scan.index_by_id.get(pid) if scan else None
            raw_handle=resource.get(f'm_vecPlayerTeamData.{resource_index:04d}.m_hSelectedHero') if resource is not None and resource_index is not None else None
            handle=raw_handle & 0xFFFFFFFF if type(raw_handle) is int else None
            hero=self.parser.entity_manager.find_by_handle(handle) if self.parser.entity_manager is not None and handle is not None and handle!=0xFFFFFFFF else None
            name=unit(_hero_npc_name(hero,self.parser.string_tables.get_by_name('EntityNames'))) if hero is not None and hero.get_class_name().startswith('CDOTA_Unit_Hero_') else None
            for field,value in values.items():self.field_coverage[field+('::present' if value is not None else '::missing')]+=1
            rows.append({'logical_player_id':pid,'team':side,'team_slot':team_slot,'hero_id':name,
                'resource_index':resource_index,'metrics':values})
        self._append(self.metrics,{'packet_tick':tick,'game_time_s':stamp,
            'resource_mapping_resolved':valid,'players':rows},self.max_metric_packets)

    def finish(self,parser,*,complete):
        state={'schema_version':SCHEMA,'match_id':self.match_id,'complete':complete,
            'producer':'existing_parser_single_pass_combat_callbacks_and_sparse_team_counters',
            'source_scope':'offline_omniscient_replay_not_live_player_knowledge',
            'replay_metadata':{'game_build':parser.game_build if integer(parser.game_build) and parser.game_build>0 else None,
                'game_mode':parser.game_mode if integer(parser.game_mode) and parser.game_mode>0 else None,
                'patch_name':None,'map_revision':None},
            'events':self.events,'metric_packets':self.metrics,
            'coverage':{'event_family_counts':dict(self.seen_kinds),'metric_field_counts':dict(self.field_coverage),
                'filtered_event_counts':dict(self.filtered),
                'metric_packet_count':len(self.metrics),'event_count':len(self.events),
                'retained_record_bytes':self.bytes,'coverage_is_not_completeness_proof':True},
            'limits':{'max_events':self.max_events,'max_metric_packets':self.max_metric_packets,'max_bytes':self.max_bytes,
                'metric_sample_interval_s':30},
            'limitations':['No chat, account identifiers, player names, raw entities or unrestricted strings.',
                'Game build and mode are source values, not an inferred human patch or map revision.',
                'Missing flags/counters remain null; hero death candidates are not an audited KDA.',
                'Packet and combat-event clocks remain separate; no inferred fallback or UTC conversion.',
                'Buyback value remains raw; no player attribution is guessed from it.',
                'Locations are raw combat-log coordinates, not a validated ward/map transform.',
                'Metric mappings and coverage require actual-source verification before cross-match use.']}
        return state


def write_context(output,context):
    raw=json.dumps(context,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    if len(raw)>12*1024**2:raise ContextLimit('context_serialized_byte_limit')
    path=Path(output)/'postgame_context.json';path.write_bytes(raw)
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def validate_context(context,match_id,source_sha256,*,require_complete=True):
    """Constrain stored sidecars to the collector's public gameplay fields."""
    allowed={'schema_version','match_id','complete','producer','source_scope','replay_metadata',
             'events','metric_packets','coverage','limits','limitations','source_sha256','producer_sha256'}
    if (not isinstance(context,dict) or set(context)!=allowed or context['schema_version']!=SCHEMA
            or context['match_id']!=match_id or context['source_sha256']!=source_sha256
            or type(context['complete']) is not bool or require_complete and not context['complete']
            or not re.fullmatch('[a-f0-9]{64}',str(context['producer_sha256']))):
        raise ValueError('invalid_postgame_context_identity_or_schema')
    if (context['producer']!='existing_parser_single_pass_combat_callbacks_and_sparse_team_counters'
            or context['source_scope']!='offline_omniscient_replay_not_live_player_knowledge'):
        raise ValueError('invalid_postgame_context_producer')
    metadata=context['replay_metadata']
    if (set(metadata)!={'game_build','game_mode','patch_name','map_revision'}
            or metadata['patch_name'] is not None or metadata['map_revision'] is not None
            or any(metadata[k] is not None and integer(metadata[k]) is None for k in ('game_build','game_mode'))):
        raise ValueError('invalid_postgame_context_metadata')
    json.dumps(context,allow_nan=False)
    if not isinstance(context['events'],list) or len(context['events'])>5000 or not isinstance(context['metric_packets'],list) or len(context['metric_packets'])>720:
        raise ValueError('postgame_context_record_limit')
    fields=set(normalize({'event_type':'BUYBACK'}))
    categories={'hero_death_candidate','tower_destroyed','barracks_destroyed','roshan_death_record','ancient_destroyed','buyback'}
    for row in context['events']:
        if not isinstance(row,dict) or set(row)!=fields or row['family'] not in categories:
            raise ValueError('invalid_postgame_event_shape')
        if (row['event_type'] not in ('DEATH','BUYBACK') or row['source'] not in ('s1_game_event','s2_direct','s2_bulk','unknown')
                or row['time_basis']!='event_and_packet_clock_retained_separately_no_silent_fallback'
                or row['location_basis']!='raw_combat_log_coordinates_not_merged_with_ward_or_map_coordinates'
                or row['classification'] not in ('reincarnation_flagged','illusion_flagged','explicit_real_hero_not_reincarnation_flagged',
                    'insufficient_hero_or_reincarnation_flags','source_record_not_tactical_verdict')):
            raise ValueError('invalid_postgame_event_labels')
        if any(row[k] is not None and unit(row[k])!=row[k] for k in ('attacker','damage_source','target')):
            raise ValueError('invalid_postgame_unit_name')
        for k in ('packet_game_time_s','event_game_time_s','timestamp_raw_s','location_x_raw','location_y_raw','value_raw'):
            if row[k] is not None and numeric(row[k]) is None:raise ValueError('invalid_postgame_number')
        for k in ('target_is_hero_raw','target_is_illusion_raw','target_is_hero_present','target_is_illusion_present','will_reincarnate_raw'):
            if row[k] is not None and type(row[k]) is not bool:raise ValueError('invalid_postgame_flag')
    for packet in context['metric_packets']:
        if (set(packet)!={'packet_tick','game_time_s','resource_mapping_resolved','players'}
                or numeric(packet['game_time_s']) is None or type(packet['resource_mapping_resolved']) is not bool
                or not isinstance(packet['players'],list) or len(packet['players'])!=10):
            raise ValueError('invalid_postgame_metric_packet')
        if {r['logical_player_id'] for r in packet['players']}!=set(range(10)):
            raise ValueError('invalid_postgame_metric_roster')
        for player in packet['players']:
            if set(player)!={'logical_player_id','team','team_slot','hero_id','resource_index','metrics'} or set(player['metrics'])!=set(METRICS):
                raise ValueError('invalid_postgame_metric_fields')
            if player['hero_id'] is not None and (unit(player['hero_id'])!=player['hero_id'] or not player['hero_id'].startswith('npc_dota_hero_')):
                raise ValueError('invalid_postgame_metric_identity')
            if any(v is not None and integer(v) is None for v in player['metrics'].values()):
                raise ValueError('invalid_postgame_metric_value')
            if not packet['resource_mapping_resolved'] and any(v is not None for v in player['metrics'].values()):
                raise ValueError('unresolved_postgame_metrics_must_be_null')
    if (context['coverage'].get('event_count')!=len(context['events'])
            or context['coverage'].get('metric_packet_count')!=len(context['metric_packets'])):
        raise ValueError('postgame_context_coverage_count_mismatch')
    return context
