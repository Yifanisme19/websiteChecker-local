"""Pinned web-checker public contract plus the worker-only delivery envelope."""
import json
import re
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

LEGACY_CONTRACT_VERSION = 'web-checker-2026-09-19'
PREVIOUS_CONTRACT_VERSION = 'web-checker-binary-v2'
CONTRACT_VERSION = 'web-checker-findings-v3'
PROMPT_VERSION = 'web-checker-v3'
MAX_REPORT_BYTES = 2 * 1024 * 1024
SCHEMA = json.loads((Path(__file__).resolve().parent.parent / 'contracts/web-checker-report.schema.json').read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
LABELS = SCHEMA['properties']['checklist']['items']['properties']['label']['enum']
SEVERITIES = ['high', 'medium', 'low']
INSPECTION_GAP = re.compile(
    r'\b(?:verification[ -](?:incomplete|gap|limitation)|incomplete verification|'
    r'(?:unable to|cannot|could not) verify|not tested)\b', re.IGNORECASE)
LEGACY_SCHEMA = json.loads((Path(__file__).resolve().parent.parent / 'contracts/web-checker-report-v1.schema.json').read_text())
LEGACY_VALIDATOR = Draft202012Validator(LEGACY_SCHEMA, format_checker=FormatChecker())


class ReportError(ValueError):
    pass


class AuditDelivery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    coverage_status: Literal['completed', 'partial', 'blocked', 'out_of_scope']
    limitations: list[str] = Field(max_length=100)
    report: dict | None


def validate_report(report: dict, schema_version: str = CONTRACT_VERSION) -> dict:
    if schema_version not in (CONTRACT_VERSION, PREVIOUS_CONTRACT_VERSION, LEGACY_CONTRACT_VERSION):
        raise ReportError('report_schema_version_unknown')
    legacy = schema_version == LEGACY_CONTRACT_VERSION
    try:
        (LEGACY_VALIDATOR if legacy else VALIDATOR).validate(report)
    except SchemaValidationError:
        raise ReportError('report_schema_invalid') from None
    if [item['label'] for item in report['checklist']] != LABELS:
        raise ReportError('report_checklist_order_invalid')
    # JSON Schema permits 1.0 for integer; the public contract requires actual integers.
    for item in report['checklist']:
        if item['status'] is not None and type(item['status']) is not int:
            raise ReportError('report_status_invalid')
        if item['status'] == 0:
            if not item['severity'] or not report['issues_found']:
                raise ReportError('report_failure_without_issue')
        elif item['severity'] != ('' if legacy else 'low'):
            raise ReportError('report_severity_invalid')
    issues = report['issues_found']
    if schema_version == CONTRACT_VERSION and any(
        INSPECTION_GAP.search(i['problem'] + '\n' + i['recommended_fix']) for i in issues
    ):
        raise ReportError('report_inspection_gap_issue')
    if [i['issue'] for i in issues] != [f'#{i:03d}' for i in range(1, len(issues) + 1)]:
        raise ReportError('report_issue_number_invalid')
    severities = ['critical', 'high', 'medium', 'low'] if legacy else SEVERITIES
    ranks = [severities.index(i['severity']) for i in issues]
    if ranks != sorted(ranks):
        raise ReportError('report_issue_order_invalid')
    return report


def normalize_report(report: dict) -> dict:
    """Normalize the skill exporter’s PASS severity to the worker contract.

    The web-checker skill schema permits an empty severity for a passing row,
    while the API v3 contract uses ``low`` for every public checklist row.
    Accept the former at this boundary and persist the latter; never weaken
    failed-row severity or any issue data.
    """
    normalized = json.loads(json.dumps(report, ensure_ascii=False))
    for item in normalized.get('checklist', []):
        if item.get('status') == 1 and item.get('severity') in (None, ''):
            item['severity'] = 'low'
    return normalized


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReportError('report_duplicate_key')
        result[key] = value
    return result


def parse_delivery(text: str, *, allow_blocked_findings: bool = False) -> AuditDelivery:
    if len(text.encode('utf-8')) > MAX_REPORT_BYTES:
        raise ReportError('report_too_large')
    try:
        data = json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ReportError('report_invalid_json')))
        delivery = AuditDelivery.model_validate(data)
    except (json.JSONDecodeError, ValidationError, RecursionError):
        raise ReportError('report_invalid_json') from None
    if any(not item.strip() for item in delivery.limitations):
        raise ReportError('report_limitations_invalid')
    if delivery.coverage_status == 'completed' and delivery.limitations:
        raise ReportError('report_coverage_invalid')
    if delivery.coverage_status != 'completed' and not delivery.limitations:
        raise ReportError('report_limitations_required')
    # Customer checklist results describe confirmed findings; coverage stays in the envelope.
    if delivery.report is None:
        raise ReportError('report_missing')
    delivery.report = normalize_report(delivery.report)
    validate_report(delivery.report)
    if not allow_blocked_findings and delivery.coverage_status in ('blocked', 'out_of_scope') and (
        delivery.report['issues_found'] or any(item['status'] != 1 for item in delivery.report['checklist'])
    ):
        raise ReportError('report_blocked_with_findings')
    return delivery
