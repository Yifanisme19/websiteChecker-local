from time import time
from uuid import uuid4

import pymysql

from app.config import Settings


def connect(settings: Settings):
    return pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=settings.db_user,
        password=settings.db_pass.get_secret_value(), database=settings.db_name,
        charset='utf8mb4', connect_timeout=5, read_timeout=5, write_timeout=5,
    )


def insert_audit(settings: Settings, project_id: str, company_id: str, website_url: str, api_token_id: int | None = None) -> str:
    with connect(settings) as connection:
        for attempt in range(3):
            job_id = str(uuid4())
            now = int(time())
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO audits (job_id, project_id, company_id, website_url, job_status, created_at, updated_at, deleted_at, api_token_id) '
                        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                        (job_id, project_id, company_id, website_url, 'queued', now, now, None, api_token_id),
                    )
                connection.commit()
                return job_id
            except pymysql.IntegrityError as exc:
                connection.rollback()
                if exc.args[0] != 1062 or attempt == 2:
                    raise
    raise RuntimeError('Unable to allocate job ID')


def get_audit(settings: Settings, job_id: str, include_report: bool = False):
    with connect(settings) as connection:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            report_column = ', r.report_json, r.schema_version' if include_report else ''
            cursor.execute(
                'SELECT a.job_id, a.project_id, a.company_id, a.website_url, a.job_status, '
                'a.created_at, a.updated_at, a.started_at, a.finished_at, a.error_code, a.api_token_id, '
                'a.result_json, r.id AS report_id' + report_column +
                ' FROM audits a LEFT JOIN audit_reports r ON r.audit_id=a.id AND r.deleted_at IS NULL '
                'WHERE a.job_id=%s AND a.deleted_at IS NULL', (job_id,),
            )
            return cursor.fetchone()


def list_audits(settings: Settings, page: int = 1, page_size: int = 20):
    if not 1 <= page <= 2147483647 or not 1 <= page_size <= 20:
        raise ValueError("Invalid pagination")
    with connect(settings) as connection:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute('SELECT COUNT(*) AS total FROM audits WHERE deleted_at IS NULL')
            total = cursor.fetchone()['total']
            cursor.execute(
                'SELECT job_id, job_status AS status FROM audits '
                'WHERE deleted_at IS NULL ORDER BY id DESC LIMIT %s OFFSET %s',
                (page_size, (page - 1) * page_size),
            )
            return cursor.fetchall(), total


def get_report_html(settings: Settings, job_id: str):
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT r.report_html FROM audit_reports r JOIN audits a ON a.id=r.audit_id '
                           "WHERE a.job_id=%s AND a.job_status='completed' AND a.deleted_at IS NULL AND r.deleted_at IS NULL",
                           (job_id,))
            row = cursor.fetchone()
            return row[0] if row else None


def retry_audit(settings: Settings, job_id: str, api_token_id: int) -> tuple[dict, bool]:
    """Serialize retries on the original row; persist child and link atomically."""
    from app.errors import AuditError

    with connect(settings) as connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    'SELECT id, project_id, company_id, website_url, job_status, error_code '
                    'FROM audits WHERE job_id=%s AND deleted_at IS NULL FOR UPDATE', (job_id,))
                original = cursor.fetchone()
                if original is None:
                    raise AuditError(404, 'AUDIT_NOT_FOUND')
                cursor.execute(
                    'SELECT a.job_id, a.job_status, a.deleted_at FROM audit_retries r '
                    'JOIN audits a ON a.id=r.retry_audit_id WHERE r.original_audit_id=%s',
                    (original['id'],))
                existing = cursor.fetchone()
                if existing is not None:
                    if existing['deleted_at'] is not None:
                        raise AuditError(409, 'RETRY_TASK_DELETED')
                    result = {'job_id': existing['job_id'], 'job_status': existing['job_status']}
                    created = False
                else:
                    if original['job_status'] != 'failed':
                        raise AuditError(409, 'AUDIT_NOT_RETRYABLE')
                    if 'outcome_unknown' in (original['error_code'] or ''):
                        raise AuditError(409, 'AUDIT_OUTCOME_UNKNOWN')
                    new_job_id, now = str(uuid4()), int(time())
                    cursor.execute(
                        'INSERT INTO audits (job_id, project_id, company_id, website_url, '
                        'job_status, created_at, updated_at, api_token_id) '
                        "VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s)",
                        (new_job_id, original['project_id'], original['company_id'],
                         original['website_url'], now, now, api_token_id))
                    child_id = cursor.lastrowid
                    cursor.execute(
                        'INSERT INTO audit_retries (original_audit_id, retry_audit_id, created_at) '
                        'VALUES (%s, %s, %s)', (original['id'], child_id, now))
                    result = {'job_id': new_job_id, 'job_status': 'queued'}
                    created = True
            connection.commit()
            return result, created
        except Exception:
            connection.rollback()
            raise
