"""Read-only checks of recorded own-GSI valuation snapshots."""


def valuation_comparison_summary(display):
    bindings = display.get('comparison_binding_counts', {})
    mismatches = display.get('mismatches_in_comparable_rows')
    mismatch_text = '未比较' if mismatches is None else str(mismatches)
    return (f"估值快照核对。同次接收 {bindings.get('accepted_receive_callback', 0)} 行，"
            f"旧记录时间戳相同 {bindings.get('legacy_provider_timestamp_only', 0)} 行，"
            f"金额不符 {mismatch_text}，熊灵来源不符 {display.get('bear_provenance_issues', 0)}。")
from collections import Counter
import re

from .helper_handoff import utc


_NEUTRAL_SLOTS = ('neutral0', 'neutral1')
_NEUTRAL_ITEM = re.compile(r'[a-z0-9_]{1,75}')


def neutral_observation_summary(audit):
    neutral = audit.get('neutral_equipment')
    if neutral is None:
        return '中立装备未记录。'
    return (f"中立装备有 {neutral['rows_observed']} 行槽位上报，"
            f"同一会话相邻记录中有 {sum(neutral['adjacent_changes_by_slot'].values())} 次槽位变化。")


def integer(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


def _time(value):
    try:
        return utc(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _bear_valid(row, economy, display):
    """Validate provenance/arithmetic only, never certify pixels or ownership."""
    part = display.get('bear_supplement')
    if (economy.get('hero_id') != 'npc_dota_hero_lone_druid'
            or economy.get('economy_status') == 'reported'
            or display.get('amount_source') != 'self_gsi_plus_bear' or not isinstance(part, dict)
            or part.get('source') != 'owner_confirmed_bear_hud' or part.get('match_id') != row.get('match_id')):
        return False
    game = display.get('self_stats_game_session_id')
    # The own-stats session and capture session have independent IDs. Do not
    # equate this game with the outer capture's game_session_id.
    if (not isinstance(game, str) or not re.fullmatch(r'game_[a-f0-9]{32}', game)
            or part.get('game_session_id') != game):
        return False
    for key, pattern in (('unit_session_id', r'[a-f0-9]{32}'), ('frame_sha256', r'[a-f0-9]{64}')):
        if not isinstance(part.get(key), str) or not re.fullmatch(pattern, part[key]):
            return False
    basis = part.get('owner_basis')
    if basis not in ('manual_same_frame', 'verified_owner_hud', 'manual_match_binding'):
        return False
    if basis == 'manual_match_binding' and not re.fullmatch(r'[a-f0-9]{32}', str(part.get('owner_binding_id', ''))):
        return False
    keys = ('main_value', 'backpack_value', 'upgrade_value', 'value', 'known_count', 'slot_count')
    if any(type(part.get(key)) is not int or not 0 <= part[key] <= 10**7 for key in keys):
        return False
    observed, computed = _time(part.get('observed_at_utc')), _time(part.get('computed_at_utc'))
    return (observed is not None and computed is not None and computed >= observed
            and 0 <= part['known_count'] <= part['slot_count'] <= 9
            and part['upgrade_value'] == 0 and part['value'] == part['main_value'] + part['backpack_value'])


class SelfEconomyAudit:
    """Bounded output; never changes GSI, overlays, labels, caches or prices."""
    def __init__(self):
        self.statuses, self.heroes, self.missing = Counter(), Counter(), Counter()
        self.display_skips = Counter()
        self.valuation_receives, self.valuation_bindings = Counter(), Counter()
        self.reported_amount_rows = self.display_snapshot_rows = 0
        self.rows = self.components_checked = self.component_mismatches = 0
        self.display_normal = self.display_bear = self.display_mismatches = self.bear_provenance_issues = 0
        self.large_decreases = 0
        self.examples = []
        self.previous = {}
        self.last_reported = None
        self.neutral_rows = 0
        self.neutral_observed = Counter()
        self.neutral_changes = Counter()
        self.neutral_change_examples = []
        self.neutral_scope = None
        self.neutral_previous = {}
        self.neutral_latest = None

    def _example(self, kind, row, evidence, **values):
        example = {'kind': kind, 'evidence': evidence,
            'provider_at_utc': row.get('provider_at_utc'), 'received_at_utc': row.get('received_at_utc'), **values}
        if len(self.examples) < 20:
            self.examples.append(example)
        elif kind != 'gsi_decrease_not_recognition_error':
            # Ordinary value decreases must not crowd out a later bug receipt.
            victim = next((i for i in reversed(range(len(self.examples)))
                           if self.examples[i]['kind'] == 'gsi_decrease_not_recognition_error'), None)
            if victim is not None:
                self.examples[victim] = example

    def add(self, row, evidence=None):
        self.rows += 1
        received_status = row.get('valuation_receive_status')
        self.valuation_receives[received_status if received_status in ('accepted', 'rejected') else 'unknown'] += 1
        self.display_snapshot_rows += int(isinstance(row.get('display_economy'), dict))
        economy = row.get('self_economy')
        if not isinstance(economy, dict):
            self.neutral_previous = {}
            self.statuses['unavailable'] += 1
            self.display_skips['economy_unavailable'] += 1
            return
        status = economy.get('economy_status')
        status = status if status in ('reported', 'estimated', 'partial') else 'unknown_status'
        self.statuses[status] += 1
        hero = economy.get('hero_id')
        if isinstance(hero, str) and re.fullmatch(r'npc_dota_hero_[a-z0-9_]{1,60}', hero):
            self.heroes[hero] += 1
        else:
            hero = None
        self._neutral(row, economy, hero, evidence)
        reasons = economy.get('missing_reasons')
        for reason in (reasons[:24] if isinstance(reasons, list) else []):
            if isinstance(reason, str) and re.fullmatch(r'[a-z0-9_]{1,128}', reason):
                self.missing[reason] += 1
        amount = integer(economy.get('amount'))
        if status == 'reported':
            self.reported_amount_rows += int(amount is not None)
            self.last_reported = {'hero_id': hero, 'amount': amount, 'gpm': integer(economy.get('gpm'))}
            self.last_reported.update(provider_at_utc=row.get('provider_at_utc'), received_at_utc=row.get('received_at_utc'))
        parts = [integer(economy.get(key)) for key in ('cash_value', 'item_value', 'upgrade_value')]
        if status in ('partial', 'estimated') and amount is not None and all(p is not None for p in parts):
            self.components_checked += 1
            if amount != sum(parts):
                self.component_mismatches += 1
                self._example('component_sum_mismatch', row, evidence, amount=amount, component_sum=sum(parts))
        received = _time(row.get('received_at_utc'))
        key = row.get('match_id'), row.get('game_session_id'), hero, status
        previous = self.previous.get(key)
        if hero is not None and received is not None and amount is not None:
            if (status == 'reported' and previous and 0 < (received - previous[0]).total_seconds() <= 5
                    and amount <= previous[1] - 500):
                self.large_decreases += 1
                self._example('gsi_decrease_not_recognition_error', row, evidence, previous_amount=previous[1], amount=amount)
            self.previous[key] = received, amount
        self._display(row, economy, amount, evidence)

    def _neutral(self, row, economy, hero, evidence):
        scope = (row.get('match_id'), row.get('game_session_id'), row.get('record_session_id'), hero)
        slots = economy.get('neutral_slots')
        if not all(scope) or not isinstance(slots, dict) or row.get('valuation_receive_status') == 'rejected':
            self.neutral_previous = {}
            return
        current = {slot: slots[slot] for slot in _NEUTRAL_SLOTS if slot in slots
                   and (slots[slot] is None or isinstance(slots[slot], str) and _NEUTRAL_ITEM.fullmatch(slots[slot]))}
        if current:
            self.neutral_rows += 1
            self.neutral_observed.update(current.keys())
            if scope == self.neutral_scope:
                for slot, item in current.items():
                    if slot in self.neutral_previous and self.neutral_previous[slot] != item:
                        self.neutral_changes[slot] += 1
                        if len(self.neutral_change_examples) < 20:
                            self.neutral_change_examples.append({'slot': slot, 'before': self.neutral_previous[slot],
                                'after': item, 'evidence': evidence, 'sequence': row.get('sequence')})
            self.neutral_latest = {'slots': current, 'evidence': evidence, 'sequence': row.get('sequence'),
                                   'received_at_utc': row.get('received_at_utc')}
        self.neutral_scope, self.neutral_previous = scope, current

    def _display(self, row, economy, amount, evidence):
        display = row.get('display_economy')
        if not isinstance(display, dict):
            reason = ('valuation_callback_rejected' if row.get('valuation_receive_status') == 'rejected'
                      else 'display_not_recorded')
            self.display_skips[reason] += 1
            return
        binding = display.get('valuation_binding')
        received_status = row.get('valuation_receive_status')
        if (binding is not None or received_status is not None) and (
                binding != 'accepted_receive_callback' or received_status != 'accepted'):
            self.display_skips['valuation_callback_binding_invalid'] += 1
            return
        provider, valuation = integer(row.get('provider_timestamp')), integer(display.get('valuation_provider_timestamp'))
        if provider is None or valuation is None:
            self.display_skips['valuation_clock_missing'] += 1
            return
        if provider != valuation:
            self.display_skips['valuation_not_same_packet'] += 1
            return
        raw, shown = integer(display.get('raw_amount')), integer(display.get('amount'))
        if amount is None or raw is None or shown is None:
            self.display_skips['amount_missing_or_invalid'] += 1
            return
        source = display.get('amount_source')
        supplement = display.get('bear_supplement')
        if source == 'self_gsi_plus_bear' or supplement is not None:
            if not _bear_valid(row, economy, display):
                self.bear_provenance_issues += 1
                self._example('bear_provenance_invalid_not_pixel_verdict', row, evidence)
                return
            self.display_bear += 1
            expected = amount + supplement['value']
        elif source is not None:
            self.display_skips['unknown_amount_source'] += 1
            return
        else:
            self.display_normal += 1
            expected = amount
        self.valuation_bindings[binding or 'legacy_provider_timestamp_only'] += 1
        if raw != amount or shown != expected:
            self.display_mismatches += 1
            self._example('same_packet_display_mismatch', row, evidence, gsi_amount=amount,
                          raw_display_amount=raw, displayed_amount=shown, expected_amount=expected)

    def result(self):
        result = {'schema_version': 1, 'rows': self.rows,
            'rows_by_economy_status': dict(self.statuses), 'hero_rows': dict(self.heroes),
            'missing_reasons': dict(self.missing), 'last_reported_gsi': self.last_reported,
            'valuation_evidence': {'reported_amount_rows': self.reported_amount_rows,
                                   'snapshot_rows': self.display_snapshot_rows,
                                   'callback_receives': dict(self.valuation_receives)},
            'component_checks': {'comparable_rows': self.components_checked, 'mismatches': self.component_mismatches,
                                 'reported_net_worth_not_compared_to_components': True},
            'display_checks': {'normal_comparable_rows': self.display_normal, 'bear_comparable_rows': self.display_bear,
                               'amount_mismatches': self.display_mismatches, 'bear_provenance_issues': self.bear_provenance_issues,
                               'comparison_binding_counts': dict(self.valuation_bindings),
                               'mismatches_in_comparable_rows': self.display_mismatches if self.valuation_bindings else None,
                               'uncomparable_reasons': dict(self.display_skips)},
            'large_decreases_within_five_seconds': self.large_decreases, 'examples': self.examples,
            'accuracy_verified': False, 'display_pixels_verified': False,
            'meaning': 'valuation_arithmetic_and_provenance_only; legacy timestamps do not uniquely identify packets; no screen verification'}
        if self.neutral_rows:
            result['neutral_equipment'] = {'rows_observed': self.neutral_rows,
                'observations_by_slot': dict(self.neutral_observed),
                'adjacent_changes_by_slot': dict(self.neutral_changes), 'last_observation': self.neutral_latest,
                'change_examples': self.neutral_change_examples,
                'scope': 'consecutive_recorded_own_gsi_slots', 'display_pixels_verified': False}
        return result
