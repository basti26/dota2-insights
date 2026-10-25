"""Common source-aware event families; no outcome or tactical inference."""
import json
import re

TARGET_FAMILIES=frozenset({'tower_destroyed','barracks_destroyed','ancient_destroyed',
                           'other_building','roshan_kill','roshan_death_record'})


def event_family(event):
    kind,payload=event['event_kind'],json.loads(event['payload'])
    if kind in ('legacy_combat','captured_combat'):return payload.get('family')
    if kind.startswith('hero_kill:'):return 'hero_kill_log'
    if kind.startswith('buyback:'):return 'buyback'
    if kind!='objective':return None
    if payload.get('type')=='CHAT_MESSAGE_ROSHAN_KILL':return 'roshan_kill'
    if payload.get('type')!='building_kill':return None
    name=str(payload.get('key',''))
    if re.fullmatch(r'npc_dota_(goodguys|badguys)_tower[1-4](_(top|mid|bot))?',name):return 'tower_destroyed'
    if re.fullmatch(r'npc_dota_(goodguys|badguys)_(melee|range)_rax_(top|mid|bot)',name):return 'barracks_destroyed'
    if name in ('npc_dota_goodguys_fort','npc_dota_badguys_fort'):return 'ancient_destroyed'
    return 'other_building'
