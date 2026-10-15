import copy
import json
import unittest
from html import unescape
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app, API_PREFIX
from app.report_html import render_report
from app.reports import LABELS, LEGACY_CONTRACT_VERSION
from app import queue


def report():
    return {'generated_at': '2026-09-19T12:00:00+08:00',
            'basic_info': {'company_website': 'https://example.com/', 'company_name': 'Example',
                           'ssm_registration_number': '', 'phone': [{'label': 'Office', 'number': '123'}],
                           'whatsapp': [], 'email': [], 'address': []},
            'checklist': [{'label': x, 'status': 1, 'severity': 'low'} for x in LABELS], 'issues_found': []}


class RenderingTests(unittest.TestCase):
    def test_fields_escape_and_no_sample_data(self):
        data = report()
        data['basic_info']['company_name'] = '<script>alert(1)</script>'
        data['issues_found'] = [{'issue': '#001', 'pic': 'Designer', 'severity': 'high',
                                 'page': 'https://example.com/?x=1&y=2', 'problem': '<img src=x onerror=alert(1)>',
                                 'recommended_fix': 'A & B'}]
        original = copy.deepcopy(data)
        html = render_report(data, coverage_status='partial', limitations=['<blocked>'])
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertIn('Office: 123', html)
        self.assertNotIn('&lt;blocked&gt;', html)
        self.assertNotIn('Verification Limitations', html)
        self.assertNotIn('Coverage', html)
        self.assertIn('2026 Sep 19 12:00 PM', html)
        self.assertIn('A &amp; B', html)
        self.assertEqual(unescape(html).count('✅ PASS'), 8)
        self.assertNotIn('yifan.lee', html)
        self.assertNotIn('kueensingauto', html)
        self.assertEqual(data, original)

    def test_legacy_unknown_and_empty(self):
        data = report()
        for item in data['checklist']:
            item.update(status=None, severity='')
        html = render_report(data, schema_version=LEGACY_CONTRACT_VERSION)
        self.assertEqual(html.count('NOT VERIFIED'), 8)
        self.assertIn('No issues recorded', html)

    def test_unsafe_url_rejected(self):
        data = report()
        data['basic_info']['company_website'] = 'javascript:alert(1)'
        with self.assertRaises(ValueError):
            render_report(data)

    def test_json_committed_before_render_failure(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (12,)
        def failure(*args):
            connection.commit.assert_called_once()
            raise RuntimeError('renderer unavailable')
        with patch('app.queue.connect') as connect, patch('app.queue.generate_html', side_effect=failure), self.assertLogs('app.queue', level='ERROR'):
            connect.return_value.__enter__.return_value = connection
            self.assertEqual(queue.finish(None, 'job', 'worker', result={'text': json.dumps({
                'coverage_status': 'completed', 'limitations': [], 'report': report()})}), 1)
        connection.rollback.assert_not_called()

    @patch('app.audits.authenticate_token', return_value=1)
    def test_html_endpoint_auth_states_headers(self, auth):
        client = TestClient(create_app(Settings(api_token='test-token')))
        url = API_PREFIX + f'/audits/{uuid4()}/report/html'
        headers = {'Authorization': 'Bearer test-token'}
        self.assertEqual(client.get(url).status_code, 401)
        with patch('app.audits.get_audit', return_value={'job_status': 'completed', 'report_id': 1}), patch('app.audits.get_report_html', return_value='<html>OK</html>'):
            response = client.get(url, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.headers['content-type'])
            self.assertIn("default-src 'none'", response.headers['content-security-policy'])
        with patch('app.audits.get_audit', return_value={'job_status': 'completed', 'report_id': 1}), patch('app.audits.get_report_html', return_value=None):
            self.assertEqual(client.get(url, headers=headers).status_code, 409)
        with patch('app.audits.get_audit', return_value={'job_status': 'running', 'report_id': None}):
            self.assertEqual(client.get(url, headers=headers).status_code, 409)
        with patch('app.audits.get_audit', return_value=None):
            self.assertEqual(client.get(url, headers=headers).status_code, 404)


if __name__ == '__main__':
    unittest.main()
