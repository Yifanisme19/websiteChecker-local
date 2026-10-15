import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app.payload_preflight import assert_payload_guard

class PreflightTests(unittest.TestCase):
 def test_missing_or_replaced_plugin_fails_closed(self):
  with patch('app.payload_preflight.subprocess.run',return_value=SimpleNamespace(stdout='{"plugins":[]}')),self.assertRaisesRegex(RuntimeError,'payload_guard_unverified'):
   assert_payload_guard()
 def test_marker_missing_refuses_and_patched_source_accepts(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'dist'/'.setup';p.mkdir(parents=True)
   a=p/'inference-dispatch-B5UKEUyC.mjs';b=p/'inference-proxy-DUO5E2QQ.mjs'
   a.write_text('unpatched');b.write_text('unpatched')
   data={'plugins':[{'id':'codex','status':'loaded','rootDir':d}]}
   with patch('app.payload_preflight.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(data))):
    with self.assertRaises(RuntimeError):assert_payload_guard()
    a.write_text('website-checker-payload-guard-v1');b.write_text('const oversized = payloadFailure(error)');assert_payload_guard()
