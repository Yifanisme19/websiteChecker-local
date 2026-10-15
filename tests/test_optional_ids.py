import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app, API_PREFIX
from app.discussion_notifications import post

class OptionalIdsTests(unittest.TestCase):
    @patch('app.audits.authenticate_token', return_value=1)
    @patch('app.audits.insert_audit', return_value=str(uuid4()))
    def test_optional_and_partial_ids(self, insert, auth):
        client = TestClient(create_app(Settings(_env_file=None)))
        for ids, expected in [({}, ('', '')), ({'company_id':'c'}, ('', 'c')),
            ({'project_id':'p'}, ('p', '')), ({'company_id':None,'project_id':'  '}, ('','')),
            ({'company_id':' c ','project_id':' p '}, ('p','c'))]:
            response = client.post(API_PREFIX+'/audits', headers={'Authorization':'Bearer test'},
                                   json={'website_url':'https://example.com', **ids})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(insert.call_args.args[1:3], expected)
        for value in (123, [], 'x'*256):
            response = client.post(API_PREFIX+'/audits', headers={'Authorization':'Bearer test'},
                json={'website_url':'https://example.com', 'company_id':value})
            self.assertEqual(response.status_code, 422)

    @patch('app.discussion_notifications.httpx.Client')
    def test_missing_either_id_never_posts(self, client):
        for company, project in [('', ''), ('c',''), ('','p'), (None,'p'), ('c','  ')]:
            result = post(Settings(_env_file=None), (1, company, project, '<html/>'))
            self.assertEqual(result[3], 'missing_newpages_identifiers')
        client.assert_not_called()
