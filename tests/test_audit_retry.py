import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pymysql
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import API_PREFIX, create_app
from app.database import retry_audit
from app.errors import AuditError


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None)
        self.job = str(uuid4())
        self.child = str(uuid4())
        self.client = TestClient(create_app(self.settings))
        self.url = API_PREFIX + '/audits/' + self.job + '/retry'
        self.headers = {'Authorization': 'Bearer test'}
        self.auth = patch('app.audits.authenticate_token', return_value=7)
        self.auth.start()
        self.addCleanup(self.auth.stop)

    @patch('app.database.retry_audit')
    def test_api_auth_new_and_existing(self, retry):
        self.assertEqual(self.client.post(self.url).status_code, 401)
        retry.assert_not_called()
        for created, status, state in ((True, 202, 'queued'), (False, 200, 'completed')):
            retry.return_value = ({'job_id': self.child, 'job_status': state}, created)
            response = self.client.post(self.url, headers=self.headers)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), dict(status=1, original_job_id=self.job,
                             job_id=self.child, job_status=state, created=created))
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(retry.call_args.args[1:], (self.job, 7))
        self.assertEqual(self.client.get(self.url, headers=self.headers).status_code, 405)

    @patch('app.database.retry_audit')
    def test_api_errors(self, retry):
        for error, status, code in (
            (AuditError(409, 'AUDIT_NOT_RETRYABLE'), 409, 'AUDIT_NOT_RETRYABLE'),
            (AuditError(404, 'AUDIT_NOT_FOUND'), 404, 'AUDIT_NOT_FOUND'),
            (pymysql.OperationalError('private details'), 503, 'DATABASE_UNAVAILABLE')):
            retry.side_effect = error
            response = self.client.post(self.url, headers=self.headers)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), dict(status=0, error_code=code))
        retry.reset_mock()
        self.assertEqual(self.client.post(API_PREFIX + '/audits/bad/retry', headers=self.headers).status_code, 422)
        retry.assert_not_called()

    def db(self, rows):
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.side_effect = rows
        cursor.lastrowid = 12
        mock = patch('app.database.connect')
        connect = mock.start()
        self.addCleanup(mock.stop)
        connect.return_value.__enter__.return_value = connection
        return connection, cursor

    def original(self, **overrides):
        return dict(dict(id=11, project_id='p', company_id='c', website_url='https://example.com/',
                         job_status='failed', error_code='invalid_report'), **overrides)

    def test_atomic_creation_copies_inputs_and_tracks_requester(self):
        connection, cursor = self.db([self.original(), None])
        result, created = retry_audit(self.settings, self.job, 7)
        self.assertTrue(created)
        self.assertEqual(result['job_status'], 'queued')
        calls = cursor.execute.call_args_list
        self.assertIn('FOR UPDATE', calls[0].args[0])
        self.assertEqual(calls[2].args[1][1:4], ('p', 'c', 'https://example.com/'))
        self.assertEqual(calls[2].args[1][-1], 7)
        self.assertEqual(calls[3].args[1][:2], (11, 12))
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()

    def test_duplicate_returns_child_without_insert(self):
        connection, cursor = self.db([self.original(),
            dict(job_id=self.child, job_status='running', deleted_at=None)])
        result, created = retry_audit(self.settings, self.job, 7)
        self.assertFalse(created)
        self.assertEqual(result['job_id'], self.child)
        self.assertEqual(cursor.execute.call_count, 2)

    def test_rejected_states_rollback(self):
        cases = [([None], 'AUDIT_NOT_FOUND'),
                 ([self.original(), dict(deleted_at=1)], 'RETRY_TASK_DELETED')]
        for state in ('queued', 'running', 'completed'):
            cases.append(([self.original(job_status=state), None], 'AUDIT_NOT_RETRYABLE'))
        for code in ('upstream_timeout_outcome_unknown', 'worker_lost_outcome_unknown'):
            cases.append(([self.original(error_code=code), None], 'AUDIT_OUTCOME_UNKNOWN'))
        for rows, code in cases:
            with self.subTest(code=code, rows=rows):
                connection, cursor = self.db(rows)
                with self.assertRaises(AuditError) as caught:
                    retry_audit(self.settings, self.job, 7)
                self.assertEqual(caught.exception.error_code, code)
                connection.rollback.assert_called_once()
                connection.commit.assert_not_called()

    def test_link_failure_rolls_back_child(self):
        connection, cursor = self.db([self.original(), None])
        cursor.execute.side_effect = [None, None, None, pymysql.OperationalError('failure')]
        with self.assertRaises(pymysql.OperationalError):
            retry_audit(self.settings, self.job, 7)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()


    def test_failed_retry_can_create_next_retry_and_old_request_stays_idempotent(self):
        # A -> B already exists and B has failed. Retrying B creates C;
        # replaying A still returns B, and replaying B returns C.
        next_job = str(uuid4())
        connection, cursor = self.db([
            self.original(id=12), None,
            self.original(id=11), dict(job_id=self.child, job_status='failed', deleted_at=None),
            self.original(id=12), dict(job_id=next_job, job_status='queued', deleted_at=None),
        ])
        cursor.lastrowid = 13
        with patch('app.database.uuid4', return_value=next_job):
            result, created = retry_audit(self.settings, self.child, 7)
        self.assertTrue(created)
        self.assertEqual(result['job_id'], next_job)
        self.assertEqual(cursor.execute.call_args_list[3].args[1][:2], (12, 13))
        result, created = retry_audit(self.settings, self.job, 7)
        self.assertFalse(created)
        self.assertEqual(result['job_id'], self.child)
        result, created = retry_audit(self.settings, self.child, 7)
        self.assertFalse(created)
        self.assertEqual(result['job_id'], next_job)
        inserts = [call for call in cursor.execute.call_args_list
                   if call.args[0].startswith('INSERT INTO audits ')]
        self.assertEqual(len(inserts), 1)
