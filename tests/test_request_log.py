import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import API_PREFIX, create_app
from app.request_log import RequestLogWriter


class RequestLoggingTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.client = TestClient(create_app(Settings(_env_file=None, app_env='local'), request_log_writer=self.records.append), raise_server_exceptions=False)

    @patch('app.audits.list_audits', return_value=([], 0))
    @patch('app.audits.authenticate_token', return_value=7)
    def test_success_attribution_and_secret_exclusion(self, auth, listing):
        response = self.client.get(API_PREFIX + '/audits?secret=hidden-query', headers={'Authorization': 'Bearer hidden-secret'})
        self.assertEqual(response.status_code, 200)
        record = self.records[-1]
        self.assertEqual((record['token_id'], record['environment'], record['status']), (7, 'local', 200))
        self.assertNotIn('hidden', json.dumps(record))
        self.assertGreaterEqual(record['duration_ms'], 0)

    def test_denied_and_unmatched_do_not_persist_raw_path(self):
        self.assertEqual(self.client.get(API_PREFIX + '/audits').status_code, 401)
        self.assertIsNone(self.records[-1]['token_id'])
        self.client.get('/private-token-secret')
        self.assertEqual(self.records[-1]['path'], '(unmatched)')
        self.assertNotIn('private-token', json.dumps(self.records))

    @patch('app.audits.authenticate_token', return_value=9)
    def test_validation_failure_attributed_and_body_not_stored(self, auth):
        self.assertEqual(self.client.post(API_PREFIX + '/audits', json={'secret': 'hidden-body'}, headers={'Authorization': 'Bearer hidden'}).status_code, 422)
        self.assertEqual(self.records[-1]['token_id'], 9)
        self.assertNotIn('hidden-body', json.dumps(self.records))

    def test_writer_failure_does_not_fail_requests(self):
        def fail(record): raise OSError('do-not-log-this-secret')
        client = TestClient(create_app(Settings(_env_file=None), request_log_writer=fail), raise_server_exceptions=False)
        with self.assertLogs('app.request_log', level='ERROR') as logs:
            self.assertEqual(client.get('/missing').status_code, 404)
        self.assertNotIn('do-not-log', str(logs.output))

    def test_rotation_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requests.jsonl'
            writer = RequestLogWriter(path)
            writer({'status': 200})
            writer.handler.maxBytes = 40
            for _ in range(20): writer({'status': 200})
            writer.handler.close()
            self.assertLessEqual(len(list(Path(directory).iterdir())), 5)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])['status'], 200)
