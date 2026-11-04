import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from dota2_insights.map_holdout import sha, save
from dota2_insights.map_updates import (MapUpdateAdapter, PROFILE, capture, check_prototype, compare,
    inspect_installation, latest_revision, prepare_revision, read_revision, validate)
from dota2_insights.map_updates import read_prepared


def compiled(version=1):
    return struct.pack('<IHHII4sII', 32, 12, version, 8, 1, b'DATA', 8, 4)+b'test'


def resources():
    return {'maps/dota.vmap_c': compiled(), 'maps/dota.gnv':
        struct.pack('<IfffIIii', 0xFADEBEAD, 64, 32, 32, 2, 2, -1, -1)+bytes([1,16,20,0]),
        'maps/dota.trm': struct.pack('<4sIIII', b'trmp', 1, 64, 1, 1)+bytes(44)+b'\0'+struct.pack('<iii',0,0,0),
        'maps/dota.vhcg': b'vhcg'+bytes(32), 'maps/dota/world_physics.vmdl_c': compiled(),
        'maps/dota/entities/default_ents.vents_c': compiled()}


def vpk(items, reverse=False):
    groups = {}
    for name, raw in items.items():
        folder, leaf = name.rsplit('/',1)
        filename, extension = leaf.rsplit('.',1)
        groups.setdefault(extension, {}).setdefault(folder, []).append((filename,raw))
    tree, payload = bytearray(), bytearray()
    for extension in sorted(groups, reverse=reverse):
        tree.extend(extension.encode()+b'\0')
        for folder in sorted(groups[extension], reverse=reverse):
            tree.extend(folder.encode()+b'\0')
            for filename, raw in sorted(groups[extension][folder], reverse=reverse):
                tree.extend(filename.encode()+b'\0')
                tree.extend(struct.pack('<IHHIIH',zlib.crc32(raw)&0xFFFFFFFF,0,0x7FFF,len(payload),len(raw),0xFFFF))
                payload.extend(raw)
            tree.extend(b'\0')
        tree.extend(b'\0')
    tree.extend(b'\0')
    return struct.pack('<III',0x55AA1234,1,len(tree))+tree+payload


def install(root, *, build=100, items=None, reverse=False, date='synthetic'):
    (root/'maps').mkdir(parents=True,exist_ok=True)
    (root/'maps/dota.vpk').write_bytes(vpk(items or resources(),reverse))
    (root/'steam.inf').write_text(f'ClientVersion={build}\nVersionDate={date}\n',encoding='utf-8')


def fake_extract(source, output, decoder):
    identity=inspect_installation(source)['identity']
    outputs={'root':'root-assets.json','landmarks':'landmarks.json','collision':'collision.json'}
    for key,name in outputs.items():
        folder=output/key;folder.mkdir()
        save(folder/name,{'client_build':identity['client_build'],'map_asset_sha256':identity['map_asset_sha256'],
            'production_ready':False,'map_calibration_verified':False})
        (folder/'COMPLETE').write_text(sha(folder/name),encoding='ascii')
    return outputs


class MapUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.game=self.root/'game';self.store=self.root/'store'
        install(self.game)
        self.no_game=patch('dota2_insights.replay_workbench.game_running',return_value=False)
        self.no_game.start();self.addCleanup(self.no_game.stop)
        self.outputs=patch('dota2_insights.history.checked_output',side_effect=lambda root,p:Path(p).resolve())
        self.outputs.start();self.addCleanup(self.outputs.stop)

    def test_inspection_fingerprints_every_resource_and_only_attempts_known_layouts(self):
        snapshot=inspect_installation(self.game)
        self.assertEqual(len(snapshot['resources']),6)
        self.assertEqual(snapshot['probe']['eligible_profile'],PROFILE)
        self.assertEqual(validate(snapshot),snapshot)
        self.assertFalse(snapshot['production_ready'])
        self.assertIsNone(snapshot['identity']['patch'])
        self.assertEqual(snapshot['identity']['map_asset_sha256'],sha(self.game/'maps/dota.vpk'))
        expected=hashlib.sha256(resources()['maps/dota.gnv']).hexdigest()
        self.assertEqual(next(r['sha256'] for r in snapshot['resources'] if r['resource']=='maps/dota.gnv'),expected)

    def test_no_baseline_and_exact_unchanged_are_distinct(self):
        current=inspect_installation(self.game)
        self.assertEqual(compare(None,current)['status'],'baseline_missing')
        result=compare(current,current)
        self.assertEqual(result['status'],'unchanged')
        self.assertFalse(result['requires_new_extraction'])
        self.assertFalse(result['calibration_inherited'])
        self.assertFalse(result['production_ready'])

    def test_client_upgrade_same_map_never_inherits_screen_calibration(self):
        old=inspect_installation(self.game)
        install(self.game,build=101)
        new=inspect_installation(self.game)
        diff=compare(old,new)
        self.assertEqual(diff['status'],'client_changed_same_map')
        self.assertTrue(diff['static_payload_reuse_candidate'])
        self.assertTrue(diff['requires_new_validation'])
        self.assertFalse(diff['requires_new_extraction'])
        self.assertEqual(diff['external_dependency_compatibility'],'unknown')

    def test_payload_changes_additions_and_removals_are_reported_by_resource(self):
        old_items=resources()|{'maps/dota/old.raw':b'old'}
        install(self.game,items=old_items);old=inspect_installation(self.game)
        new_items=resources()|{'maps/dota/new.raw':b'new'}
        new_items['maps/dota.gnv']=new_items['maps/dota.gnv'][:-1]+b'\x05'
        install(self.game,items=new_items);new=inspect_installation(self.game)
        diff=compare(old,new)
        self.assertEqual(diff['status'],'map_content_changed')
        self.assertEqual(diff['changed'],['maps/dota.gnv'])
        self.assertEqual(diff['added'],['maps/dota/new.raw'])
        self.assertEqual(diff['removed'],['maps/dota/old.raw'])
        self.assertTrue(diff['requires_new_extraction'])
        self.assertFalse(diff['static_payload_reuse_candidate'])

    def test_repacking_identical_resources_is_not_a_terrain_change(self):
        old=inspect_installation(self.game)
        install(self.game,reverse=True);new=inspect_installation(self.game)
        self.assertNotEqual(old['identity']['map_asset_sha256'],new['identity']['map_asset_sha256'])
        diff=compare(old,new)
        self.assertEqual(diff['status'],'archive_repacked_same_resources')
        self.assertEqual(diff['changed'],[])
        self.assertFalse(diff['requires_new_extraction'])
        self.assertTrue(diff['requires_new_validation'])

    def test_metadata_change_and_rollback_have_their_own_revision_identity(self):
        old=inspect_installation(self.game)
        install(self.game,date='other');new=inspect_installation(self.game)
        self.assertEqual(compare(old,new)['status'],'installation_metadata_changed')
        self.assertNotEqual(old['revision_id'],new['revision_id'])
        install(self.game,build=90)
        self.assertEqual(compare(old,inspect_installation(self.game))['status'],'client_changed_same_map')

    def test_capture_is_idempotent_keeps_old_revision_and_deduplicates_archive(self):
        first,old=capture(self.game,self.store)
        first_hash=sha(first/'snapshot.json')
        again,_=capture(self.game,self.store)
        self.assertEqual(first,again)
        self.assertEqual(sha(first/'snapshot.json'),first_hash)
        install(self.game,build=101)
        second,new=capture(self.game,self.store)
        self.assertNotEqual(first,second)
        self.assertEqual(read_revision(first)['identity']['client_build'],100)
        self.assertEqual(read_revision(second)['identity']['client_build'],101)
        self.assertEqual(len(list((self.store/'blobs').glob('*.vpk'))),1)
        self.assertEqual(latest_revision(self.store)[0],second)
        self.assertEqual(read_revision(first)['revision_id'],old['revision_id'])
        self.assertEqual(latest_revision(self.store)[1]['revision_id'],new['revision_id'])

    def test_failed_capture_leaves_old_pointer_and_retry_can_complete(self):
        first,_=capture(self.game,self.store)
        pointer=sha(self.store/'latest-capture.json')
        install(self.game,build=101)
        original_save=save
        def changing_save(path,value):
            original_save(path,value)
            if Path(path).name=='snapshot.json':
                (self.game/'steam.inf').write_text('ClientVersion=999\n',encoding='utf-8')
        with patch('dota2_insights.map_updates.save',side_effect=changing_save):
            with self.assertRaisesRegex(ValueError,'source_changed_during_capture'):
                capture(self.game,self.store)
        self.assertEqual(sha(self.store/'latest-capture.json'),pointer)
        self.assertEqual(latest_revision(self.store)[0],first)
        failures=list((self.store/'staging').glob('*/FAILED.json'))
        self.assertEqual(len(failures),1)
        self.assertFalse((failures[0].parent/'COMPLETE').exists())
        install(self.game,build=101)
        path,new=capture(self.game,self.store)
        self.assertEqual(new['identity']['client_build'],101)
        self.assertTrue((path/'COMPLETE').is_file())

    def test_tampered_snapshot_archive_and_pointer_are_refused(self):
        path,_=capture(self.game,self.store)
        raw=(path/'snapshot.json').read_bytes()
        (path/'snapshot.json').write_bytes(raw+b' ')
        with self.assertRaisesRegex(ValueError,'receipt_changed'):read_revision(path)
        (path/'snapshot.json').write_bytes(raw)
        (self.store/'latest-capture.json').write_text(json.dumps({'revision_id':'../outside'}))
        with self.assertRaisesRegex(ValueError,'pointer_invalid'):latest_revision(self.store)
        (path/'source/maps/dota.vpk').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'archive_changed'):read_revision(path)

    def test_unknown_layout_is_captured_but_old_adapter_never_runs(self):
        items=resources();items['maps/dota.gnv']=b'new_grid_version'
        install(self.game,items=items)
        path,snapshot=capture(self.game,self.store)
        self.assertIsNone(snapshot['probe']['eligible_profile'])
        self.assertEqual(compare(None,snapshot)['status'],'baseline_missing')
        with self.assertRaisesRegex(ValueError,'requires_new_adapter'):
            prepare_revision(path,decoder=Path('not-run.exe'))
        self.assertFalse((self.store/'derived').exists())
        self.assertTrue((path/'source/maps/dota.vpk').is_file())

    def test_explicit_future_adapter_and_standard_output_contract(self):
        items=resources();items['maps/dota.gnv']=b'future'
        install(self.game,items=items)
        revision,snapshot=capture(self.game,self.store)
        adapter=MapUpdateAdapter('future-test',fake_extract,accepts=lambda s:s['probe']['eligible_profile'] is None)
        output,result=prepare_revision(revision,decoder=None,adapters=(adapter,))
        self.assertEqual(result['adapter_profile'],'future-test')
        self.assertEqual(result['state'],'extracted_unverified')
        self.assertEqual(result['calibration_status'],'unverified')
        self.assertFalse(result['production_ready'])
        self.assertEqual((output/'COMPLETE').read_text().strip(),sha(output/'extraction.json'))
        self.assertEqual(read_revision(revision),snapshot)
        self.assertEqual(read_prepared(output,revision),result)

    def test_consumer_rejects_changed_payload_even_if_metadata_receipt_unchanged(self):
        revision,_=capture(self.game,self.store)
        def payload(source,output,decoder):
            result=fake_extract(source,output,decoder)
            (output/'collision/mesh.bin').write_bytes(b'synthetic geometry')
            return result
        output,result=prepare_revision(revision,decoder=None,adapters=(MapUpdateAdapter(PROFILE,payload),))
        self.assertEqual(read_prepared(output,revision),result)
        original_metadata=sha(output/'extraction.json')
        (output/'collision/mesh.bin').write_bytes(b'changed geometry')
        self.assertEqual(sha(output/'extraction.json'),original_metadata)
        with self.assertRaisesRegex(ValueError,'payload_changed'):read_prepared(output,revision)

    def test_consumer_rejects_wrong_revision_and_fake_ready_state(self):
        revision,_=capture(self.game,self.store)
        output,result=prepare_revision(revision,decoder=None,adapters=(MapUpdateAdapter(PROFILE,fake_extract),))
        install(self.game,build=101)
        new,_=capture(self.game,self.store)
        with self.assertRaisesRegex(ValueError,'revision_mismatch'):read_prepared(output,new)
        result['production_ready']=True
        (output/'extraction.json').write_text(json.dumps(result))
        (output/'COMPLETE').write_text(sha(output/'extraction.json'))
        with self.assertRaisesRegex(ValueError,'schema_or_certification'):read_prepared(output,revision)

    def test_partial_extraction_never_replaces_prior_success_and_retry_uses_new_attempt(self):
        revision,_=capture(self.game,self.store)
        adapter=MapUpdateAdapter(PROFILE,fake_extract)
        first,_=prepare_revision(revision,decoder=None,adapters=(adapter,))
        first_sha=sha(first/'extraction.json')
        def fail(source,output,decoder):
            (output/'partial.txt').write_text('partial')
            raise RuntimeError('synthetic_decode_failure')
        with self.assertRaisesRegex(RuntimeError,'synthetic_decode_failure'):
            prepare_revision(revision,decoder=None,adapters=(MapUpdateAdapter(PROFILE,fail),))
        failures=list((self.store/'derived').glob('*/*/FAILED.json'))
        self.assertEqual(len(failures),1)
        self.assertFalse((failures[0].parent/'COMPLETE').exists())
        second,_=prepare_revision(revision,decoder=None,adapters=(adapter,))
        self.assertNotEqual(first,second)
        self.assertEqual(sha(first/'extraction.json'),first_sha)

    def test_adapter_cannot_return_wrong_identity_or_claim_raw_data_certified(self):
        revision,_=capture(self.game,self.store)
        for bad in ('identity','certified','type'):
            def extractor(source,output,decoder):
                result=fake_extract(source,output,decoder)
                p=output/'root/root-assets.json'
                value=json.loads(p.read_text())
                if bad=='identity':value['client_build']+=1
                elif bad=='type':value['client_build']=float(value['client_build'])
                else:value['production_ready']=True
                p.write_text(json.dumps(value))
                (p.parent/'COMPLETE').write_text(sha(p))
                return result
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                prepare_revision(revision,decoder=None,adapters=(MapUpdateAdapter(PROFILE,extractor),))
        self.assertEqual(len(list((self.store/'derived').glob('*/*/COMPLETE'))),0)

    def test_source_mutation_during_adapter_is_refused(self):
        revision,_=capture(self.game,self.store)
        def mutating(source,output,decoder):
            result=fake_extract(source,output,decoder)
            (source/'steam.inf').write_text('ClientVersion=999\n')
            return result
        with self.assertRaisesRegex(ValueError,'metadata_changed'):
            prepare_revision(revision,decoder=None,adapters=(MapUpdateAdapter(PROFILE,mutating),))
        self.assertEqual(len(list((self.store/'derived').glob('*/*/COMPLETE'))),0)

    def test_future_adapter_output_walk_stops_at_file_and_directory_budgets(self):
        revision,_=capture(self.game,self.store)
        for kind in ('files','directories'):
            def too_many(source,output,decoder):
                result=fake_extract(source,output,decoder)
                for i in range(129 if kind=='files' else 65):
                    p=output/'root'/str(i)
                    if kind=='files':p.write_bytes(b'x')
                    else:p.mkdir()
                return result
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'budget'):
                prepare_revision(revision,decoder=None,adapters=(MapUpdateAdapter(PROFILE,too_many),))
        self.assertEqual(len(list((self.store/'derived').glob('*/*/COMPLETE'))),0)

    def test_game_running_and_mid_scan_source_change_are_refused(self):
        with patch('dota2_insights.replay_workbench.game_running',return_value=True), \
                patch('dota2_insights.map_updates.directory_bytes') as read:
            with self.assertRaises(InterruptedError):inspect_installation(self.game)
            read.assert_not_called()
        raw=(self.game/'maps/dota.vpk').read_bytes()
        with patch('dota2_insights.map_updates.directory_bytes',side_effect=[raw,raw+b'X']):
            with self.assertRaisesRegex(ValueError,'source_changed'):inspect_installation(self.game)

    def test_validation_rejects_fake_certification_and_content_manifest_edit(self):
        snapshot=inspect_installation(self.game)
        for key,value in (('production_ready',True),('schema_version',True),('calibration_status','passed')):
            edited=copy.deepcopy(snapshot);edited[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate(edited)
        edited=copy.deepcopy(snapshot);edited['resources'][0]['sha256']='a'*64
        with self.assertRaisesRegex(ValueError,'content_manifest'):validate(edited)

    def test_prototype_check_reports_staleness_but_never_rebinds_historical_data(self):
        installed=inspect_installation(self.game)
        old={'client_build':99,'map_asset_sha256':installed['identity']['map_asset_sha256']}
        with patch('dota2_insights.map_prototype.open_scene',return_value=old):
            result=check_prototype('owned-synthetic-package',installed)
        self.assertEqual(result['status'],'client_changed_same_map')
        self.assertTrue(result['historical_view_allowed'])
        self.assertFalse(result['reuse_for_current_game_allowed'])
        self.assertTrue(result['requires_new_validation'])
        self.assertEqual(old['client_build'],99)

    def test_check_cli_is_read_only_and_writes_an_explicit_report(self):
        from tools.map_update import main
        revision,_=capture(self.game,self.store)
        pointer_hash=sha(self.store/'latest-capture.json')
        args=['map_update.py','check','--game',str(self.game),'--store',str(self.store),
            '--output',str(self.root/'check')]
        stdout=io.StringIO()
        with patch('sys.argv',args),patch('sys.stdout',stdout):main()
        report=json.loads(stdout.getvalue())
        self.assertEqual(report['status'],'unchanged')
        self.assertEqual(sha(self.store/'latest-capture.json'),pointer_hash)
        self.assertTrue((self.root/'check/check.json').is_file())
        self.assertEqual(latest_revision(self.store)[0],revision)
