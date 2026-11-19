"""Generated timing/counter streams and independently sealed synthetic rows."""
import copy
import json
import unittest

from dota2_insights.observation_runtime import RuntimeProfile, CacheDeltas, render_markdown
import test_observation_handoff as fixtures
from dota2_insights.observation_handoff import review, render_markdown as render_review


def cache(hits,misses,generation='a'*32,**changes):
    value={'hits':hits,'misses':misses,'entries':2,'limit':64,
           'basis':'identical_pixels_only','generation_id':generation}
    value.update(changes)
    return value


class RuntimeProfileTests(unittest.TestCase):
    def test_exact_nearest_rank_percentiles_missing_invalid_and_capacity(self):
        profile=RuntimeProfile()
        for value in range(1,21):profile.add({'attempt':{'stage_ms':{'items':value}}})
        profile.add({'attempt':{'stage_ms':{'items':True}}})
        profile.add({'attempt':{'stage_ms':{'items':float('nan')}}})
        profile.add({})
        result=profile.result();series=result['timings']['stage.items']
        self.assertEqual((series['median_ms'],series['p95_ms'],series['maximum_ms']),(10.5,19,20))
        self.assertEqual((series['samples'],series['missing'],series['invalid']),(20,1,2))
        self.assertIsNone(result['timings']['performance.queue_wait']['median_ms'])
        limited=RuntimeProfile(sample_limit=2)
        for value in (0,10,999):limited.add({'performance_ms':{'capture':value}})
        self.assertEqual(limited.result()['timings']['performance.capture']['capacity_excluded'],1)
        self.assertFalse(result['stage_times_additive'])
        self.assertFalse(result['game_fps_or_cpu_effect_verified'])

    def test_first_cumulative_value_is_baseline_and_new_generation_never_subtracts_old(self):
        stats=CacheDeltas('items')
        for row in (cache(100,50),cache(107,53),cache(107,53),
                    cache(999,999,'b'*32),cache(1000,1002,'b'*32)):stats.add(row)
        result=stats.result()
        self.assertEqual((result['observed_delta_hits'],result['observed_delta_misses']),(8,6))
        self.assertEqual(result['generation_count'],2)
        self.assertEqual(result['valid_adjacent_intervals'],3)

    def test_missing_snapshots_and_legacy_without_generation_break_differences(self):
        stats=CacheDeltas('items')
        for row in (cache(5,2),None,cache(15,6),cache(16,7),cache(20,10,generation=None),cache(30,20)):
            stats.add(row)
        result=stats.result()
        self.assertEqual((result['observed_delta_hits'],result['observed_delta_misses']),(1,1))
        self.assertEqual(result['observed_delta_hit_fraction'],.5)
        self.assertEqual(result['missing_samples'],1)
        self.assertEqual(result['samples_without_generation'],1)
        legacy=CacheDeltas('items');legacy.add(cache(50,50,generation=None))
        self.assertIsNone(legacy.result()['observed_delta_hit_fraction'])

    def test_counter_regression_config_change_or_retired_generation_invalidates_its_whole_delta(self):
        cases=[(cache(1,1),cache(5,2),cache(4,2)),
               (cache(1,1),cache(5,2),cache(6,3,limit=128)),
               (cache(1,1),cache(5,2),None,cache(0,0,'b'*32),cache(10,5))]
        for rows in cases:
            stats=CacheDeltas('items')
            for row in rows:stats.add(row)
            result=stats.result()
            self.assertEqual(result['invalid_generation_count'],1)
            self.assertEqual(result['observed_delta_hits'],0)
            self.assertIsNone(result['observed_delta_hit_fraction'])

    def test_bad_counter_types_zero_denominator_png_scope_and_generation_capacity(self):
        stats=CacheDeltas('slot_png');stats.add(cache(1,1))
        self.assertEqual(stats.result()['invalid_samples'],1)
        png=cache(0,0,basis='identical_pixels_shape_dtype_only',retained_bytes=0,byte_limit=2097152,
                  scope='slot_png_encoding_not_recognition_values_or_timestamps')
        stats.add(png);stats.add(png)
        self.assertIsNone(stats.result()['observed_delta_hit_fraction'])
        malformed=CacheDeltas('items');malformed.add(cache(True,2))
        self.assertEqual(malformed.result()['invalid_samples'],1)
        bounded=CacheDeltas('items')
        for index in range(33):bounded.add(cache(0,0,generation=f'{index:032x}'))
        self.assertEqual(bounded.result()['generation_count'],32)
        self.assertEqual(bounded.result()['capacity_excluded_samples'],1)

    def test_closed_observation_review_and_markdown_include_the_runtime_profile(self):
        fixture=fixtures.ObservationHandoffTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        rows=[]
        for index in range(3):
            row=copy.deepcopy(fixture.row)
            row['captured_at_utc']=row['attempt']['captured_at_utc']=f'2026-10-26T00:00:0{index}Z'
            row['attempt'].update(stage_ms={'items':10+index},cache={'items':cache(100+2*index,50+index)})
            row['performance_ms']={'capture':1,'queue_wait':2,'recognition':20}
            rows.append(row)
        raw=b''.join((json.dumps(row)+'\n').encode() for row in rows)
        fixture.manifest['accepted_observations_at_cutoff']=3
        fixture.write_payload(raw)
        fixture.manifest['payload']['rows']=3;package_id=fixture.write_manifest()
        result=review(fixture.root,fixture.session)
        profile=result['runtime_profile']
        self.assertEqual(profile['rows'],3)
        self.assertEqual(profile['timings']['stage.items']['median_ms'],11)
        self.assertEqual(profile['caches']['items']['observed_delta_hits'],4)
        self.assertTrue(result['source_verified_before_and_after'])
        text=render_review([{**result,'root_key':'helper_history','source':str(fixture.source.relative_to(fixture.root)),
                             'package_id':package_id,'also_in_closed_launcher_scope':False,
                             'linked_native_gsi_packages':[]}],[],index_state='ready')
        self.assertIn('相邻同代次',text)
        self.assertIn('stage.items',text)
        self.assertIn('不代表游戏FPS',text)
        self.assertTrue(render_markdown(profile))
