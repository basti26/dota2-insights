"""Offline review sampling only; catalogue cost is not a patch price claim."""
import math
import re


def _number(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) else None
    except OverflowError:
        return None


class ItemReviewPriorities:
    def __init__(self, catalogue=None):
        self.catalogue = catalogue or {}
        self.entries = {}

    @property
    def at_capacity(self):
        return len(self.entries) >= 5000

    def add(self, match_id, row, evidence):
        hero = (row.get('attempt') or {}).get('hero') or {}
        hid = hero.get('hero_id')
        if not isinstance(hid, str) or not re.fullmatch(r'npc_dota_hero_[a-z0-9_]{1,60}', hid):
            return
        for slot in hero.get('slots', []):
            if not isinstance(slot, dict):
                continue
            sid = slot.get('slot_id')
            if not isinstance(sid, str) or not re.fullmatch(r'[a-z0-9_]{1,40}', sid):
                continue
            item = slot.get('item_id') if slot.get('status') == 'known' else slot.get('candidate')
            if not isinstance(item, str):
                continue
            item = item.removeprefix('item_')
            if not re.fullmatch(r'[a-z0-9_]{1,80}', item):
                continue
            catalogue_item = self.catalogue.get(item) or {}
            cost = _number(catalogue_item.get('cost')) if isinstance(catalogue_item, dict) else None
            margin = _number(slot.get('candidate_margin'))
            reason = ('unknown_high_cost_candidate' if slot.get('status') not in ('known', 'empty') else
                      'small_recorded_matcher_margin' if margin is not None and margin < .12 else None)
            if cost is None or cost < 2000 or reason is None:
                continue
            key = match_id, hid, sid, item, reason
            if key not in self.entries and not self.at_capacity:
                self.entries[key] = {'match_id': match_id, 'hero_id': hid, 'slot_id': sid,
                    'candidate_item': item, 'catalogue_cost': cost, 'reason': reason, 'observations': 0,
                    'first_evidence': evidence, 'last_evidence': evidence, 'identity_or_item_confirmed': False}
            if key in self.entries:
                self.entries[key]['observations'] += 1
                self.entries[key]['last_evidence'] = evidence

    def result(self):
        return sorted(self.entries.values(), key=lambda p: (-p['catalogue_cost'], -p['observations'],
            p['match_id'], p['hero_id'], str(p['slot_id']), p['candidate_item']))[:60]
