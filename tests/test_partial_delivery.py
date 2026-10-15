import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import queue, worker
from app.render_reports import generate_html
from app.reports import CONTRACT_VERSION
from test_report_html import report


def partial_result():
    return {'response_id': 'response-first', 'text': json.dumps({
        'coverage_status': 'partial', 'limitations': ['External store requires login.'],
        'report': report()})}


class PartialDeliveryTests(unittest.TestCase):
    def test_partial_is_delivered_with_one_upstream_request(self):
        result = partial_result()
        with patch('app.worker.queue.claim', return_value={'job_id': 'job'}), \
             patch('app.worker.send_prompt', return_value=result) as send, \
             patch('app.worker.queue.finish', return_value=1) as finish:
            self.assertTrue(worker.process_one(SimpleNamespace(worker_heartbeat_seconds=60), 'worker'))
        send.assert_called_once()
        finish.assert_called_once()
        self.assertNotIn('error', finish.call_args.kwargs)
        self.assertEqual(finish.call_args.kwargs['result']['coverage_status'], 'partial')

    def test_partial_commits_report_before_rendering(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (12,)
        with patch('app.queue.connect') as connect, patch('app.queue.generate_html') as render:
            connect.return_value.__enter__.return_value = connection
            render.side_effect = lambda *args: connection.commit.assert_called_once()
            self.assertEqual(queue.finish(SimpleNamespace(), 'job', 'worker', result=partial_result()), 1)
        render.assert_called_once()
        insert = [call for call in cursor.execute.call_args_list if call.args[0].startswith('INSERT INTO audit_reports')]
        self.assertEqual(len(insert), 1)
        update = [call for call in cursor.execute.call_args_list if call.args[0].startswith('UPDATE audits')][0]
        params = update.args[1]
        self.assertEqual(params[0], 'completed')
        self.assertIsNone(params[3])
        metadata = json.loads(params[1])
        self.assertEqual(metadata['coverage_status'], 'partial')
        self.assertEqual(metadata['limitations'], ['External store requires login.'])

    def test_partial_html_enqueues_notification_once(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (12, None)
        audit = {'job_status': 'completed', 'report_id': 1, 'schema_version': CONTRACT_VERSION,
                 'result_json': json.dumps({'coverage_status': 'partial', 'limitations': ['Login required']}),
                 'report_json': json.dumps(report())}
        with patch('app.render_reports.get_audit', return_value=audit), \
             patch('app.render_reports.connect') as connect, \
             patch('app.render_reports.enqueue') as enqueue:
            connect.return_value.__enter__.return_value = connection
            html = generate_html(SimpleNamespace(discussion_notifications_enabled=True), 'job')
            enqueue.assert_called_once_with(cursor, 12)
            cursor.fetchone.return_value = (12, html)
            generate_html(SimpleNamespace(discussion_notifications_enabled=True), 'job')
            enqueue.assert_called_once()

