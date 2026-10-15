"""Durable, at-most-once NEWPAGES discussion delivery. No automatic retries."""
import logging
import json
from html import escape
from app.retry_links import retry_url
from time import time

import httpx

from app.discussion_html import discussion_fragment
from app.database import connect
from app.report_safety import DISCUSSION_MAX_BYTES, ReportHTMLInvalid, ascii_fragment

URL = 'https://www.newpages2u.com/admin/project/ProjectAPI.php'
log = logging.getLogger(__name__)


def enqueue(cursor, audit_id):
    # Called in the transaction that persists HTML or marks a task failed.
    cursor.execute(
        'INSERT INTO discussion_notifications (audit_id, created_at) '
        "SELECT id, %s FROM audits WHERE id=%s AND TRIM(company_id) <> '' AND TRIM(project_id) <> '' "
        'ON DUPLICATE KEY UPDATE audit_id=VALUES(audit_id)', (int(time()), audit_id))


def claim(settings, job_id=None):
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            sql = (
                'SELECT n.id, a.company_id, a.project_id, r.report_html, '
                'a.job_status, a.job_id, a.website_url, a.error_code '
                'FROM discussion_notifications n JOIN audits a ON a.id=n.audit_id '
                'LEFT JOIN audit_reports r ON r.audit_id=a.id '
                "WHERE n.status='pending' AND a.deleted_at IS NULL "
                "AND ((a.job_status='completed' AND r.deleted_at IS NULL AND r.report_html IS NOT NULL) "
                "OR (a.job_status='failed' AND %s=1)) "
                "AND TRIM(a.company_id) <> '' AND TRIM(a.project_id) <> ''")
            args = (int(bool(settings.audit_retry_page_url)),)
            if job_id is not None:
                sql += ' AND a.job_id=%s'
                args += (job_id,)
            cursor.execute(sql + ' ORDER BY n.id LIMIT 1 FOR UPDATE SKIP LOCKED', args)
            row = cursor.fetchone()
            if row is not None:
                # Unknown is terminal: a crash after this commit must NEVER resend.
                cursor.execute(
                    "UPDATE discussion_notifications SET status='unknown', attempted_at=%s, "
                    "error_code='interrupted_or_in_progress' WHERE id=%s", (int(time()), row[0]))
        connection.commit()
    if row is not None:
        html = row[3]
        if row[4] == 'failed':
            link = escape(retry_url(settings, row[5]), quote=True)
            unknown = 'outcome_unknown' in (row[7] or '')
            message = ('The previous check may still be running. Ask an administrator to verify its outcome before retrying.'
                       if unknown else 'Open the link below and click Retry to start a new website check.')
            html = ('<h2>Website check failed / 网站检查失败</h2>'
                    f'<p>Website: {escape(row[6])}</p><p>Job: {escape(row[5])}</p>'
                    f'<p>{message}</p>'
                    f'<p><a href="{link}">View task / Retry · 查看任务 / 重试</a></p>')
            html = discussion_fragment(html)
        return row[:3] + (html,)
    return None


def post(settings, row):
    notification_id, company_id, project_id, html = row
    if not company_id or not project_id or not company_id.strip() or not project_id.strip():
        return 'failed', None, None, 'missing_newpages_identifiers'
    try:
        html = ascii_fragment(html)
    except ReportHTMLInvalid:
        return 'failed', None, None, 'discussion_html_invalid'
    if len(html.encode('ascii')) > DISCUSSION_MAX_BYTES:
        return 'failed', None, None, 'discussion_html_too_large'
    data = {'action': 'post-discussion', 'company_id': company_id, 'system_id': '2',
            'project_id': project_id, 'email': 'websitechecker@newpages.com.my',
            'post_message': html, 'token': settings.discussion_api_token.get_secret_value()}
    parameters = {**data, 'token': '[REDACTED]'}
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE discussion_notifications SET request_parameters=%s WHERE id=%s',
                (json.dumps(parameters, ensure_ascii=False), notification_id))
        connection.commit()
    try:
        with httpx.Client(transport=httpx.HTTPTransport(retries=0), trust_env=False,
                          follow_redirects=False, timeout=httpx.Timeout(20, connect=5)) as client:
            response = client.post(URL, data=data)
        if not 200 <= response.status_code < 300:
            return 'unknown', None, response.status_code, 'http_response_not_successful'
        try:
            body = response.json()
        except ValueError:
            return 'unknown', None, response.status_code, 'invalid_json'
        if not isinstance(body, dict):
            return 'unknown', None, response.status_code, 'invalid_response'
        if type(body.get('status')) is int and body['status'] == 1:
            message_id = body.get('id')
            if ((type(message_id) is int and message_id > 0) or
                    (isinstance(message_id, str) and message_id.strip() and len(message_id) <= 255)):
                return 'sent', str(message_id), response.status_code, None
            return 'unknown', None, response.status_code, 'missing_discussion_id'
        if type(body.get('status')) is int and body['status'] != 1:
            return 'failed', None, response.status_code, 'api_rejected'
        return 'unknown', None, response.status_code, 'invalid_response'
    except httpx.TimeoutException:
        return 'unknown', None, None, 'request_timeout'
    except httpx.HTTPError:
        return 'unknown', None, None, 'transport_error'


def dispatch_one(settings, job_id=None):
    if not settings.discussion_notifications_enabled:
        return False
    if not settings.discussion_api_token.get_secret_value():
        log.error('Discussion notifications enabled but token is missing')
        return False
    row = claim(settings, job_id)
    if row is None:
        return False
    status, message_id, http_status, error = post(settings, row)
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE discussion_notifications SET status=%s, discussion_id=%s, '
                'http_status=%s, error_code=%s, finished_at=%s WHERE id=%s',
                (status, message_id, http_status, error, int(time()), row[0]))
        connection.commit()
    log.info('Discussion notification %s: %s', row[0], status)
    return True


def dispatch_safely(settings, job_id=None):
    try:
        return dispatch_one(settings, job_id)
    except Exception:
        # Do not log payloads, credentials, response bodies or exception strings.
        # If final persistence failed, the committed unknown state prevents resends.
        log.error('Discussion notification processing interrupted; check database delivery state')
        return False


def main():
    """Administrative commands never send requests."""
    import argparse
    import getpass
    import json
    import pymysql
    from app.config import ROOT, runtime_settings

    parser = argparse.ArgumentParser(description='Manage one-shot discussion notifications (no sending)')
    parser.add_argument('action', choices=('migrate', 'status'))
    parser.add_argument('--admin-socket')
    args = parser.parse_args()
    settings = runtime_settings()
    if args.action == 'migrate':
        connection = (pymysql.connect(unix_socket=args.admin_socket, user=getpass.getuser(),
                                     database=settings.db_name, charset='utf8mb4',
                                     connect_timeout=5, read_timeout=30, write_timeout=30)
                      if args.admin_socket else connect(settings))
        with connection:
            with connection.cursor() as cursor:
                cursor.execute((ROOT / 'sql/008_discussion_notifications.sql').read_text())
                cursor.execute((ROOT / 'sql/010_discussion_request_parameters.sql').read_text())
            connection.commit()
        print(f'{settings.app_env}: notification schema ready')
    else:
        with connect(settings) as connection:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    'SELECT a.job_id, n.status, n.discussion_id, n.http_status, n.error_code, '
                    'n.created_at, n.attempted_at, n.finished_at FROM discussion_notifications n '
                    'JOIN audits a ON a.id=n.audit_id ORDER BY n.id DESC LIMIT 100')
                print(json.dumps({'environment': settings.app_env, 'deliveries': cursor.fetchall()}, indent=2))


if __name__ == '__main__':
    main()
