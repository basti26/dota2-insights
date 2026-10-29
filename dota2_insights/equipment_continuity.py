"""Bounded log evidence-loss review; never infer ownership or smooth economy."""
from collections import Counter
from datetime import datetime, timezone
import re

from .item_review_priorities import _number


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


class EquipmentContinuity:
    """One instance per admitted match/session, consumed in original row order."""

    def __init__(self, catalogue=None, *, maximum_gap_s=180, example_limit=60):
        if not 0 < maximum_gap_s <= 1800 or not 1 <= example_limit <= 100:
            raise ValueError('equipment_continuity_budget')
        self.catalogue = catalogue or {}
        self.gap = maximum_gap_s
        self.limit = example_limit
        self.heroes = {}
        self.events = []
        self.counts = Counter()

    def add(self, row, evidence):
        attempt = row.get('attempt') or {}
        hero = attempt.get('hero') or {}
        hid = hero.get('hero_id')
        if not isinstance(hid, str) or not re.fullmatch(r'npc_dota_hero_[a-z0-9_]{1,60}', hid):
            self.counts['rows_without_hero'] += 1
            return
        stamp = _time(row.get('captured_at_utc'))
        old = self.heroes.get(hid)
        if stamp is None or (old and stamp <= old['time']):
            self.heroes.pop(hid, None)
            self.counts['invalid_or_nonincreasing_time_resets'] += 1
            return
        if old and (stamp - old['time']).total_seconds() > self.gap:
            old = None
            self.counts['observation_gap_resets'] += 1
        slots = hero.get('slots')
        if not isinstance(slots, list):
            self.heroes.pop(hid, None)
            return
        ids = Counter(s.get('slot_id') for s in slots if isinstance(s, dict) and isinstance(s.get('slot_id'), str))
        current = {}
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            sid = slot.get('slot_id')
            if not isinstance(sid, str) or not re.fullmatch(r'[a-z0-9_]{1,40}', sid) or ids[sid] != 1:
                continue
            prior = old['slots'].get(sid) if old else None
            status = slot.get('status')
            if status == 'known':
                item = slot.get('item_id')
                if not isinstance(item, str):
                    continue
                item = item.removeprefix('item_')
                cost_row = self.catalogue.get(item)
                cost = _number(cost_row.get('cost')) if isinstance(cost_row, dict) else None
                if prior and prior.get('loss') is not None:
                    event = prior['loss']
                    if (stamp - prior['known_time']).total_seconds() <= self.gap:
                        event['next_known'] = self._sample(row, slot, evidence)
                        event['next_known_same_item'] = item == prior['item']
                        self.counts['next_known_same_item' if item == prior['item'] else 'next_known_different_item'] += 1
                if cost is not None and cost >= 2000:
                    current[sid] = {'item': item, 'cost': cost, 'known_time': stamp,
                                    'known': self._sample(row, slot, evidence), 'loss': None}
            elif status == 'unknown' and prior and (stamp - prior['known_time']).total_seconds() <= self.gap:
                if prior['loss'] is None:
                    event = {'hero_id': hid, 'slot_id': sid, 'last_known_item': prior['item'],
                             'catalogue_cost': prior['cost'], 'known': prior['known'],
                             'first_unknown': self._sample(row, slot, evidence),
                             'last_unknown': self._sample(row, slot, evidence),
                             'unknown_rows': 0, 'next_known': None, 'next_known_same_item': None}
                    self.counts['known_to_unknown_episodes'] += 1
                    if len(self.events) < self.limit:
                        self.events.append(event)
                    else:
                        self.counts['episodes_not_retained_due_to_limit'] += 1
                    prior['loss'] = event
                prior['loss']['unknown_rows'] += 1
                prior['loss']['last_unknown'] = self._sample(row, slot, evidence)
                self.counts['unknown_rows_after_known'] += 1
                current[sid] = prior
            # Empty, missing, duplicate or other states break the chain.
        self.heroes[hid] = {'time': stamp, 'slots': current}

    @staticmethod
    def _sample(row, slot, evidence):
        selection = (row.get('attempt') or {}).get('economy_selection')
        retained = None
        if isinstance(selection, dict):
            retained = {k: selection.get(k) for k in ('policy', 'reason', 'selected_captured_at_utc',
                        'selected_value', 'current_observed_value', 'maximum_age_s')}
        return {'captured_at_utc': row.get('captured_at_utc'), 'evidence': evidence,
                'status': slot.get('status'), 'item_id': slot.get('item_id'),
                'candidate': slot.get('candidate'), 'value': slot.get('value'),
                'economy_selection': retained}

    def result(self):
        return {'maximum_gap_s': self.gap, 'example_limit': self.limit,
                'catalogue_available': bool(self.catalogue), 'counts': dict(self.counts),
                'episodes': self.events, 'images_verified': False, 'ui_value_verified': False,
                'limitation': 'recorded_status_transitions_not_item_truth_or_missed_gold; '
                              'catalogue_cost_is_review_priority_not_patch_price; '
                              'gaps_and_missing_slots_break_chains'}
