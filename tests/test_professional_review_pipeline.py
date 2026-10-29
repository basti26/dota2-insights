import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from dota2_insights.professional_review_pipeline import Pipeline, TOOLS, lease, process_identity, WaitingForStage


class ProfessionalReviewPipelineTests(unittest.TestCase):
    def setup_run(self, base, runner=None):
        root=Path(base)/'project';(root/'tools').mkdir(parents=True);(root/'data').mkdir()
        (root/'data/validation-locks.json').write_text('{}')
        for name in TOOLS:(root/'tools'/name).write_text('# generated offline test tool\n')
        batch=root/'batch';batch.mkdir();(batch/'snapshot.json').write_text(json.dumps({'matches':[{'match_id':'9876543210'}]}))
        (batch/'receipt.json').write_text('{}');mp=root/'map.jpg';mp.write_bytes(b'generated-map')
        config={'batches':[str(batch)],'previous':None,'map':str(mp),'collect_pi':False,'limit':8,
                'selection':{},'reconcile_pauses':False,'retain_missing_event_times':False}
        p=Pipeline(root/'run',config,root=root,runner=runner)
        return root,p

    @staticmethod
    def good(argv, output, attempt):
        output.mkdir();(output/'receipt.json').write_text('{"status":"complete"}')
        (output/'payload.txt').write_text('generated test output')
        return 0

    def stage(self,p):
        return p.stage('test',TOOLS[0],[],'receipt.json')

    def test_completed_stage_reused_without_running_again(self):
        with TemporaryDirectory() as temp:
            calls=[]
            def run(*args):calls.append(1);return self.good(*args)
            root,p=self.setup_run(temp,run);first=self.stage(p)
            p2=Pipeline(root/'run',None,resume=True,root=root,runner=run)
            self.assertEqual(self.stage(p2),first)
            self.assertEqual(len(calls),1)

    def test_failed_attempt_preserved_and_resume_uses_new_directory(self):
        with TemporaryDirectory() as temp:
            def fail(argv,output,attempt):
                output.mkdir();(output/'partial.txt').write_text('partial');return 1
            root,p=self.setup_run(temp,fail)
            with self.assertRaises(ValueError):self.stage(p)
            partial=root/'run/stages/test/attempt-0001/partial.txt'
            p2=Pipeline(root/'run',None,resume=True,root=root,runner=self.good)
            self.assertEqual(self.stage(p2).name,'attempt-0002')
            self.assertEqual(partial.read_text(),'partial')
            self.assertEqual(len(p2.state['stages']['test']['attempts']),2)

    def test_zero_exit_without_receipt_is_not_complete(self):
        with TemporaryDirectory() as temp:
            def incomplete(argv,output,attempt):output.mkdir();return 0
            _,p=self.setup_run(temp,incomplete)
            with self.assertRaises(FileNotFoundError):self.stage(p)
            self.assertEqual(p.state['stages']['test']['attempts'][0]['status'],'failed')

    def test_changed_completed_artifact_refused_instead_of_recomputed(self):
        with TemporaryDirectory() as temp:
            root,p=self.setup_run(temp,self.good);folder=self.stage(p)
            (folder/'payload.txt').write_text('changed')
            p2=Pipeline(root/'run',None,resume=True,root=root,runner=self.good)
            with self.assertRaises(ValueError):self.stage(p2)
            self.assertEqual(len(p2.state['stages']['test']['attempts']),1)

    def test_changed_input_or_code_refused(self):
        for changed in ('input','code'):
            with TemporaryDirectory() as temp:
                root,p=self.setup_run(temp,self.good);self.stage(p)
                path=root/'batch/receipt.json' if changed=='input' else root/'tools'/TOOLS[0]
                path.write_text(path.read_text()+' ')
                p2=Pipeline(root/'run',None,resume=True,root=root,runner=self.good)
                with self.assertRaises(ValueError):self.stage(p2)

    def test_live_os_process_identity_prevents_duplicate_stage(self):
        with TemporaryDirectory() as temp:
            root,p=self.setup_run(temp,self.good)
            identity=process_identity(os.getpid());self.assertIsInstance(identity,str)
            p.state['stages']['test']={'attempts':[{'status':'running','pid':os.getpid(),
                'process_identity':identity,'output':'stages/test/attempt-0001'}]};p.save()
            p2=Pipeline(root/'run',None,resume=True,root=root,runner=lambda *_:self.fail('duplicate'))
            with self.assertRaises(WaitingForStage):self.stage(p2)
            self.assertEqual(p2.state['status'],'waiting_for_stage')

    def test_reused_pid_with_different_birth_is_not_the_original_worker(self):
        with TemporaryDirectory() as temp:
            root,p=self.setup_run(temp,self.good)
            p.state['stages']['test']={'attempts':[{'status':'running','pid':os.getpid(),
                'process_identity':'not-the-current-birth','output':'stages/test/attempt-0001'}]};p.save()
            p2=Pipeline(root/'run',None,resume=True,root=root,runner=self.good)
            self.assertEqual(self.stage(p2).name,'attempt-0002')

    def test_unconfirmed_launch_never_automatically_restarted(self):
        with TemporaryDirectory() as temp:
            root,p=self.setup_run(temp,self.good)
            p.state['stages']['test']={'attempts':[{'status':'launching','output':'stages/test/attempt-0001'}]};p.save()
            p2=Pipeline(root/'run',None,resume=True,root=root,runner=lambda *_:self.fail('duplicate'))
            with self.assertRaises(ValueError):self.stage(p2)

    def test_real_child_native_exit_and_artifact_are_recorded(self):
        with TemporaryDirectory() as temp:
            root,p=self.setup_run(temp)
            (root/'tools'/TOOLS[0]).write_text("from pathlib import Path\nimport sys\np=Path(sys.argv[-1]);p.mkdir();(p/'receipt.json').write_text('{\\\"status\\\":\\\"complete\\\"}')\n")
            # Construct after publishing the generated tool, so the source fingerprint is stable.
            p=Pipeline(root/'real-run',p.config,root=root)
            folder=self.stage(p)
            a=p.state['stages']['test']['attempts'][0]
            self.assertEqual(a['native_exit_code'],0)
            self.assertEqual(a['status'],'complete')
            self.assertTrue((folder/'receipt.json').is_file())

    def test_lease_prevents_concurrent_pipeline_writer(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)
            with lease(folder):
                with self.assertRaises(ValueError):
                    with lease(folder):self.fail('second lease acquired')

    def test_all_conflicting_matches_stop_before_downstream_stages(self):
        with TemporaryDirectory() as temp:
            calls=[]
            def run(argv,output,attempt):
                calls.append(argv[1]);output.mkdir()
                (output/'registry.json').write_text(json.dumps({'status':'complete_with_conflicts',
                    'selected_matches':0,'conflicting_matches':['9876543210']}))
                return 0
            _,p=self.setup_run(temp,run);result=p.run()
            self.assertEqual(result['status'],'needs_selection')
            self.assertEqual(len(calls),1)
            self.assertFalse((p.folder/'index.html').exists())
