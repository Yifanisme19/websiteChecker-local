import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app.visual_checkpoint import maybe_compact, CheckpointError
from app.openclaw import send_prompt, OpenClawError

JOB='00000000-0000-4000-8000-000000000001'
CP=json.dumps({'coverage_status':'checkpoint','checkpoint':{'version':1,'job_id':JOB,'space_id':7,'progress_file':'progress.json'}})

class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'progress.json').write_text(json.dumps({'job_id':JOB,'space_id':7,'completed':['home'],'remaining':['contact']}))
        self.ledger={'generation':0,'encoded_bytes':1234,'reservations':[{'id':'evidence'}]}
        (self.root/'visual-budget.json').write_text(json.dumps(self.ledger))
        self.settings=SimpleNamespace(openclaw_agent_id='website-checker-agent-local')
    def test_confirmed_native_compaction_preserves_progress_and_archives_budget(self):
        with patch('app.visual_checkpoint.audit_root',return_value=self.root),patch('app.visual_checkpoint.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{"ok":true,"compacted":true,"result":{"details":{"backend":"codex-app-server","completed":true,"pending":false}}}')) as run:
            self.assertTrue(maybe_compact(self.settings,JOB,CP))
        cmd=run.call_args.args[0];self.assertIn('agent:website-checker-agent-local:website-checker:'+JOB,cmd)
        self.assertNotIn('--max-lines',cmd)
        self.assertEqual(json.loads((self.root/'visual-budget.json').read_text())['encoded_bytes'],0)
        self.assertEqual(json.loads((self.root/'visual-budget-generation-0.json').read_text()),self.ledger)
        self.assertEqual(json.loads((self.root/'progress.json').read_text())['remaining'],['contact'])
    def test_failure_noop_timeout_do_not_clear_budget(self):
        for result in [SimpleNamespace(returncode=1,stdout='{}'),SimpleNamespace(returncode=0,stdout='{"ok":true,"compacted":false}'),SimpleNamespace(returncode=0,stdout='{"ok":true,"compacted":true}'),subprocess.TimeoutExpired('openclaw',360)]:
            with self.subTest(result=result),patch('app.visual_checkpoint.audit_root',return_value=self.root),patch('app.visual_checkpoint.subprocess.run',side_effect=result if isinstance(result,Exception) else None,return_value=result),self.assertRaises(CheckpointError):
                maybe_compact(self.settings,JOB,CP)
            self.assertEqual(json.loads((self.root/'visual-budget.json').read_text()),self.ledger)
    def test_wrong_job_and_space_are_rejected_before_command(self):
        for key,val in [('job_id','different'),('space_id',8),('progress_file','../progress.json')]:
            cp=json.loads(CP);cp['checkpoint'][key]=val
            with patch('app.visual_checkpoint.audit_root',return_value=self.root),patch('app.visual_checkpoint.subprocess.run') as run,self.assertRaises(CheckpointError):
                maybe_compact(self.settings,JOB,json.dumps(cp))
            run.assert_not_called()
    def test_checkpoint_is_not_returned_as_customer_report(self):
        final={'text':'{"coverage_status":"completed"}'}
        with patch('app.openclaw._send_with_transport_recovery',side_effect=[{'text':CP},final]) as send,patch('app.openclaw.maybe_compact',side_effect=[True,False]):
            self.assertEqual(send_prompt(self.settings,JOB,'original'),final)
        self.assertIn('SAME audit',send.call_args_list[1].args[2])
        self.assertEqual(send.call_args_list[0].args[1],send.call_args_list[1].args[1])
    def test_regular_report_does_not_compact(self):
        with patch('app.visual_checkpoint.subprocess.run') as run:
            self.assertFalse(maybe_compact(self.settings,JOB,'{"coverage_status":"completed"}'))
        run.assert_not_called()
