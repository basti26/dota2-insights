import base64
from contextlib import redirect_stdout
import copy
import io
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_tier1_postgame_store import entry
from dota2_insights.pi_analysis_snapshot import collect


class LocalPi:
    """Run the exact remote read-only script against a private generated archive."""
    def __init__(self, root, entries):
        self.config={'remote_data':str(root/'pi')}
        self.root=Path(self.config['remote_data']);self.root.mkdir()
        self.rows={e['match_id']:copy.deepcopy(e['catalog']) for e in entries}
        for e in entries:
            folder=self.root/'analysis'/e['match_id'];folder.mkdir(parents=True)
            (folder/'manifest.json').write_bytes(base64.b64decode(e['manifest_base64']))
            (folder/'wards.json').write_bytes(base64.b64decode(e['wards_base64']))
        (self.root/'config.json').write_text('{}')
        self.persist();self.requests=0;self.catalog_calls=0

    def persist(self):
        (self.root/'catalog.json').write_text(json.dumps({'matches':self.rows}))

    def catalog(self):
        self.catalog_calls+=1
        return {'matches':copy.deepcopy(self.rows)}

    def execute(self, command, *, source, timeout):
        self.requests+=1
        out=io.StringIO()
        with redirect_stdout(out):exec(compile(source.read_text(encoding='utf-8'), str(source), 'exec'), {})
        return json.loads(out.getvalue())


class PiAnalysisSnapshotTests(unittest.TestCase):
    def test_complete_batches_can_be_paginated_without_repeating_newest(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);bridge=LocalPi(root,[entry(),entry('9876543211')])
            first=collect(bridge,root/'first',limit=1)
            self.assertEqual(first['matches'][0]['match_id'],'9876543211')
            self.assertEqual(first['next_before_match'],'9876543211')
            second=collect(bridge,root/'second',limit=1,before_match=first['next_before_match'])
            self.assertEqual(second['matches'][0]['match_id'],'9876543210')
            self.assertIsNone(second['next_before_match'])
    def test_real_remote_script_collects_only_completed_without_dem(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); bridge=LocalPi(root,[entry(),entry('9876543211')])
            bridge.rows['9876543211']['analysis_status']='analyzing';bridge.persist()
            before={str(p.relative_to(bridge.root)):p.read_bytes() for p in bridge.root.rglob('*') if p.is_file()}
            result=collect(bridge,root/'collected')
            self.assertEqual(bridge.requests,1)
            self.assertEqual(len(result['excluded_incomplete']),1)
            self.assertFalse(result['raw_replays_transferred'])
            self.assertFalse((root/'collected/fetch-private.py').exists())
            self.assertEqual(before,{str(p.relative_to(bridge.root)):p.read_bytes() for p in bridge.root.rglob('*') if p.is_file()})
            snapshot=json.loads((root/'collected/snapshot.json').read_text())
            self.assertEqual(snapshot['matches'][0]['catalog']['sources'],['tier1'])

    def test_changed_remote_body_refused_without_complete_receipt(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);bridge=LocalPi(root,[entry()])
            (bridge.root/'analysis/9876543210/wards.json').write_bytes(b'[]')
            with self.assertRaises(ValueError):collect(bridge,root/'collected')
            self.assertFalse((root/'collected/receipt.json').exists())

    def test_changed_catalog_during_collection_refused(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);bridge=LocalPi(root,[entry()]);original=bridge.execute
            def execute(*args,**kwargs):
                result=original(*args,**kwargs);bridge.rows['9876543210']['sampling']['league_id']=999
                return result
            bridge.execute=execute
            with self.assertRaises(ValueError):collect(bridge,root/'collected')
            self.assertFalse((root/'collected/receipt.json').exists())

    def test_holdout_refusal_precedes_remote_fetch(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);bridge=LocalPi(root,[entry()])
            with patch('dota2_insights.pi_analysis_snapshot.refuse_locked_match',side_effect=ValueError('locked')):
                with self.assertRaises(ValueError):collect(bridge,root/'collected')
            self.assertEqual(bridge.requests,0)

    def test_context_is_collected_with_its_original_manifest_digest(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);bridge=LocalPi(root,[entry()])
            folder=bridge.root/'analysis/9876543210'
            # Collection certifies bytes only; semantic admission belongs to load_capture.
            raw=json.dumps({'match_id':'9876543210','complete':True}).encode()
            m=json.loads((folder/'manifest.json').read_bytes())
            m['postgame_context']={'complete':True}
            m['files']['postgame_context.json']={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
            (folder/'manifest.json').write_text(json.dumps(m))
            (folder/'postgame_context.json').write_bytes(raw)
            r=collect(bridge,root/'collected')
            self.assertTrue(r['matches'][0]['context_collected'])
            captured=json.loads((root/'collected/capture-9876543210.json').read_text())
            self.assertEqual(base64.b64decode(captured['context_base64']),raw)
