import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
import pymysql
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app, API_PREFIX


class ErrorContractTests(unittest.TestCase):
    def setUp(self):
        auth = patch('app.audits.authenticate_token', return_value=1)
        auth.start()
        self.addCleanup(auth.stop)
        self.app = create_app(Settings(_env_file=None, api_token='test'))
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.headers = {'Authorization': 'Bearer test'}
        self.job = API_PREFIX + '/audits/' + str(uuid4())

    def check(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json(), {'status': 0, 'error_code': code})
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_auth_validation_and_framework_errors(self):
        for path in (API_PREFIX + '/audits', self.job, self.job + '/report', self.job + '/report/html'):
            self.check(self.client.get(path), 401, 'UNAUTHORIZED')
        self.check(self.client.post(API_PREFIX + '/audits', headers=self.headers, json={}), 422, 'VALIDATION_ERROR')
        self.check(self.client.post(API_PREFIX + '/audits', headers=self.headers, content='{',
                                    ), 422, 'VALIDATION_ERROR')
        self.check(self.client.get(API_PREFIX + '/audits/not-a-uuid', headers=self.headers), 422, 'VALIDATION_ERROR')
        self.check(self.client.get(API_PREFIX + '/audits?page_size=21', headers=self.headers), 422, 'VALIDATION_ERROR')
        self.check(self.client.get(API_PREFIX + '/missing'), 404, 'NOT_FOUND')
        response = self.client.delete(API_PREFIX + '/health')
        self.check(response, 405, 'METHOD_NOT_ALLOWED')
        self.assertIn('GET', response.headers['allow'])

    def test_missing_credentials_without_legacy_config(self):
        client = TestClient(create_app(Settings(_env_file=None, api_token='')))
        self.check(client.get(API_PREFIX + '/audits'), 401, 'UNAUTHORIZED')

    @patch('app.audits.get_audit')
    def test_business_errors_and_unexpected_failure(self, get):
        get.return_value = None
        self.check(self.client.get(self.job, headers=self.headers), 404, 'AUDIT_NOT_FOUND')
        get.return_value = {'job_status': 'queued'}
        self.check(self.client.get(self.job + '/report', headers=self.headers), 409, 'REPORT_NOT_AVAILABLE')
        get.return_value = {'job_status': 'completed', 'report_id': None}
        self.check(self.client.get(self.job + '/report', headers=self.headers), 404, 'REPORT_NOT_FOUND')
        get.side_effect = pymysql.OperationalError('private connection detail')
        self.check(self.client.get(self.job, headers=self.headers), 503, 'DATABASE_UNAVAILABLE')
        get.side_effect = RuntimeError('private exception detail')
        with self.assertLogs('app.errors', level='ERROR'):
            self.check(self.client.get(self.job, headers=self.headers), 500, 'INTERNAL_ERROR')

    def test_health_error_and_success(self):
        for payload, status in (({'ok': False}, 503), ({'ok': True}, 200)):
            app = create_app(Settings(_env_file=None), transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)))
            with TestClient(app) as client:
                response = client.get(API_PREFIX + '/health')
                if status == 503:
                    self.check(response, 503, 'OPENCLAW_UNAVAILABLE')
                else:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(set(response.json()), {'status', 'checked_at'})
                    self.assertEqual(response.json()['status'], 1)

    def test_openapi_error_schema(self):
        schema = self.app.openapi()
        for path, methods in schema['paths'].items():
            for operation in methods.values():
                for status, response in operation.get('responses', {}).items():
                    if int(status) >= 400:
                        self.assertEqual(response['content']['application/json']['schema']['$ref'],
                                         '#/components/schemas/ErrorResponse')


if __name__ == '__main__':
    unittest.main()
