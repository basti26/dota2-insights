import base64
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dota2_insights.postgame_store import create_store, source_category
from dota2_insights.ward_patterns import describe


def entry(identifier='9876543210', sources=None):
    wards = [{'match_id': identifier, 'ward_type': 'observer', 'team': 2, 'x': 15000, 'y': 16000,
              'placed_game_time_s': 60, 'ended_game_time_s': 420, 'duration_s': 360,
              'end_kind': 'expired', 'after_match_end': False,
              'coordinate_basis': 'gem_cell128_plus_vec_raw_coordinates'}]
    raw = json.dumps(wards).encode()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = {'match_id': identifier, 'parsed_match_id': identifier, 'complete': True,
                'parse_error': None, 'source_unchanged_stat': True, 'stop_reason': 'replay_eof',
                'independent_footer_matches_parser': True, 'source_sha256': 'a'*64,
                'match_end_game_time_s': 1200, 'ward_count': 1,
                'parser_version': '0.13.0', 'parser_commit': 'b'*40, 'parser_sha256': 'c'*64,
                'ward_extractor_sha256': 'd'*64, 'adapter_sha256': 'e'*64,
                'files': {'wards.json': {'sha256': digest, 'bytes': len(raw)}}}
    catalog = {'match_id': identifier, 'sources': sources or ['tier1'], 'sha256': 'a'*64,
               'analysis_sha256': digest, 'download_status': 'verified', 'analysis_status': 'complete',
               'sampling': {'league_id': 20395, 'league_name': 'Example league', 'tier': 1,
                            'liquipedia_url': 'https://liquipedia.net/dota2/Example'}}
    return {'match_id': identifier, 'catalog': catalog,
            'manifest_base64': base64.b64encode(json.dumps(manifest).encode()).decode(),
            'wards_base64': base64.b64encode(raw).decode()}


def snapshot(path, entries):
    path.write_text(json.dumps({'kind': 'frozen_verified_ward_exports', 'schema_version': 1,
        'raw_replays_transferred': False, 'frozen_at_utc': '2026-10-29T00:00:00+00:00',
        'matches': entries}), encoding='utf-8')


class Tier1PostgameStoreTests(unittest.TestCase):
    def test_source_groups_stay_separate_and_metadata_preserved(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); path = root/'snapshot.json'
            snapshot(path, [entry('9876543210'), entry('9876543211', ['d2pt']),
                            entry('9876543212', ['own']), entry('9876543213', ['d2pt','tier1'])])
            result = create_store(path, root/'facts')
            self.assertEqual(result['source_group_counts'], {'tier1': 2, 'd2pt': 1, 'own': 1})
            with closing(sqlite3.connect(root/'facts/facts.sqlite')) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM match_competitions').fetchone()[0], 2)
                self.assertEqual(c.execute("SELECT league_id,tier FROM match_competitions WHERE match_id='9876543210'").fetchone(), (20395,1))
                self.assertEqual(c.execute("SELECT count(*) FROM sources WHERE kind='source_catalog.json'").fetchone()[0],2)
            data = describe(root/'facts/facts.sqlite', root/'patterns')
            self.assertEqual({c['source_group'] for c in data['cohorts'].values()}, {'tier1','own','d2pt'})

    def test_existing_own_priority_preserved_with_overlap(self):
        group, comp = source_category(entry(sources=['own','d2pt','tier1'])['catalog'])
        self.assertEqual(group, 'own')
        self.assertEqual(comp['tier'],1)

    def test_unknown_duplicate_sources_and_missing_classification_refused(self):
        for origins in ([], ['tier1','tier1'], ['public'], [True]):
            row = entry()['catalog']; row['sources'] = origins
            with self.assertRaises(ValueError):
                source_category(row)
        for change in ({'tier':2}, {'tier':True}, {'league_id':True}, {'league_name':''},
                       {'liquipedia_url':'https://example.com/dota2/Example'},
                       {'liquipedia_url':'https://liquipedia.net/dota2/Example?token=private'}):
            row=entry()['catalog']; row['sampling'].update(change)
            with self.assertRaises(ValueError):
                source_category(row)

    def test_catalog_digest_and_footer_still_required(self):
        for mutation in ('digest','footer'):
            with TemporaryDirectory() as temp:
                root=Path(temp); e=entry()
                if mutation=='digest': e['catalog']['analysis_sha256']='0'*64
                else:
                    m=json.loads(base64.b64decode(e['manifest_base64']));m['independent_footer_matches_parser']=False
                    e['manifest_base64']=base64.b64encode(json.dumps(m).encode()).decode()
                path=root/'snapshot.json';snapshot(path,[e])
                with self.assertRaises(ValueError):create_store(path,root/'facts')

    def test_holdout_checked_before_decoding_any_body(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);path=root/'snapshot.json';snapshot(path,[entry()])
            with patch('dota2_insights.postgame_store.refuse_locked_match',side_effect=ValueError('locked')), \
                 patch('dota2_insights.postgame_store.base64.b64decode') as decode:
                with self.assertRaises(ValueError):create_store(path,root/'facts')
                decode.assert_not_called()
