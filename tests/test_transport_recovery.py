import json
import unittest
from unittest.mock import patch

import httpx
from app.config import Settings
from app.openclaw import send_prompt, OpenClawError


class TransportRecoveryTests(unittest.TestCase):
    def run_responses(self, responses):
        requests = []
        def handle(request):
            requests.append(request)
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        real_client = httpx.Client
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handle), **kwargs)
        return requests, patch('app.openclaw.httpx.Client', side_effect=client)

    def failure(self, message='Codex parent-local inference transport failed'):
        return httpx.Response(502, json={'object': 'response', 'id': 'resp-failed',
            'status': 'failed', 'error': {'code': 'api_error', 'message': message}})

    def success(self):
        return httpx.Response(200, json={'id': 'resp-ok', 'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'REPORT'}]}]})

    def settings(self):
        return Settings(_env_file=None, openclaw_token='test', payload_guard_required=False)

    def test_confirmed_failure_recovers_in_same_session_and_new_client(self):
        requests, clients = self.run_responses([self.failure(), self.success()])
        with clients as factory, patch('app.openclaw.time.sleep') as sleep:
            result = send_prompt(self.settings(), 'job-1', 'Audit this site')
        self.assertEqual(result['text'], 'REPORT')
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(requests[0].headers['x-openclaw-session-key'], requests[1].headers['x-openclaw-session-key'])
        self.assertIn('Never replace a closed', json.loads(requests[1].content)['input'])
        sleep.assert_called_once_with(5)

    def test_gateway_normalized_provider_error_recovers(self):
        # OpenClaw resolveOpenAiCompatError maps server_error to this exact
        # message, then createFailedResponse wraps it as a terminal response.
        requests, clients = self.run_responses([
            self.failure('upstream provider error'), self.success()])
        with clients, patch('app.openclaw.time.sleep'):
            self.assertEqual(send_prompt(self.settings(), 'job', 'audit')['text'], 'REPORT')
        self.assertEqual(len(requests), 2)

    def test_recovery_has_finite_budget(self):
        requests, clients = self.run_responses([self.failure() for _ in range(3)])
        with clients, patch('app.openclaw.time.sleep'), self.assertRaisesRegex(OpenClawError, 'upstream_http_502'):
            send_prompt(self.settings(), 'job', 'audit')
        self.assertEqual(len(requests), 3)

    def test_ambiguous_or_non_transport_failures_never_replay(self):
        failures = [httpx.Response(502, text='proxy error'), self.failure('invalid tool configuration'),
                    self.failure('authentication token invalidated'), httpx.Response(401),
                    httpx.ReadTimeout('timeout'), httpx.ConnectError('disconnect'),
                    httpx.Response(502, json={'status': 'failed'})]
        for failure in failures:
            with self.subTest(failure=str(failure)):
                requests, clients = self.run_responses([failure])
                with clients, patch('app.openclaw.time.sleep') as sleep, self.assertRaises(OpenClawError):
                    send_prompt(self.settings(), 'job', 'audit')
                self.assertEqual(len(requests), 1)
                sleep.assert_not_called()

    def test_payload_errors_never_retry(self):
        for failure in [httpx.Response(413, json={'error': {'code': 'payload_too_large'}}),
                        self.failure('Codex request body too large: save progress and compact context')]:
            requests, clients = self.run_responses([failure])
            with clients, patch('app.openclaw.time.sleep') as sleep, self.assertRaisesRegex(OpenClawError, 'upstream_payload_too_large'):
                send_prompt(self.settings(), 'job', 'audit')
            self.assertEqual(len(requests), 1)
            sleep.assert_not_called()

    def test_success_does_not_retry(self):
        requests, clients = self.run_responses([self.success()])
        with clients:
            self.assertEqual(send_prompt(self.settings(), 'job', 'audit')['text'], 'REPORT')
        self.assertEqual(len(requests), 1)
