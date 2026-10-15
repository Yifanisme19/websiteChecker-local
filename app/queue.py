import json
import logging
from time import time

from app.database import connect
from app.reports import CONTRACT_VERSION, PROMPT_VERSION, parse_delivery
from app.render_reports import generate_html
from app.discussion_notifications import enqueue


def claim(settings, worker_id, job_id=None):
    """Lock one row until its queued -> running transition is committed."""
    with connect(settings) as c:
        with c.cursor() as q:
            sql = "SELECT job_id, project_id, company_id, website_url FROM audits WHERE job_status='queued' AND deleted_at IS NULL"
            args = ()
            if job_id:
                sql += ' AND job_id=%s'
                args = (job_id,)
            q.execute(sql + ' ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED', args)
            row = q.fetchone()
            if not row:
                c.commit()
                return None
            now = int(time())
            q.execute("UPDATE audits SET job_status='running', worker_id=%s, started_at=%s, heartbeat_at=%s, updated_at=%s, prompt_version=%s WHERE job_id=%s",
                      (worker_id, now, now, now, PROMPT_VERSION, row[0]))
        c.commit()
    return dict(zip(('job_id', 'project_id', 'company_id', 'website_url'), row))


def heartbeat(settings, job_id, worker_id):
    with connect(settings) as c:
        with c.cursor() as q:
            now = int(time())
            q.execute("UPDATE audits SET heartbeat_at=%s, updated_at=%s WHERE job_id=%s AND worker_id=%s AND job_status='running'", (now, now, job_id, worker_id))
            owned = q.rowcount
        c.commit()
    return owned


def finish(settings, job_id, worker_id, result=None, error=None):
    # Revalidate at the persistence boundary; public data never supplies audit_id.
    delivery = None
    if not error:
        delivery = parse_delivery(result['text'])
        if delivery.report is None:
            raise ValueError('A successful task requires a report')
    with connect(settings) as c:
        try:
            with c.cursor() as q:
                q.execute("SELECT id FROM audits WHERE job_id=%s AND worker_id=%s AND job_status='running' AND deleted_at IS NULL FOR UPDATE",
                          (job_id, worker_id))
                row = q.fetchone()
                if row is None:
                    c.rollback()
                    return 0
                now = int(time())
                if delivery is not None:
                    q.execute('INSERT INTO audit_reports (audit_id, schema_version, report_json, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)',
                              (row[0], CONTRACT_VERSION, json.dumps(delivery.report, ensure_ascii=False), now, now))
                # Store only response metadata here; the public report has one source of truth.
                metadata = {k: result[k] for k in ('coverage_status', 'limitations', 'usage') if k in result} if result else None
                if delivery is not None:
                    metadata.update(coverage_status=delivery.coverage_status, limitations=delivery.limitations)
                # Partial coverage is a deliverable report; retain limitations
                # in metadata and use the same HTML/notification path as completed.
                incomplete = delivery is not None and delivery.coverage_status not in ('completed', 'partial')
                final_status = 'failed' if error or incomplete else 'completed'
                final_error = error or ('audit_coverage_incomplete' if incomplete else None)
                q.execute("UPDATE audits SET job_status=%s, result_json=%s, response_id=%s, error_code=%s, finished_at=%s, updated_at=%s WHERE id=%s",
                          (final_status, json.dumps(metadata, ensure_ascii=False) if metadata else None,
                           result.get('response_id') if result else None, final_error, now, now, row[0]))
                if error and settings.discussion_notifications_enabled and settings.audit_retry_page_url:
                    enqueue(q, row[0])
            c.commit()
            if delivery is not None and delivery.coverage_status in ('completed', 'partial'):
                try:
                    generate_html(settings, job_id)
                except Exception:
                    # JSON and completion are durable; rendering can be retried independently.
                    logging.getLogger(__name__).exception('HTML rendering failed for %s; JSON preserved', job_id)
            return 1
        except Exception:
            c.rollback()
            raise


def recover_stale(settings):
    # A crashed HTTP caller may have started an upstream run: never requeue blindly.
    with connect(settings) as c:
        with c.cursor() as q:
            now = int(time())
            # Lock the exact stale rows so notification creation and failure state are atomic.
            q.execute("SELECT id FROM audits WHERE job_status='running' "
                      "AND (heartbeat_at IS NULL OR heartbeat_at < %s) FOR UPDATE",
                      (now - settings.worker_stale_seconds,))
            stale_ids = [row[0] for row in q.fetchall()]
            q.execute("UPDATE audits SET job_status='failed', error_code='worker_lost_outcome_unknown', finished_at=%s, updated_at=%s WHERE job_status='running' AND (heartbeat_at IS NULL OR heartbeat_at < %s)",
                      (now, now, now - settings.worker_stale_seconds))
            changed = q.rowcount
            if settings.discussion_notifications_enabled and settings.audit_retry_page_url:
                for audit_id in stale_ids:
                    enqueue(q, audit_id)
        c.commit()
    return changed
