"""Synthetic identities/archives only; real sealed DEMs or answers never opened."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.minimap import replay_alignment as alignment
from dota2_insights.minimap import replay_baselines as baselines
from dota2_insights.minimap.offline_io import check_match_metadata

LOCKED = '9056629627'  # Public role declaration, not a test data source.


class MinimapReplayAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def header(self, **changes):
        value = {'kind':'offline_replay_archive','match_id':'12345','parsed_match_id':'12345',
            'source_file':'generated.dem','source_sha256':'a'*64,'source_unchanged_stat':True,
            'parse_error':None,'truncated_at_tick':None,'stop_reason':'replay_eof','export_complete':True}
        value.update(changes)
        raw=json.dumps(value).encode()
        (self.root/'manifest.json').write_bytes(raw)
        return value,raw

    def test_named_sealed_path_refused_before_open_or_hash(self):
        path=self.root/('match_'+LOCKED)/'hero_samples.jsonl'
        real_open=Path.open
        def guarded(candidate,*args,**kwargs):
            self.assertNotEqual(Path(candidate),path,'sealed fixture body was opened')
            return real_open(candidate,*args,**kwargs)
        with patch.object(Path,'open',guarded):
            for reader in (alignment._digest,alignment._json,baselines._read_object):
                with self.subTest(reader=reader.__name__),self.assertRaises(ValueError):reader(path)

    def test_unnamed_archive_declares_lock_before_sample_hash_or_open(self):
        self.header(match_id=LOCKED,parsed_match_id=LOCKED)
        with patch.object(alignment,'_digest',side_effect=AssertionError('body hash before identity')):
            with self.assertRaises(ValueError):alignment.read_replay_samples(self.root/'hero_samples.jsonl')

    def test_metadata_checks_nested_identity_and_declared_source_path(self):
        for value in ({'sources':{'original_replay':{'match_id':LOCKED}}},
                      {'source_file':'unopened/'+LOCKED+'.dem'},
                      {'training':{'matches':[LOCKED]}}, {'parsed_match_id':LOCKED}):
            with self.subTest(value=value),self.assertRaises(ValueError):check_match_metadata(value)
        check_match_metadata({'match_id':None,'metadata':{'count':12}})

    def test_declared_sealed_source_file_rejected_before_sampling_body(self):
        self.header(source_file=LOCKED+'.dem')
        with patch.object(alignment,'_digest',side_effect=AssertionError('forbidden body')):
            with self.assertRaises(ValueError):alignment.read_replay_samples(self.root/'hero_samples.jsonl')

    def test_baseline_authorizes_archive_before_reading_future_target_candidates(self):
        self.header(match_id=LOCKED,parsed_match_id=LOCKED)
        candidate=self.root/'future-targets.json'
        reader=baselines._read_object
        def guarded(path,*args,**kwargs):
            self.assertNotEqual(Path(path),candidate,'candidate answers opened before archive identity')
            return reader(path,*args,**kwargs)
        with patch.object(baselines,'_read_object',side_effect=guarded):
            with self.assertRaises(ValueError):baselines.evaluate_case(self.root,candidate,{},lambda:None)

    def test_visibility_readers_inherit_metadata_before_body_gate(self):
        from dota2_insights.minimap.replay_visibility_windows import read_event_candidates,prepare_windows
        from dota2_insights.minimap.visibility_time_audit import VisibilityTimeAudit
        self.header(match_id=LOCKED,parsed_match_id=LOCKED)
        for reader in (lambda:read_event_candidates(self.root/'hero_samples.jsonl'),
                       lambda:prepare_windows(replay_path=self.root/'hero_samples.jsonl',player_team=2),
                       lambda:VisibilityTimeAudit(self.root/'visibility.jsonl',expected_source={})):
            with self.assertRaises(ValueError):reader()

    def test_allowed_generated_archive_and_empty_baseline_cohort_still_work(self):
        rows=[{'schema_version':1,'kind':'hero_sample','match_id':'12345','hero_id':'npc_dota_hero_axe',
            'tick':n,'game_time_s':float(n),'team':3,'world_x':16000+n,'world_y':16000,
            'alive':True,'paused':False,'game_time_basis':'gem_pause_aware_engine_game_clock',
            'coordinate_basis':'gem_cell128_plus_vec_raw_coordinates','packet_boundary':'after_entity_updates',
            'visibility':{'radiant':{'state':'visible'}}} for n in (1,2)]
        raw=b''.join((json.dumps(r)+'\n').encode() for r in rows)
        path=self.root/'hero_samples.jsonl';path.write_bytes(raw)
        spec={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        _,manifest_raw=self.header(files={'hero_samples.jsonl':spec})
        projected,source=alignment.read_replay_samples(path)
        self.assertEqual(len(projected),2)
        self.assertEqual(source['original_replay']['match_id'],'12345')
        candidates={'schema_version':1,'kind':'offline_replay_raw_visibility_candidates',
            'runtime_integration':False,'hidden_positions_exported':False,
            'visibility_semantics_verified':False,'training_ready':False,'windows':[],
            'player_perspective_team':2,'main_in_progress_bounds':{'clock_min_s':0.,'clock_max_s':10.},
            'postgame_tail_excluded':True,'sources':{'hero_samples':{**spec,
                'manifest':{'sha256':hashlib.sha256(manifest_raw).hexdigest()},
                'original_replay':{'match_id':'12345'}}}}
        candidate=self.root/'candidates.json';candidate.write_text(json.dumps(candidates))
        result=baselines.evaluate_case(self.root,candidate,{},lambda:None)
        self.assertEqual(result['events'],[])
        self.assertEqual(result['sources']['match_id'],'12345')

    def test_non_match_hero_stats_envelope_remains_valid(self):
        payload=[{'name':'npc_dota_hero_axe','move_speed':300}]
        obj={'schema_version':1,'provider':'opendota_raw','source_url':'https://api.opendota.com/api/heroStats',
             'payload':payload,'payload_sha256':hashlib.sha256(baselines._canonical(payload)).hexdigest()}
        path=self.root/'stats.json';path.write_text(json.dumps(obj))
        speeds,source=baselines.read_base_speeds(path)
        self.assertEqual(speeds,{'npc_dota_hero_axe':300})
        self.assertFalse(source['network_used'])
