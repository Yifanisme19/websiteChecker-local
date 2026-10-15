"""Deterministic HTML presentation of the validated public report."""
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

from app.reports import CONTRACT_VERSION, validate_report
from app.discussion_html import discussion_fragment

TEMPLATE_VERSION = 'inline-fragment-v2'


def text(value):
    return escape(str(value), quote=True)


def link(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return text(value)
    return f'<a href="{text(value)}" rel="noreferrer">{text(value)}</a>'


def info_table(rows, label):
    body = ''.join(f'<tr><th scope="row">{text(k)}</th><td>{v}</td></tr>' for k, v in rows)
    return f'<div class="wrap"><table class="info" aria-label="{text(label)}"><tbody>{body}</tbody></table></div>'


def render_report(report, *, schema_version=CONTRACT_VERSION, coverage_status=None, limitations=()):
    validate_report(report, schema_version)
    if coverage_status not in (None, 'completed', 'partial', 'blocked', 'out_of_scope'):
        raise ValueError('Invalid coverage status')
    if not isinstance(limitations, (list, tuple)) or any(not isinstance(x, str) for x in limitations):
        raise ValueError('Invalid limitations')
    generated = datetime.fromisoformat(report['generated_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Kuala_Lumpur'))
    month = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[generated.month - 1]
    formatted = (f'{generated.year:04d} {month} {generated.day:02d} '
                 f'{generated.hour % 12 or 12:02d}:{generated.minute:02d} '
                 f'{"AM" if generated.hour < 12 else "PM"}')
    metadata = [('Generated At', text(formatted))]
    basic = report['basic_info']
    company = [('Company Website', link(basic['company_website'])),
               ('Company Name', text(basic['company_name']) or '—'),
               ('SSM Registration Number', text(basic['ssm_registration_number']) or '—')]
    for key, label, field in [('phone', 'Phone', 'number'), ('whatsapp', 'WhatsApp', 'number'),
                              ('email', 'Email', 'email'), ('address', 'Address', 'address')]:
        values = [text((x['label'] + ': ' if x['label'] else '') + x[field]) for x in basic[key]]
        company.append((label, '<br>'.join(values) or '—'))
    rows = []
    for index, item in enumerate(report['checklist'], 1):
        css, status = {1: ('pass', '✅ PASS'), 0: ('fail', '❌ FAIL'), None: ('', 'NOT VERIFIED')}[item['status']]
        rows.append(f'<tr><td>{index}</td><td>{text(item["label"])}</td>'
                    f'<td class="status {css}">{status}</td><td>{text(item["severity"].title()) or "—"}</td></tr>')
    checklist = ('<div class="wrap"><table class="check" aria-label="Audit checklist"><thead><tr>'
                 '<th scope="col">#</th><th scope="col">Check</th><th scope="col">Status</th>'
                 '<th scope="col">Severity</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
    issues = []
    for item in report['issues_found']:
        number = text(item['issue'][1:])
        issues.append(f'<article class="issue-card" id="issue-{number}" data-issue-number="{number}">'
                      '<div class="wrap"><table class="issue"><thead><tr>'
                      f'<th><span class="issue-number">Issue {text(item["issue"])}</span></th>'
                      f'<th><span class="badge issue-owner">{text(item["pic"])}</span></th><th>Severity</th>'
                      '</tr></thead><tbody><tr><th scope="row">Page</th>'
                      f'<td>{link(item["page"])}</td><td>{text(item["severity"].title())}</td></tr>'
                      f'<tr><th scope="row">Problem</th><td colspan="2">{text(item["problem"])}</td></tr>'
                      f'<tr><th scope="row">Recommended Fix</th><td colspan="2">{text(item["recommended_fix"])}</td>'
                      '</tr></tbody></table></div></article>')
    template = Template((Path(__file__).parent / 'templates/report.html').read_text(encoding='utf-8'))
    return discussion_fragment(template.substitute(metadata=info_table(metadata, 'Report information'),
                               company=info_table(company, 'Company information'), checklist=checklist,
                               issues=''.join(issues) or '<p>No issues recorded.</p>'))
