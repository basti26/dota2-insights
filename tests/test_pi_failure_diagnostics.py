"""Diagnostic failure metadata from fixtures; no Pi, parser or device startup."""
import json
from contextlib import nullcontext
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import Mock, patch

from tools.pi_ward_worker import failure_summary
from tools.extract_replay_wards import terminal_status


class FailureDiagnosticsTests(unittest.TestCase):
    def test_partial_parser_error_and_low32_are_preserved_not_repaired(self):
        with TemporaryDirectory() as temp:
            p=Path(temp)
            mid=str(2**33+12345)
            (p/'manifest.json').write_text(json.dumps({'match_id':mid,'parsed_match_id':'12345',
                'parse_error':'BufferReadError','stop_reason':'replay_eof','complete':False,
                'independent_footer_matches_parser':False,'last_tick':80412}))
            r=failure_summary(p,mid,2)
            self.assertEqual(r['parse_error'],'BufferReadError')
            self.assertEqual(r['last_tick'],80412)
            self.assertEqual(r['parsed_match_id'],'12345')
            self.assertTrue(r['parsed_match_id_matches_only_low32'])
            self.assertFalse(r['eligible_for_analysis'])
            self.assertFalse(r['complete'])

    def test_missing_large_invalid_and_other_identity_manifests_stay_diagnostic(self):
        with TemporaryDirectory() as temp:
            p=Path(temp);f=p/'manifest.json'
            self.assertEqual(failure_summary(p,'12345',2)['manifest_status'],'unreadable')
            for body,status in ((' '*262145,'oversized'),('{broken','unreadable'),
                                (json.dumps({'match_id':'99999'}),'identity_conflict')):
                f.write_text(body)
                r=failure_summary(p,'12345',2)
                self.assertEqual(r['manifest_status'],status)
                self.assertFalse(r['eligible_for_analysis'])

    def test_no_partial_read_while_process_is_running_and_no_arbitrary_error_text(self):
        with TemporaryDirectory() as temp:
            p=Path(temp);f=p/'manifest.json'
            f.write_text(json.dumps({'match_id':'12345','parse_error':'error with private/path',
                                     'stop_reason':'wall_time_limit','complete':0,'last_tick':False}))
            self.assertEqual(failure_summary(p,'12345',None)['manifest_status'],'not_read_process_nonterminal')
            r=failure_summary(p,'12345',-9)
            self.assertIsNone(r['parse_error'])
            self.assertIsNone(r['complete'])
            self.assertIsNone(r['last_tick'])
            self.assertEqual(r['process_exitcode'],-9)

    def test_real_EOF_is_distinct_from_swallowed_parser_error(self):
        class BufferReadError(Exception):pass
        self.assertEqual(terminal_status('replay_eof',None,None),('replay_eof',None))
        self.assertEqual(terminal_status('replay_eof',None,BufferReadError()),
                         ('parser_stream_error','BufferReadError'))
        self.assertEqual(terminal_status('wall_time_limit',None,BufferReadError()),('wall_time_limit',None))

    def test_worker_summary_includes_sanitized_packet_metadata_only(self):
        with TemporaryDirectory() as temp:
            p=Path(temp)
            packet={'schema_version':1,'kind':'inner_packet_failure_diagnostic',
                    'status':'payload_length_out_of_bounds','packet_bytes':10,'tick':12,
                    'packet_sha256':'a'*64,'payload':'do not export',
                    'invalid_header':{'message_index':2,'declared_bytes':100,'private':'do not export'}}
            (p/'manifest.json').write_text(json.dumps({'match_id':'12345','failure_packet_diagnostics':[packet]}))
            summary=failure_summary(p,'12345',2)['failure_packet_diagnostics'][0]
            self.assertEqual(summary['status'],'payload_length_out_of_bounds')
            self.assertNotIn('payload',summary)
            self.assertNotIn('private',summary['invalid_header'])
            self.assertFalse(summary['eligible_for_analysis'])

    def test_worker_keeps_failure_detail_and_never_accepts_partial_export(self):
        from tools.pi_ward_worker import process_one
        with TemporaryDirectory() as temp:
            root=Path(temp)
            row={'match_id':'12345','download_status':'verified','analysis_status':'pending','sha256':'a'*64}
            store=types.SimpleNamespace(root=root,replays=root/'replays',config={},
                catalog=lambda:{'matches':{'12345':row}},locked=lambda _:nullcontext(),
                update=Mock(),accept_analysis=Mock())
            def atomic(path,value):
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(value))
            library=types.SimpleNamespace(atomic=atomic,utc=lambda:'2026-10-29T00:00:00Z',
                                          verify=lambda *_:{'sha256':'a'*64})
            def failed(command,**kwargs):
                output=Path(command[command.index('--output')+1]);output.mkdir(parents=True)
                (output/'manifest.json').write_text(json.dumps({'match_id':'12345','parsed_match_id':'12345',
                    'complete':False,'parse_error':'BufferReadError','last_tick':12,'stop_reason':'parser_stream_error'}))
                return types.SimpleNamespace(returncode=2,poll=lambda:2)
            with patch.dict(sys.modules,{'library':library}),patch('tools.pi_ward_worker.subprocess.Popen',side_effect=failed):
                result=process_one(store)
            self.assertEqual(result['analysis_status'],'partial_or_failed')
            store.accept_analysis.assert_not_called()
            last=store.update.call_args.kwargs
            self.assertEqual(last['analysis_failure']['parse_error'],'BufferReadError')
            status=json.loads((root/'worker_status.json').read_text())
            self.assertEqual(status['analysis_failure'],last['analysis_failure'])
            self.assertFalse(status['analysis_failure']['eligible_for_analysis'])


if __name__=='__main__':unittest.main()
