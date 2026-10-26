"""Small generated payloads; no real provider requests, credentials or game data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights import distribution_bundle as b
from dota2_insights.contracts.native_statistics import bundle_checksum
from dota2_insights.contracts.native_rules import rules_checksum


def statistics_payload():
    scope={'game_version':'7.41f','game_mode':'ranked','rank':'all','region':'all'}
    value={'schema_version':1,'bundle_id':'generated_stats','provider':'stratz',
        'source_url':'https://api.stratz.com/graphql','downloaded_at_utc':'2026-10-25T12:00:00Z',
        'source_updated_at_utc':None,'coverage_start_utc':'2026-10-24T00:00:00Z',
        'coverage_end_utc':'2026-10-25T00:00:00Z','scope':scope,'pair_scope':dict(scope),
        'heroes':[{'hero_id':'npc_dota_hero_axe','name':'Axe','match_count':100,'win_count':50,'capabilities':[],'capability_source':None},
                  {'hero_id':'npc_dota_hero_bane','name':'Bane','match_count':100,'win_count':50,'capabilities':[],'capability_source':None}],
        'relations':[{'hero_id':'npc_dota_hero_axe','other_id':'npc_dota_hero_bane','kind':'against','match_count':10,'delta':.01}]}
    value['sha256']=bundle_checksum(value)
    return value


def rules_payload():
    value={'schema_version':2,'ruleset_id':'generated_rules','source_kind':'installed_valve_resources',
        'reviewed_at_utc':'2026-10-25T00:00:00Z','source_build':{'client_version':'6966','source_revision':'123','version_date':'2026-10-24'},
        'applies_to_game_version':None,'version_evidence':None,'criteria_version':'generated_v1',
        'criteria':[{'criterion_id':'control','capability':'disable','definition':'Generated criterion'}],
        'sources':[{'source_id':'hero','kind':'hero_definition','publisher':'Valve','resource_id':'hero','sha256':'a'*64}],
        'heroes':[{'hero_id':'npc_dota_hero_axe','numeric_hero_id':2,'assessments':{
            'disable':{'value':'unknown','criterion_id':'control','ability_ids':[],'source_ids':[],
                       'basis':'Not reviewed','review_scope':'not_reviewed'}}}]}
    value['sha256']=rules_checksum(value)
    return value


class BundleTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.allow=patch('dota2_insights.history.checked_output',side_effect=lambda _root,path:Path(path))
        self.allow.start()
        self.addCleanup(self.allow.stop)

    def publish(self,kind='statistics',value=None):
        value=value or (statistics_payload() if kind=='statistics' else rules_payload())
        source=self.root/(kind+'-source.json')
        source.write_text(json.dumps(value),encoding='utf-8')
        output=self.root/('package-'+kind)
        manifest=b.publish(kind,source,output,created_at_utc='2026-10-25T12:00:00Z')
        return source,output,manifest

    def inspect(self,folder,**kwargs):
        return b.inspect(folder,game_version='7.41f',now_utc='2026-10-25T12:00:00Z',**kwargs)

    def test_roundtrip_native_payload_without_activation(self):
        source,folder,manifest=self.publish()
        r=self.inspect(folder)
        self.assertEqual(r['compatibility_status'],'eligible_for_native_consumer_validation')
        self.assertFalse(r['activation_allowed'])
        self.assertTrue(r['native_consumer_validation_required'])
        self.assertEqual(json.loads((folder/'payload.json').read_text()),json.loads(source.read_text()))

    def test_stale_and_scope_mismatch(self):
        _,folder,_=self.publish()
        r=b.inspect(folder,game_version='7.41f',now_utc='2026-10-27T00:00:00Z')
        self.assertEqual(r['compatibility_status'],'stale_statistics')
        self.assertEqual(self.inspect(folder,expected_scope={'rank':'high'})['compatibility_status'],'scope_mismatch')

    def test_semantic_version_not_inferred_from_build(self):
        _,folder,_=self.publish('rules')
        self.assertEqual(self.inspect(folder)['compatibility_status'],'semantic_version_unverified')
        self.assertEqual(b.inspect(folder,now_utc='2026-10-25T12:00:00Z')['compatibility_status'],'semantic_version_unverified')

    def test_declared_rule_version_still_requires_source_binding(self):
        value=rules_payload()
        value['sources'].append({'source_id':'patch','kind':'version_confirmation','publisher':'Valve','resource_id':'patch','sha256':'b'*64})
        value['applies_to_game_version']='7.41f'
        value['version_evidence']={'source_ids':['patch'],'basis':'Generated explicit semantic evidence'}
        value['sha256']=rules_checksum(value)
        _,folder,_=self.publish('rules',value)
        self.assertEqual(self.inspect(folder)['compatibility_status'],'source_binding_required_before_native_consumer_validation')

    def test_bad_native_checksum_and_mixed_scope_rejected(self):
        for kind in ('checksum','scope','boolean_samples','win_count'):
            value=statistics_payload()
            if kind=='scope':value['pair_scope']['rank']='other'
            if kind=='boolean_samples':value['heroes'][0]['match_count']=True
            if kind=='win_count':value['heroes'][0]['win_count']=101
            if kind!='checksum':value['sha256']=bundle_checksum(value)
            else:value['sha256']='0'*64
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                self.publish(value=value)

    def test_changed_payload_and_missing_completion_refused(self):
        _,folder,_=self.publish()
        with (folder/'payload.json').open('ab') as stream:stream.write(b' ')
        with self.assertRaisesRegex(ValueError,'payload_checksum'):self.inspect(folder)
        (folder/'COMPLETE').unlink()
        with self.assertRaises(OSError):self.inspect(folder)

    def test_manifest_metadata_cannot_disagree_with_payload(self):
        _,folder,manifest=self.publish()
        manifest['applicability']['semantic_game_version']='7.99'
        manifest['package_id']=b.digest(b.canonical({k:v for k,v in manifest.items() if k!='package_id'}))
        (folder/'manifest.json').write_bytes(b.canonical(manifest))
        (folder/'COMPLETE').write_text(manifest['package_id']+'\n',encoding='ascii')
        with self.assertRaisesRegex(ValueError,'metadata_mismatch'):self.inspect(folder)

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        path=self.root/'bad.json'
        for raw in ('{"a":1,"a":2}','{"a":NaN}'):
            path.write_text(raw)
            with self.assertRaises(ValueError):b.read_json(path)

    def test_synthetic_data_cannot_be_published_by_default(self):
        value=statistics_payload()
        value['provider']='synthetic'
        value['sha256']=bundle_checksum(value)
        with self.assertRaisesRegex(ValueError,'synthetic'):self.publish(value=value)

    def test_synthetic_transport_remains_ineligible(self):
        value=statistics_payload();value['provider']='synthetic';value['sha256']=bundle_checksum(value)
        source=self.root/'synthetic.json';source.write_text(json.dumps(value))
        folder=self.root/'fixture'
        b.publish('statistics',source,folder,allow_synthetic=True,created_at_utc='2026-10-25T12:00:00Z')
        self.assertEqual(self.inspect(folder)['compatibility_status'],'synthetic_not_for_live_use')

    def test_existing_package_never_overwritten(self):
        source,folder,_=self.publish()
        before=(folder/'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):b.publish('statistics',source,folder)
        self.assertEqual((folder/'manifest.json').read_bytes(),before)

    def test_future_creation_and_version_mismatch(self):
        _,folder,_=self.publish()
        self.assertEqual(b.inspect(folder,game_version='7.42',now_utc='2026-10-25T12:00:00Z')['compatibility_status'],'semantic_version_mismatch')
        self.assertEqual(b.inspect(folder,game_version='7.41f',now_utc='2026-10-25T11:00:00Z')['compatibility_status'],'package_clock_skew')

    def test_boolean_manifest_version_rejected(self):
        _,_,manifest=self.publish()
        manifest['schema_version']=True
        with self.assertRaisesRegex(ValueError,'manifest_schema'):b.validate_manifest(manifest)

    def test_rule_native_version_label_must_match_actual_payload(self):
        _,folder,manifest=self.publish('rules')
        manifest['native_format']='draft_capability_rules_v1'
        manifest['package_id']=b.digest(b.canonical({k:v for k,v in manifest.items() if k!='package_id'}))
        (folder/'manifest.json').write_bytes(b.canonical(manifest))
        (folder/'COMPLETE').write_bytes((manifest['package_id']+'\n').encode())
        with self.assertRaisesRegex(ValueError,'metadata_mismatch'):self.inspect(folder)

    def test_payload_download_cannot_follow_package_creation(self):
        value=statistics_payload()
        value['downloaded_at_utc']='2026-10-26T00:00:00Z'
        value['sha256']=bundle_checksum(value)
        with self.assertRaisesRegex(ValueError,'source_time_follows_package'):self.publish(value=value)

    def test_legacy_rule_schema_v1_roundtrip(self):
        value=rules_payload();value['schema_version']=1;value['sha256']=rules_checksum(value)
        _,folder,_=self.publish('rules',value)
        r=self.inspect(folder)
        self.assertEqual(r['native_format'],'draft_capability_rules_v1')
        self.assertEqual(r['compatibility_status'],'semantic_version_unverified')

    def test_unknown_manifest_fields_and_generator_claims_refused(self):
        _,_,manifest=self.publish()
        manifest['generator']['token']='NOT_A_REAL_SECRET'
        manifest['package_id']=b.digest(b.canonical({k:v for k,v in manifest.items() if k!='package_id'}))
        with self.assertRaisesRegex(ValueError,'generator_schema'):b.validate_manifest(manifest)

    def test_interrupted_payload_write_never_has_completion_marker(self):
        source=self.root/'source.json';source.write_text(json.dumps(statistics_payload()))
        folder=self.root/'interrupted'
        original=Path.open
        def fail_payload(path,*args,**kwargs):
            if path.name=='payload.json':raise OSError('synthetic interrupted write')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',fail_payload):
            with self.assertRaises(OSError):b.publish('statistics',source,folder)
        self.assertFalse((folder/'COMPLETE').exists())
        self.assertFalse((folder/'manifest.json').exists())
