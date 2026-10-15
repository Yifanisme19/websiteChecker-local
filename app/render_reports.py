"""Render persisted JSON without calling OpenClaw: python -m app.render_reports."""
import argparse
import json
from time import time
from uuid import UUID

from app.config import Settings
from app.database import connect, get_audit
from app.discussion_notifications import enqueue
from app.report_html import TEMPLATE_VERSION, render_report


def generate_html(settings, job_id):
    audit = get_audit(settings, job_id, include_report=True)
    if not audit or audit['job_status'] != 'completed' or audit['report_id'] is None:
        raise ValueError('Completed report not found')
    metadata = json.loads(audit['result_json'] or '{}')
    html = render_report(json.loads(audit['report_json']), schema_version=audit['schema_version'],
                         coverage_status=metadata.get('coverage_status'), limitations=metadata.get('limitations', []))
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT r.audit_id, r.report_html FROM audit_reports r JOIN audits a ON a.id=r.audit_id '
                "WHERE r.id=%s AND r.deleted_at IS NULL AND a.deleted_at IS NULL AND a.job_status='completed' FOR UPDATE",
                (audit['report_id'],))
            stored = cursor.fetchone()
            if stored is None:
                raise ValueError('Completed report no longer available')
            cursor.execute('UPDATE audit_reports r JOIN audits a ON a.id=r.audit_id '
                           'SET r.report_html=%s, r.template_version=%s, r.updated_at=%s '
                           "WHERE r.id=%s AND r.deleted_at IS NULL AND a.deleted_at IS NULL AND a.job_status='completed'",
                           (html, TEMPLATE_VERSION, int(time()), audit['report_id']))
            if stored[1] is None and settings.discussion_notifications_enabled:
                enqueue(cursor, stored[0])
        connection.commit()
    return html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', type=UUID, help='Regenerate one completed report')
    args = parser.parse_args()
    settings = Settings()
    if args.job_id:
        jobs = [str(args.job_id)]
    else:
        with connect(settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT a.job_id FROM audits a JOIN audit_reports r ON r.audit_id=a.id '
                               "WHERE a.job_status='completed' AND a.deleted_at IS NULL AND r.deleted_at IS NULL "
                               'AND r.report_html IS NULL')
                jobs = [row[0] for row in cursor.fetchall()]
    failed = 0
    for job_id in jobs:
        try:
            generate_html(settings, job_id)
            print(f'{job_id}: rendered')
        except Exception as exc:
            failed += 1
            print(f'{job_id}: failed ({type(exc).__name__})')
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
