import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pymysql
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import API_PREFIX, create_app
from app.database import list_audits


class PaginationTests(unittest.TestCase):
    def setUp(self):
        auth = patch('app.audits.authenticate_token', return_value=1)
        auth.start()
        self.addCleanup(auth.stop)
        self.client = TestClient(create_app(Settings(_env_file=None, api_token='test-token')))
        self.url = API_PREFIX + '/audits'
        self.headers = {'Authorization': 'Bearer test-token'}

    @patch('app.audits.list_audits')
    def test_default_and_last_page(self, listing):
        listing.return_value = ([{'job_id': str(uuid4()), 'status': 'queued'}], 21)
        response = self.client.get(self.url, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['page_size'], 20)
        self.assertEqual(response.json()['total_pages'], 2)
        self.assertEqual(listing.call_args.args[1:], (1, 20))
        response = self.client.get(self.url + '?page=2&page_size=20', headers=self.headers)
        self.assertEqual(response.json()['page'], 2)
        self.assertEqual(len(response.json()['lists']), 1)
        self.assertEqual(listing.call_args.args[1:], (2, 20))
        self.assertEqual(response.headers['cache-control'], 'no-store')

    @patch('app.audits.list_audits')
    def test_empty_and_past_end(self, listing):
        for total in (0, 21):
            listing.return_value = ([], total)
            data = self.client.get(self.url + '?page=4', headers=self.headers).json()
            self.assertEqual(data['lists'], [])
            self.assertEqual(data['total'], total)
            self.assertEqual(data['total_pages'], 0 if total == 0 else 2)

    @patch('app.audits.list_audits')
    def test_invalid_parameters_do_not_query_database(self, listing):
        for query in ('page=0', 'page=-1', 'page=2147483648', 'page=abc',
                      'page_size=0', 'page_size=21', 'page_size=1.5'):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(self.url + '?' + query, headers=self.headers).status_code, 422)
        listing.assert_not_called()

    @patch('app.audits.list_audits')
    def test_auth_and_database_error(self, listing):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        listing.assert_not_called()
        listing.side_effect = pymysql.OperationalError('unavailable')
        response = self.client.get(self.url, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 0, 'error_code': 'DATABASE_UNAVAILABLE'})

    @patch('app.database.connect')
    def test_database_limits_and_filters_both_queries(self, connect):
        cursor = MagicMock()
        connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {'total': 21}
        cursor.fetchall.return_value = [{'job_id': str(uuid4()), 'status': 'failed'}]
        rows, total = list_audits(Settings(_env_file=None), 2, 20)
        self.assertEqual(total, 21)
        self.assertEqual(len(rows), 1)
        count_call, page_call = cursor.execute.call_args_list
        self.assertIn('WHERE deleted_at IS NULL', count_call.args[0])
        self.assertIn('WHERE deleted_at IS NULL ORDER BY id DESC LIMIT %s OFFSET %s', page_call.args[0])
        self.assertEqual(page_call.args[1], (20, 20))


if __name__ == '__main__':
    unittest.main()
