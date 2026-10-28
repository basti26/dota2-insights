"""Generated callback context, including public sealed role IDs, no real body."""
from datetime import datetime, timezone
import unittest

from dota2_insights.audio_source_context import validate_reference_context, validate_scope


class AudioSourceContextTests(unittest.TestCase):
    def context(self):
        stamp = 1793040000
        return {'match_id': '12345', 'provider_timestamp': stamp,
            'recorded_source_context': {'schema_version': 1, 'match_id': '12345',
                'game_session_id': 'game_'+'a'*32, 'record_session_id': 'run_'+'b'*32,
                'gsi_source_id': 'gsi_'+'c'*32,
                'provider_at_utc': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                'basis': 'accepted_same_GSI_callback_not_audio_sample_time'}}

    def test_optional_identity_and_null_match_are_not_backfilled(self):
        self.assertIsNone(validate_reference_context({'match_id': '12345'}))
        context = self.context()
        self.assertEqual(validate_reference_context(context), context['recorded_source_context'])
        context['match_id'] = context['recorded_source_context']['match_id'] = None
        self.assertIsNone(validate_reference_context(context)['match_id'])

    def test_provider_mismatch_invalid_id_and_extra_payload_rejected(self):
        for key, value in [('provider_at_utc', '2024-01-01T00:00:00Z'),
                           ('gsi_source_id', 'wrong'), ('record_session_id', None),
                           ('schema_version', True), ('private_path', '/not-a-source')]:
            context = self.context()
            context['recorded_source_context'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_reference_context(context)

    def test_nested_sealed_identity_is_refused_without_opening_body(self):
        context = self.context()
        context['recorded_source_context']['match_id'] = '9056629627'
        with self.assertRaises(ValueError):
            validate_reference_context(context)
        with self.assertRaises(ValueError):
            validate_scope({'match_id': '9056629627', 'game_session_id': 'game_'+'a'*32,
                            'record_session_id': 'run_'+'b'*32}, expected_match='12345')
