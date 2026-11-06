import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.map_translation_pairs import pair, compare
from dota2_insights.replay_archive import GEM_VERSION, GEM_COMMIT, COORDINATE_BASIS
from tools.compare_map_translation import run


IDENTITY = {'map_name': 'dota', 'client_build': 10, 'map_asset_sha256': 'a'*64, 'patch': None}


def fixture():
    assets = [{'classname': 'npc_dota_tower', 'targetname': '[PR#]dota_goodguys_tower1_mid',
               'entity_ordinal': 1, 'origin_xyz': [-4000.015, 0, 128]}]
    report = {'schema_version': 1, 'kind': 'offline_replay_stationary_landmarks', 'match_id': '123',
              'source_sha256': 'b'*64, 'source_unchanged': True, 'parse_error': None,
              'coordinate_basis': COORDINATE_BASIS, 'tick': 100, 'game_time_s': .03,
              'parser': {'version': GEM_VERSION, 'upstream_commit': GEM_COMMIT},
              'landmarks': [{'name': 'dota_goodguys_tower1_mid', 'class_name': 'CDOTA_BaseNPC_Tower',
                             'team': 2, 'entity_index': 5, 'entity_serial': 12,
                             'world_x': 12384, 'world_y': 16384}]}
    return report, assets


class TranslationPairTests(unittest.TestCase):
    def test_exact_names_and_class_form_pairs_and_keep_nonzero_quantization_error(self):
        report, assets = fixture(); before = copy.deepcopy((report, assets))
        result = compare(report, assets, IDENTITY)
        self.assertEqual((report, assets), before)
        self.assertEqual(result['dataset']['rows'][0]['raw_xy'], [12384, 16384])
        self.assertEqual(result['dataset']['rows'][0]['world_xy'], [-4000.015, 0])
        self.assertAlmostEqual(result['comparison']['results']['minus16384']['errors_world_units']['max'], .015)
        self.assertGreater(result['comparison']['results']['zero']['errors_world_units']['median'], 23000)
        self.assertIsNone(result['dataset']['replay_map_identity'])
        self.assertFalse(result['dataset']['whole_map_version_alignment_verified'])
        self.assertFalse(result['comparison']['translation_verified'])

    def test_duplicate_names_entities_or_assets_refuse_arbitrary_pair_choice(self):
        for mode in ('asset', 'name', 'entity'):
            report, assets = fixture()
            if mode == 'asset': assets.append(copy.deepcopy(assets[0]))
            if mode == 'name':
                row = {**report['landmarks'][0], 'entity_index': 6}; report['landmarks'].append(row)
            if mode == 'entity':
                row = {**report['landmarks'][0], 'name': 'dota_goodguys_tower2_mid'}; report['landmarks'].append(row)
            with self.subTest(mode=mode), self.assertRaises(ValueError): pair(report, assets, IDENTITY)

    def test_missing_same_name_stays_unmatched_even_if_coordinate_is_identical(self):
        report, assets = fixture(); assets[0]['targetname'] = '[PR#]dota_goodguys_tower2_mid'
        data = pair(report, assets, IDENTITY)
        self.assertEqual(data['rows'], [])
        self.assertEqual(data['unmatched_replay_objects'][0]['reason'], 'no_exact_asset_name')

    def test_bad_name_class_team_coordinates_and_identity_are_rejected(self):
        for key, value in (('name', 'dota_badguys_tower1_mid'), ('class_name', 'CDOTA_Unit'),
                           ('team', True), ('world_y', float('nan')), ('entity_serial', None)):
            report, assets = fixture(); report['landmarks'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): pair(report, assets, IDENTITY)

    def test_wrong_frame_parser_changed_source_or_noninitial_window_rejected(self):
        for field, value in (('coordinate_basis', 'gsi'), ('source_unchanged', False), ('schema_version', True),
                             ('game_time_s', 10), ('parse_error', 'bad')):
            report, assets = fixture(); report[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): pair(report, assets, IDENTITY)
        report, assets = fixture(); report['parser']['upstream_commit'] = 'unknown'
        with self.assertRaises(ValueError): pair(report, assets, IDENTITY)

    def test_lock_is_checked_from_small_manifest_before_extraction_or_dem_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest = root/'inputs.json'
            manifest.write_text(json.dumps({'schema_version': 1, 'role': 'development', 'matches': [{'match_id': '123'}]}), encoding='utf-8')
            with patch('tools.compare_map_translation.checked_input', side_effect=lambda root, p: Path(p)), \
                    patch('tools.compare_map_translation.checked_output', side_effect=lambda root, p: Path(p)), \
                    patch('tools.compare_map_translation.refuse_locked_match', side_effect=ValueError('test_locked')), \
                    patch('tools.compare_map_translation._footer_match_id') as footer, \
                    patch('tools.compare_map_translation.load', wraps=lambda p, *args: json.loads(p.read_text(encoding='utf-8'))) as read:
                with self.assertRaisesRegex(ValueError, 'test_locked'):
                    run(manifest, root/'missing_asset', root/'missing_revision', root/'out')
                footer.assert_not_called()
                self.assertEqual(read.call_count, 1)

    def test_file_consumer_checks_original_footer_sha_and_final_source_unchanged(self):
        from dota2_insights.map_holdout import sha
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); lm, rev = root/'landmarks', root/'revision'
            lm.mkdir(); (rev/'source/maps').mkdir(parents=True)
            for p in (lm/'landmarks.json', lm/'COMPLETE', lm/'DATA.txt', lm/'default_ents.vents_c',
                      rev/'snapshot.json', rev/'COMPLETE', rev/'source/maps/dota.vpk', rev/'source/steam.inf'):
                p.write_bytes(b'synthetic source')
            replay = root/'123.dem'; replay.write_bytes(b'synthetic replay bytes, not parsed')
            report, assets = fixture(); report.update(source_file=replay.name, source_bytes=replay.stat().st_size, source_sha256=sha(replay))
            extraction = root/'extraction.json'; extraction.write_text(json.dumps(report), encoding='utf-8')
            manifest = root/'inputs.json'
            manifest.write_text(json.dumps({'schema_version': 1, 'role': 'development', 'matches': [{
                'match_id': '123', 'extraction': str(extraction), 'extraction_sha256': sha(extraction), 'replay': str(replay)}]}), encoding='utf-8')
            metadata = {'map_asset_sha256': 'a'*64, 'client_build': 10,
                        'source_export_sha256': sha(lm/'DATA.txt'), 'resource_sha256': sha(lm/'default_ents.vents_c'),
                        'source_resource': 'maps/dota/entities/default_ents.vents_c'}
            snapshot = {'identity': IDENTITY, 'resources': [{'resource': metadata['source_resource'], 'sha256': metadata['resource_sha256']}]}
            with patch('tools.compare_map_translation.checked_input', side_effect=lambda root, p: Path(p)), \
                    patch('tools.compare_map_translation.checked_output', side_effect=lambda root, p: Path(p)), \
                    patch('tools.compare_map_translation.refuse_locked_match'), \
                    patch('tools.compare_map_translation.read_complete', return_value=metadata), \
                    patch('tools.compare_map_translation.read_revision', return_value=snapshot), \
                    patch('tools.compare_map_translation.extract', return_value={'landmarks': assets}), \
                    patch('tools.compare_map_translation._footer_match_id', return_value='123') as footer:
                result = run(manifest, lm, rev, root/'good')
                self.assertEqual(result['pair_count'], 1)
                self.assertTrue((root/'good/COMPLETE').is_file())
                footer.return_value = '456'
                with self.assertRaisesRegex(ValueError, 'footer_disagree'):
                    run(manifest, lm, rev, root/'wrong_footer')
                footer.return_value = '123'
                replay.write_bytes(b'changed source bytes')
                with self.assertRaisesRegex(ValueError, 'replay_changed'):
                    run(manifest, lm, rev, root/'wrong_source')
                self.assertFalse((root/'wrong_source').exists())
