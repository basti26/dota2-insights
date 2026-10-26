import re
from dota2_insights.helper_handoff import utc as utc_time

def check_hero(value):
    if value is not None and (not isinstance(value,str) or re.fullmatch(r"npc_dota_hero_[a-z0-9_]+",value) is None):
        raise ValueError("invalid hero identity")
