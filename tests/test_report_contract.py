import copy
import json
import unittest
from html import unescape

from app.reports import (CONTRACT_VERSION, PREVIOUS_CONTRACT_VERSION, LABELS,
                         ReportError, parse_delivery, validate_report)
from app.report_html import render_report
from app.worker import _terminal_blocked


def report():
    return {'generated_at': '2026-09-29T12:00:00+08:00',
            'basic_info': {'company_website': 'https://example.com/', 'company_name': 'Example',
                           'ssm_registration_number': '', 'phone': [], 'whatsapp': [],
                           'email': [], 'address': []},
            'checklist': [{'label': label, 'status': 1, 'severity': 'low'} for label in LABELS],
            'issues_found': []}


def issue(problem='Contact Us opens a 404 page.'):
    return {'issue': '#001', 'pic': 'Designer', 'severity': 'low',
            'page': 'https://example.com/', 'problem': problem,
            'recommended_fix': 'Restore the Contact Us destination.'}


class FindingsContractTests(unittest.TestCase):
    def test_no_findings_is_pass_independent_of_coverage(self):
        for coverage in ('completed', 'partial', 'blocked', 'out_of_scope'):
            limitations = [] if coverage == 'completed' else ['Verification incomplete: fixture.']
            delivery = parse_delivery(json.dumps(dict(coverage_status=coverage,
                                                       limitations=limitations, report=report())))
            self.assertEqual(delivery.limitations, limitations)
            html = render_report(delivery.report, coverage_status=coverage, limitations=limitations)
            self.assertEqual(unescape(html).count('✅ PASS'), 8)
            self.assertNotIn('incomplete', html.lower())
            self.assertNotIn('Verification', html)

    def test_partial_with_confirmed_low_issue_does_not_raise_severity(self):
        data = report()
        data['issues_found'] = [issue()]
        data['checklist'][0].update(status=0, severity='low')
        delivery = parse_delivery(json.dumps(dict(coverage_status='partial',
                              limitations=['Some pages unavailable.'], report=data)))
        self.assertEqual(delivery.report, data)
        self.assertEqual(unescape(render_report(data)).count('❌ FAIL'), 1)

    def test_issue_outside_checklist_keeps_all_pass(self):
        data = report()
        data['issues_found'] = [issue('Homepage has no H1.')]
        self.assertEqual(validate_report(data), data)

    def test_skill_exporter_null_pass_severity_is_normalized(self):
        data = report()
        for item in data['checklist']:
            item['severity'] = None
        delivery = parse_delivery(json.dumps(dict(coverage_status='completed', limitations=[], report=data)))
        self.assertEqual({item['severity'] for item in delivery.report['checklist']}, {'low'})

    def test_fail_requires_an_issue(self):
        data = report()
        data['checklist'][0].update(status=0, severity='medium')
        with self.assertRaisesRegex(ReportError, 'report_failure_without_issue'):
            validate_report(data)

    def test_new_inspection_gap_rejected_but_historical_v2_unchanged(self):
        for phrase in ('Verification incomplete', 'Unable to verify images', 'Not tested'):
            data = report()
            data['issues_found'] = [issue(phrase)]
            data['checklist'][0].update(status=0, severity='low')
            original = copy.deepcopy(data)
            with self.assertRaisesRegex(ReportError, 'report_inspection_gap_issue'):
                validate_report(data, CONTRACT_VERSION)
            self.assertEqual(validate_report(data, PREVIOUS_CONTRACT_VERSION), original)
            self.assertIn(phrase, render_report(data, schema_version=PREVIOUS_CONTRACT_VERSION))
            self.assertEqual(data, original)

    def test_fully_blocked_cannot_claim_confirmed_findings(self):
        data = report()
        data['issues_found'] = [issue()]
        for coverage in ('blocked', 'out_of_scope'):
            with self.assertRaisesRegex(ReportError, 'report_blocked_with_findings'):
                parse_delivery(json.dumps(dict(coverage_status=coverage,
                                    limitations=['No inspection performed.'], report=data)))

    def test_worker_can_resume_blocked_checkpoint_with_findings(self):
        data = report()
        data['issues_found'] = [issue()]
        data['checklist'][0].update(status=0, severity='low')
        delivery = parse_delivery(json.dumps(dict(
            coverage_status='blocked',
            limitations=['TaskSpace interrupted after homepage and product review.'],
            report=data)), allow_blocked_findings=True)
        self.assertEqual(delivery.coverage_status, 'blocked')
        self.assertEqual(delivery.report['issues_found'][0]['issue'], '#001')

    def test_closed_taskspace_block_is_terminal(self):
        data = report()
        delivery = parse_delivery(json.dumps(dict(
            coverage_status='blocked',
            limitations=['The previously owned TaskSpace was already closed and cannot be resumed.'],
            report=data)))
        self.assertTrue(_terminal_blocked(delivery))


if __name__ == '__main__':
    unittest.main()
