"""Shared configurable cPanel retry entry for API responses and notifications."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def retry_url(settings, job_id):
    if not settings.audit_retry_page_url:
        return None
    parts = urlsplit(str(settings.audit_retry_page_url))
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in ('retry_job_id', 'action', 'job_id')]
    query.append(('retry_job_id', str(job_id)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
