import unittest
import json
from html import unescape
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

import httpx

from app.config import Settings
from app import discussion_notifications as notifications
from app import render_reports
from app.report_safety import DISCUSSION_MAX_BYTES


class DiscussionTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None, discussion_notifications_enabled=True,
                                 discussion_api_token='test-token')
        self.row = (7, 'company-1', 'project-2', '<p>中文 & + = <a href="https://example.com/?a=1&amp;b=2">Website</a></p>')

    def run_response(self, response):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        def handler(request):
            connection.commit.assert_called_once()
            parameters = json.loads(connection.cursor.return_value.__enter__.return_value.execute.call_args[0][1][0])
            expected = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            expected['token'] = '[REDACTED]'
            self.assertEqual(parameters, expected)
            self.assertNotIn('test-token', json.dumps(parameters))
            self.assertEqual(request.method, 'POST')
            self.assertEqual(str(request.url), notifications.URL)
            self.assertIn('application/x-www-form-urlencoded', request.headers['content-type'])
            self.assertEqual(parse_qs(request.content.decode()), {
                'action': ['post-discussion'], 'company_id': ['company-1'],
                'project_id': ['project-2'], 'system_id': ['2'],
                'email': ['websitechecker@newpages.com.my'],
                'post_message': [self.row[3].encode('ascii', 'xmlcharrefreplace').decode()], 'token': ['test-token']})
            self.assertEqual(unescape(expected['post_message']), unescape(self.row[3]))
            if isinstance(response, Exception):
                raise response
            return response
        with patch.object(notifications, 'connect', return_value=connection), \
             patch.object(notifications.httpx, 'HTTPTransport', return_value=httpx.MockTransport(handler)) as transport:
            result = notifications.post(self.settings, self.row)
            transport.assert_called_once_with(retries=0)
            return result

    def test_parameter_save_failure_prevents_post(self):
        for stage in ('execute', 'commit'):
            with self.subTest(stage=stage):
                connection = MagicMock()
                connection.__enter__.return_value = connection
                if stage == 'execute':
                    connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError('DB failed')
                else:
                    connection.commit.side_effect = RuntimeError('DB failed')
                with patch.object(notifications, 'connect', return_value=connection), \
                     patch.object(notifications.httpx, 'Client') as client:
                    with self.assertRaises(RuntimeError):
                        notifications.post(self.settings, self.row)
                    client.assert_not_called()

    def test_full_html_form_and_success(self):
        self.assertEqual(self.run_response(httpx.Response(200, json={'status': 1, 'id': 123})),
                         ('sent', '123', 200, None))

    def test_invalid_and_oversized_reports_never_open_http_or_db(self):
        cases = [('<article><table><tbody><tr><td style="border:1px soli', 'discussion_html_invalid'),
                 ('<p>Intro<h1>Report</h1></p>', 'discussion_html_invalid'),
                 ('<p>' + 'a' * DISCUSSION_MAX_BYTES + '</p>', 'discussion_html_too_large'),
                 ('<p>' + '中' * 10000 + '</p>', 'discussion_html_too_large'),
                 ('<p>\ufffd</p>', 'discussion_html_invalid')]
        for html, error in cases:
            with self.subTest(error=error, size=len(html)), \
                 patch.object(notifications.httpx, 'Client') as client, \
                 patch.object(notifications, 'connect') as connect:
                self.assertEqual(notifications.post(self.settings, self.row[:3] + (html,)),
                                 ('failed', None, None, error))
                client.assert_not_called()
                connect.assert_not_called()

    def test_exact_budget_is_sent_without_truncation(self):
        self.row = self.row[:3] + ('<p>' + 'a' * (DISCUSSION_MAX_BYTES - 7) + '</p>',)
        self.assertEqual(self.run_response(httpx.Response(200, json={'status': 1, 'id': 123}))[0], 'sent')

    def test_invalid_report_finishes_failed_without_retry(self):
        row = self.row[:3] + ('<table><tr><td',)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch.object(notifications, 'claim', return_value=row), \
             patch.object(notifications, 'connect', return_value=connection), \
             patch.object(notifications.httpx, 'Client') as client:
            self.assertTrue(notifications.dispatch_one(self.settings))
            args = connection.cursor.return_value.__enter__.return_value.execute.call_args[0][1]
            self.assertEqual(args[0], 'failed')
            self.assertEqual(args[3], 'discussion_html_invalid')
            client.assert_not_called()

    def test_timeout_unknown(self):
        self.assertEqual(self.run_response(httpx.ReadTimeout('private details')),
                         ('unknown', None, None, 'request_timeout'))

    def test_connection_error_unknown(self):
        self.assertEqual(self.run_response(httpx.ConnectError('private details')),
                         ('unknown', None, None, 'transport_error'))

    def test_business_failure(self):
        self.assertEqual(self.run_response(httpx.Response(200, json={'status': 0, 'message': 'secret'})),
                         ('failed', None, 200, 'api_rejected'))

    def test_ambiguous_responses(self):
        for response in [httpx.Response(500), httpx.Response(302, headers={'location': 'https://example.com'}),
                         httpx.Response(200, text='bad json'), httpx.Response(200, json=[]),
                         httpx.Response(200, json={'status': 1}), httpx.Response(200, json={'status': '1'})]:
            with self.subTest(response=response):
                self.assertEqual(self.run_response(response)[0], 'unknown')

    def test_disabled_or_missing_token_does_not_claim(self):
        with patch.object(notifications, 'claim') as claim:
            notifications.dispatch_one(Settings(_env_file=None, discussion_notifications_enabled=False))
            self.settings.discussion_api_token = type(self.settings.discussion_api_token)('')
            notifications.dispatch_one(self.settings)
            claim.assert_not_called()

    def test_claim_commit_failure_never_posts(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = self.row
        connection.commit.side_effect = RuntimeError('commit acknowledgement lost')
        with patch.object(notifications, 'connect', return_value=connection), patch.object(notifications, 'post') as post:
            self.assertFalse(notifications.dispatch_safely(self.settings))
            post.assert_not_called()

    def test_claim_commits_unknown_before_return(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = self.row + ('completed', 'job', 'https://example.com/', None)
        with patch.object(notifications, 'connect', return_value=connection):
            self.assertEqual(notifications.claim(self.settings), self.row)
        self.assertIn("status='unknown'", cursor.execute.call_args[0][0])
        connection.commit.assert_called_once()

    def test_result_write_failure_does_not_retry(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.commit.side_effect = RuntimeError('lost DB')
        with patch.object(notifications, 'claim', return_value=self.row), \
             patch.object(notifications, 'post', return_value=('sent', '1', 200, None)) as post, \
             patch.object(notifications, 'connect', return_value=connection):
            self.assertFalse(notifications.dispatch_safely(self.settings))
            post.assert_called_once()

    def test_html_and_enqueue_share_transaction_and_rerender_does_not_enqueue(self):
        audit = {'job_status': 'completed', 'report_id': 9, 'result_json': '{}',
                 'report_json': '{}', 'schema_version': 'test'}
        for old_html in (None, '<html>previous</html>'):
            with self.subTest(old_html=old_html):
                connection = MagicMock()
                connection.__enter__.return_value = connection
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchone.return_value = (12, old_html)
                def enqueue(q, audit_id):
                    connection.commit.assert_not_called()
                    self.assertIs(q, cursor)
                    self.assertEqual(audit_id, 12)
                with patch.object(render_reports, 'get_audit', return_value=audit), \
                     patch.object(render_reports, 'render_report', return_value='<html>new</html>'), \
                     patch.object(render_reports, 'connect', return_value=connection), \
                     patch.object(render_reports, 'enqueue', side_effect=enqueue) as queued:
                    render_reports.generate_html(self.settings, 'job')
                    self.assertEqual(queued.call_count, 1 if old_html is None else 0)
                    connection.commit.assert_called_once()

    def test_enqueue_failure_does_not_commit_html(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (12, None)
        audit = {'job_status': 'completed', 'report_id': 9, 'result_json': '{}',
                 'report_json': '{}', 'schema_version': 'test'}
        with patch.object(render_reports, 'get_audit', return_value=audit), \
             patch.object(render_reports, 'render_report', return_value='<html>new</html>'), \
             patch.object(render_reports, 'connect', return_value=connection), \
             patch.object(render_reports, 'enqueue', side_effect=RuntimeError('DB failure')):
            with self.assertRaises(RuntimeError):
                render_reports.generate_html(self.settings, 'job')
            connection.commit.assert_not_called()
