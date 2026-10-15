import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pymysql
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import API_PREFIX, create_app
from app.tokens import authenticate_token, create_token, revoke_token, token_hash
from app.database import insert_audit


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None, api_token='old-env-secret')
        self.client = TestClient(create_app(self.settings), raise_server_exceptions=False)
        self.path = API_PREFIX + '/audits'
        self.headers = {'Authorization': 'Bearer old-env-secret'}

    @patch('app.audits.authenticate_token', return_value=None)
    def test_legacy_config_does_not_bypass_revocation(self, auth):
        for suffix in ('', '/' + str(uuid4()), '/' + str(uuid4()) + '/report', '/' + str(uuid4()) + '/report/html'):
            response = self.client.get(self.path + suffix, headers=self.headers)
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.post(self.path, headers=self.headers, json={}).status_code, 401)

    @patch('app.audits.authenticate_token', side_effect=pymysql.OperationalError('secret'))
    def test_database_failure_is_closed_and_sanitized(self, auth):
        response = self.client.get(self.path, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 0, 'error_code': 'DATABASE_UNAVAILABLE'})

    @patch('app.audits.insert_audit', return_value=str(uuid4()))
    @patch('app.audits.authenticate_token', return_value=27)
    def test_creation_records_identity_from_auth(self, auth, insert):
        payload = {'project_id': 'p', 'company_id': 'c', 'website_url': 'https://example.com'}
        response = self.client.post(self.path, headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(insert.call_args.kwargs['api_token_id'], 27)
        response = self.client.post(self.path, headers=self.headers, json={**payload, 'api_token_id': 9})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(insert.call_count, 1)

    @patch('app.audits.list_audits', return_value=([], 0))
    @patch('app.audits.authenticate_token', side_effect=[1, 2])
    def test_tokens_share_listing_and_have_no_admin_endpoint(self, auth, listing):
        for token in ('one', 'two'):
            self.assertEqual(self.client.get(self.path, headers={'Authorization': 'Bearer ' + token}).status_code, 200)
        self.assertEqual(listing.call_args_list[0], listing.call_args_list[1])
        self.assertEqual(self.client.post(API_PREFIX + '/tokens', headers=self.headers, json={}).status_code, 404)

    @patch('app.tokens.connect')
    def test_generated_secret_not_stored_and_no_expiry(self, connect):
        cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.lastrowid = 7
        token_id, value = create_token(self.settings, 'internal')
        sql, params = cursor.execute.call_args.args
        self.assertEqual(token_id, 7)
        self.assertGreaterEqual(len(value), 43)
        self.assertNotIn(value, params)
        self.assertEqual(params[2], token_hash(value))
        self.assertNotIn('expires', sql)

    @patch('app.tokens.connect')
    def test_revoked_lookup_and_last_used(self, connect):
        cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        self.assertIsNone(authenticate_token(self.settings, 'revoked'))
        self.assertIn('revoked_at IS NULL', cursor.execute.call_args.args[0])
        cursor.reset_mock()
        cursor.fetchone.return_value = (4,)
        self.assertEqual(authenticate_token(self.settings, 'valid'), 4)
        self.assertIn('last_used_at', cursor.execute.call_args.args[0])
        cursor.reset_mock()
        self.assertIsNone(authenticate_token(self.settings, 'a' * 4097))
        cursor.execute.assert_not_called()

    @patch('app.tokens.connect')
    def test_revoke_does_not_delete_and_missing_id_errors(self, connect):
        cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        with self.assertRaises(ValueError):
            revoke_token(self.settings, 99)
        self.assertIn('revoked_at IS NULL', cursor.execute.call_args_list[0].args[0])
        self.assertNotIn('DELETE', cursor.execute.call_args_list[0].args[0])

    @patch('app.database.connect')
    def test_database_persists_token_attribution(self, connect):
        cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        insert_audit(self.settings, 'p', 'c', 'https://example.com', api_token_id=27)
        sql, params = cursor.execute.call_args.args
        self.assertIn('api_token_id', sql)
        self.assertEqual(params[-1], 27)


if __name__ == '__main__':
    unittest.main()
