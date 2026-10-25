from copy import deepcopy
from types import SimpleNamespace
import unittest

from dota2_insights.replay_death_semantics import (
    capture_reincarnation_presence, classify_death_event,
    summarize_death_semantics,
)

try:
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
except ImportError:
    descriptor_pb2 = descriptor_pool = message_factory = None


class Message:
    def __init__(self, value=None):
        self.value = value
        self.will_reincarnate = False if value is None else value

    def HasField(self, key):
        assert key == 'will_reincarnate'
        return self.value is not None


class Processor:
    """Generated messages and synchronous emits; no replay or game dependency."""
    def __init__(self):
        self.handlers = []

    def process_s2_entry(self, msg, source='s2_direct'):
        entry = SimpleNamespace(source=source,
                                will_reincarnate=bool(msg.will_reincarnate)
                                if msg.HasField('will_reincarnate') else False)
        for handler in self.handlers:
            handler(entry)
        return entry

    def process_s2_bulk(self, messages):
        for message in messages:
            self.process_s2_entry(message, source='s2_bulk')

    def process_s1_event(self):
        entry = SimpleNamespace(source='s1_game_event', will_reincarnate=False)
        for handler in self.handlers:
            handler(entry)


def death(**fields):
    row = {'kind': 'combat_event', 'event_type': 'DEATH', 'tick': 100,
           'target': 'npc_dota_hero_skeleton_king',
           'target_is_hero': True, 'target_is_hero_present': True,
           'target_is_illusion': False, 'target_is_illusion_present': True}
    row.update(fields)
    return row


class PresenceTests(unittest.TestCase):
    @unittest.skipIf(message_factory is None, 'optional protobuf runtime unavailable')
    def test_generated_proto2_optional_bool_presence_survives_decoder_default(self):
        descriptor = descriptor_pb2.FileDescriptorProto(
            name='death_semantics_presence_test.proto', syntax='proto2')
        message = descriptor.message_type.add(name='CombatEntry')
        message.field.add(name='will_reincarnate', number=78,
                          label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
                          type=descriptor_pb2.FieldDescriptorProto.TYPE_BOOL)
        pool = descriptor_pool.DescriptorPool()
        pool.Add(descriptor)
        generated = message_factory.GetMessageClass(pool.FindMessageTypeByName('CombatEntry'))
        # Round trips exercise a real generated protobuf's absence semantics.
        messages = [generated.FromString(generated(will_reincarnate=value).SerializeToString())
                    for value in (True, False)]
        messages.append(generated.FromString(b''))
        self.assertFalse(messages[2].will_reincarnate)
        self.assertFalse(messages[2].HasField('will_reincarnate'))
        processor, seen = Processor(), []
        with capture_reincarnation_presence(processor) as read:
            processor.handlers.append(lambda entry: seen.append(read(entry)))
            processor.process_s2_bulk(messages)
        self.assertEqual([r['will_reincarnate'] for r in seen], [True, False, None])
        self.assertEqual([r['will_reincarnate_present'] for r in seen], [True, True, False])
        self.assertEqual([r['will_reincarnate_decoder_value'] for r in seen], [True, False, False])

    def test_synchronous_true_false_absent_bulk_and_restore(self):
        processor, seen = Processor(), []
        prior = processor.process_s2_entry
        with capture_reincarnation_presence(processor) as read:
            processor.handlers.append(lambda entry: seen.append(read(entry)))
            processor.process_s2_entry(Message(True))
            processor.process_s2_bulk([Message(False), Message()])
            self.assertIsNone(read()['will_reincarnate'])
            self.assertEqual([row['will_reincarnate'] for row in seen], [True, False, None])
            self.assertEqual([row['will_reincarnate_present'] for row in seen], [True, True, False])
            self.assertEqual([row['will_reincarnate_decoder_value'] for row in seen], [True, False, False])
            self.assertEqual(seen[2]['will_reincarnate_basis'], 'absent_s2_protobuf_field')
        self.assertEqual(processor.process_s2_entry, prior)
        self.assertNotIn('process_s2_entry', processor.__dict__)

    def test_reentrant_s2_and_nested_s1_do_not_borrow_context(self):
        processor, seen = Processor(), []
        recurse = [False]
        with capture_reincarnation_presence(processor) as read:
            def handler(entry):
                seen.append((entry.source, read(entry)))
                if entry.source == 's2_direct' and not recurse[0]:
                    recurse[0] = True
                    processor.process_s2_entry(Message(False))
                    processor.process_s1_event()
                    seen.append(('outer_restored', read(entry)))
            processor.handlers.append(handler)
            processor.process_s2_entry(Message(True))
            processor.process_s1_event()
        self.assertEqual([r['will_reincarnate'] for _, r in seen], [True, False, None, True, None])
        self.assertIsNone(seen[2][1]['will_reincarnate_present'])
        self.assertEqual(seen[2][1]['will_reincarnate_decoder_value'], False)

    def test_callback_exception_clears_stack_and_context_exit_restores_binding(self):
        processor = Processor()
        original = processor.process_s2_entry
        with self.assertRaisesRegex(RuntimeError, 'handler_failed'):
            with capture_reincarnation_presence(processor) as read:
                def failing(entry):
                    self.assertTrue(read(entry)['will_reincarnate'])
                    raise RuntimeError('handler_failed')
                processor.handlers.append(failing)
                try:
                    processor.process_s2_entry(Message(True))
                finally:
                    self.assertIsNone(read()['will_reincarnate'])
        self.assertEqual(processor.process_s2_entry, original)

    def test_nested_observers_restore_existing_instance_binding(self):
        processor = Processor()
        bound = processor.process_s2_entry
        processor.process_s2_entry = bound
        seen = []
        with capture_reincarnation_presence(processor) as outer:
            outer_binding = processor.process_s2_entry
            with capture_reincarnation_presence(processor) as inner:
                processor.handlers.append(lambda entry: seen.append((outer(entry), inner(entry))))
                processor.process_s2_entry(Message(True))
            self.assertIs(processor.process_s2_entry, outer_binding)
        self.assertIs(processor.process_s2_entry, bound)
        self.assertTrue(all(a['will_reincarnate'] is True and b['will_reincarnate'] is True
                            for a, b in seen))


class SemanticsTests(unittest.TestCase):
    def test_bounded_generated_stream_preserves_counts_and_reports_retention_omissions(self):
        produced = []
        def rows():
            for index in range(2001):
                produced.append(index)
                yield death(target='npc_dota_hero_' + ('sniper' if index % 2 else 'skeleton_king'),
                            source_ref=f'events.jsonl:{index + 1}')
        result = summarize_death_semantics(rows(), max_events_kept=100,
                                           max_refs_per_bucket=50, max_heroes=2)
        self.assertEqual(len(produced), 2001)
        self.assertEqual(result['counts']['raw_death_events'], 2001)
        self.assertEqual(result['counts']['unresolved_real_hero_death_events'], 2001)
        self.assertEqual(len(result['events']), 100)
        self.assertEqual(result['events_omitted'], 1901)
        self.assertEqual(len(result['refs']['raw_death_events']), 50)
        self.assertEqual(result['refs_omitted']['raw_death_events'], 1951)
        for hero, expected in [('npc_dota_hero_skeleton_king', 1001), ('npc_dota_hero_sniper', 1000)]:
            bucket = result['by_hero'][hero]
            self.assertEqual(bucket['counts']['raw_death_events'], expected)
            self.assertEqual(len(bucket['refs']['raw_death_events']), 50)
            self.assertEqual(bucket['refs_omitted']['raw_death_events'], expected - 50)
        self.assertTrue(result['retention']['counts_cover_all_death_events'])
        self.assertEqual(result['events'][99]['source_ref'], 'events.jsonl:100')

    def test_bounded_zero_retention_and_generated_hero_limit(self):
        result = summarize_death_semantics((death() for _ in range(3)),
                                           max_events_kept=0, max_refs_per_bucket=0, max_heroes=1)
        self.assertEqual(result['events'], [])
        self.assertEqual(result['events_omitted'], 3)
        self.assertEqual(result['counts']['raw_death_events'], 3)
        self.assertEqual(result['refs_omitted']['raw_death_events'], 3)
        self.assertEqual(result['normalization_status'], 'unresolved_no_final_death_count_certified')
        with self.assertRaisesRegex(ValueError, '^death_semantics_hero_limit$'):
            summarize_death_semantics((death(target=f'npc_dota_hero_generated_{i}') for i in range(3)),
                                      max_events_kept=0, max_refs_per_bucket=0, max_heroes=2)
        for name, value in [('max_events_kept', -1), ('max_refs_per_bucket', True), ('max_heroes', 1.5)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                summarize_death_semantics([], **{name: value})

    def test_old_archive_no_flag_remains_unresolved_and_preserves_duplicate_rows(self):
        rows = [death(source_ref='events.jsonl:10'), death(source_ref='events.jsonl:11')]
        original = deepcopy(rows)
        result = summarize_death_semantics(rows)
        self.assertEqual(rows, original)
        self.assertEqual(result['counts']['raw_death_events'], 2)
        self.assertEqual(result['counts']['unresolved_real_hero_death_events'], 2)
        self.assertEqual(result['refs']['raw_death_events'], ['events.jsonl:10', 'events.jsonl:11'])
        self.assertEqual(len(result['events']), 2)
        self.assertTrue(all(r['reincarnation_status'] == 'not_exported' for r in result['events']))
        self.assertFalse(any(r['final_death_certified'] for r in result['events']))
        result['events'][0]['source_event']['tick'] = -1
        self.assertEqual(rows, original)

    def test_positive_true_is_separate_from_false_or_absent(self):
        rows = [death(will_reincarnate=True, will_reincarnate_present=True,
                      will_reincarnate_decoder_value=True),
                death(will_reincarnate=False, will_reincarnate_present=True,
                      will_reincarnate_decoder_value=False),
                death(will_reincarnate=None, will_reincarnate_present=False,
                      will_reincarnate_decoder_value=False),
                death(will_reincarnate_decoder_value=True),
                death(will_reincarnate_decoder_value=False)]
        result = summarize_death_semantics(rows)
        self.assertEqual([r['reincarnation_status'] for r in result['events']],
                         ['explicit_true', 'explicit_false_unresolved', 'absent_unresolved',
                          'decoder_true_positive', 'false_or_absent_unresolved'])
        self.assertEqual(result['counts']['positive_reincarnation_triggers'], 2)
        self.assertEqual(result['counts']['explicit_reincarnation_triggers'], 1)
        self.assertEqual(result['counts']['decoder_positive_reincarnation_triggers'], 1)
        self.assertEqual(result['counts']['scoreboard_compatible_candidates'], 3)
        self.assertEqual(result['counts']['unresolved_real_hero_death_events'], 3)

    def test_timeline_source_flags_keep_provenance(self):
        raw = death(will_reincarnate=True, will_reincarnate_present=True,
                    will_reincarnate_decoder_value=True)
        timeline = {'kind': 'DEATH', 'tick': raw['tick'], 'tick_epoch': 2,
                    'sequence': 6, 'source_ref': 'events.jsonl:6',
                    'details': {key: raw[key] for key in ('target', 'target_is_hero', 'target_is_illusion')},
                    'source_flags': {key: value for key, value in raw.items()
                                     if key.startswith(('target_is_', 'will_reincarnate'))}}
        result = summarize_death_semantics([timeline])
        self.assertEqual(result['counts']['explicit_real_hero_reincarnation_triggers'], 1)
        self.assertEqual(result['events'][0]['tick_epoch'], 2)
        self.assertEqual(result['by_hero'][raw['target']]['refs']['raw_death_events'], ['events.jsonl:6'])
        timeline['source_flags']['target_is_illusion_present'] = None
        self.assertEqual(classify_death_event(timeline)['target_identity'], 'unresolved')

    def test_hero_name_default_false_strings_and_illusion_do_not_prove_real_hero(self):
        rows = [death(target_is_illusion_present=None),
                death(target_is_hero='true'), death(target_is_illusion=True),
                death(target='npc_dota_creep_badguys_melee', target_is_hero=False),
                {'kind': 'MODIFIER_ADD', 'details': {'inflictor': 'modifier_skeleton_king_reincarnation'}}]
        result = summarize_death_semantics(rows)
        self.assertEqual(result['counts']['raw_death_events'], 4)
        self.assertEqual(result['counts']['explicit_real_hero_death_events'], 0)
        self.assertEqual(result['counts']['target_identity_unresolved_death_events'], 2)
        self.assertEqual(result['counts']['explicit_nonhero_or_illusion_death_events'], 2)

    def test_conflicting_flags_are_unresolved_and_nearby_data_cannot_repair_them(self):
        row = death(will_reincarnate=True, will_reincarnate_present=False,
                    will_reincarnate_decoder_value=True,
                    nearby_modifier='modifier_skeleton_king_reincarnation_scepter',
                    alive_again_seconds=3, API_total_deaths=90)
        result = classify_death_event(row)
        self.assertEqual(result['reincarnation_status'], 'conflicting_unresolved')
        self.assertFalse(result['positive_reincarnation_trigger'])
        self.assertFalse(result['final_death_certified'])


if __name__ == '__main__':
    unittest.main()
