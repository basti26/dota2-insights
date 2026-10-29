import base64
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_tier1_postgame_store import entry
from dota2_insights.analysis_batch_registry import merge, version


def batch(folder, entries, stamp='2026-10-29T00:00:00+00:00'):
    folder.mkdir()
    raw=json.dumps({'schema_version':1,'kind':'frozen_verified_ward_exports',
                    'raw_replays_transferred':False,'frozen_at_utc':stamp,'matches':entries}).encode()
    (folder/'snapshot.json').write_bytes(raw)
    receipt={'status':'complete','snapshot_sha256':hashlib.sha256(raw).hexdigest(),
             'matches':[{'match_id':e['match_id'],'wards_sha256':e['catalog']['analysis_sha256'],
                         'context_collected':False} for e in entries]}
    (folder/'receipt.json').write_text(json.dumps(receipt))
    return folder


def changed_parser(e):
    e=copy.deepcopy(e);m=json.loads(base64.b64decode(e['manifest_base64']))
    m['parser_version']='different-parser'
    e['manifest_base64']=base64.b64encode(json.dumps(m).encode()).decode()
    return e


class AnalysisBatchRegistryTests(unittest.TestCase):
    def test_overlapping_batches_count_matches_once(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); a=batch(root/'a',[entry()]); b=batch(root/'b',[entry(),entry('9876543211')],
                                                                     '2026-10-29T01:00:00+00:00')
            before={p:p.read_bytes() for f in (a,b) for p in f.iterdir()}
            r=merge([a,b],root/'merged')
            self.assertEqual((r['unique_matches'],r['selected_matches'],r['duplicate_entries']),(2,2,1))
            s=json.loads((root/'merged/snapshot.json').read_text())
            self.assertEqual(len(s['matches']),2)
            self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_previous_registry_adds_new_match_and_is_idempotent(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);a=batch(root/'a',[entry()]); merge([a],root/'one')
            b=batch(root/'b',[entry('9876543211')])
            r=merge([b],root/'two',previous=root/'one/registry.json')
            self.assertEqual(r['selected_matches'],2)
            r2=merge([a,b],root/'three',previous=root/'two/registry.json')
            self.assertEqual(r2['selected_matches'],2)
            self.assertEqual(len(r2['batches']),2)
            self.assertEqual(len(r2['versions']),2)

    def test_changed_parser_requires_explicit_selection_and_preserves_both(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);a=batch(root/'a',[entry()]);b=batch(root/'b',[changed_parser(entry())])
            r=merge([a,b],root/'conflict')
            self.assertEqual(r['selected_matches'],0)
            self.assertEqual(r['conflicting_matches'],['9876543210'])
            self.assertFalse((root/'conflict/snapshot.json').exists())
            selected=r['versions'][0]['source_id']
            r2=merge([],root/'selected',previous=root/'conflict/registry.json',selection={'9876543210':selected})
            self.assertEqual(r2['selected_matches'],1)
            self.assertEqual(len(r2['versions']),2)
            self.assertEqual(sum(v['selected'] for v in r2['versions']),1)

    def test_previous_automatic_choice_is_not_a_conflict_override(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);a=batch(root/'a',[entry()]);merge([a],root/'one')
            b=batch(root/'b',[changed_parser(entry())])
            r=merge([b],root/'two',previous=root/'one/registry.json')
            self.assertEqual(r['selected_matches'],0)

    def test_previous_source_or_receipt_mutation_refused(self):
        for name in ('snapshot.json','receipt.json'):
            with TemporaryDirectory() as temp:
                root=Path(temp);a=batch(root/'a',[entry()]);merge([a],root/'one')
                (a/name).write_bytes((a/name).read_bytes()+b' ')
                with self.assertRaises(ValueError):merge([],root/'two',previous=root/'one/registry.json')

    def test_receipt_and_duplicate_rows_in_one_batch_refused(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);a=batch(root/'a',[entry(),entry()])
            with self.assertRaises(ValueError):merge([a],root/'merged')
        with TemporaryDirectory() as temp:
            root=Path(temp);a=batch(root/'a',[entry()])
            p=a/'receipt.json';r=json.loads(p.read_text());r['snapshot_sha256']='0'*64;p.write_text(json.dumps(r))
            with self.assertRaises(ValueError):merge([a],root/'merged')

    def test_missing_declared_context_not_silently_discarded(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);a=batch(root/'a',[entry()]);p=a/'receipt.json'
            r=json.loads(p.read_text());r['matches'][0]['context_collected']=True;p.write_text(json.dumps(r))
            with self.assertRaises(ValueError):merge([a],root/'merged')

    def test_unknown_selection_and_existing_output_refused(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);a=batch(root/'a',[entry()])
            with self.assertRaises(ValueError):merge([a],root/'merged',selection={'9876543210':'0'*64})
            root.joinpath('merged').mkdir()
            with self.assertRaises(ValueError):merge([a],root/'merged')

    def test_locked_identity_checked_before_decoding(self):
        with patch('dota2_insights.analysis_batch_registry.refuse_locked_match',side_effect=ValueError('locked')), \
             patch('dota2_insights.analysis_batch_registry.base64.b64decode') as decode:
            with self.assertRaises(ValueError):version(entry())
            decode.assert_not_called()

    def test_source_change_while_merging_refused_before_publishing(self):
        from dota2_insights import analysis_batch_registry as registry
        with TemporaryDirectory() as temp:
            root=Path(temp);a=batch(root/'a',[entry()]);original=registry.version
            def change(e):
                result=original(e)
                (a/'snapshot.json').write_bytes((a/'snapshot.json').read_bytes()+b' ')
                return result
            with patch.object(registry,'version',side_effect=change):
                with self.assertRaises(ValueError):merge([a],root/'merged')
            self.assertFalse((root/'merged').exists())

    def test_context_preserved_and_previous_capture_raw_change_refused(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);e=entry();m=json.loads(base64.b64decode(e['manifest_base64']))
            context=b'{"complete":true}'
            m['postgame_context']={'complete':True}
            m['files']['postgame_context.json']={'bytes':len(context),'sha256':hashlib.sha256(context).hexdigest()}
            e['manifest_base64']=base64.b64encode(json.dumps(m).encode()).decode()
            a=batch(root/'a',[e]);rp=a/'receipt.json';receipt=json.loads(rp.read_text())
            receipt['matches'][0]['context_collected']=True;rp.write_text(json.dumps(receipt))
            capture={'schema_version':1,'kind':'isolated_postgame_capture_export',
                     'manifest_base64':e['manifest_base64'],'wards_base64':e['wards_base64'],
                     'context_base64':base64.b64encode(context).decode()}
            cp=a/'capture-9876543210.json';cp.write_text(json.dumps(capture))
            r=merge([a],root/'one')
            self.assertEqual(r['capture_count'],1)
            self.assertEqual((root/'one/capture-9876543210.json').read_bytes(),cp.read_bytes())
            cp.write_bytes(cp.read_bytes()+b' ')
            with self.assertRaises(ValueError):merge([],root/'two',previous=root/'one/registry.json')
