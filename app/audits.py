import json
from typing import Annotated, Literal
from uuid import UUID
from app.retry_links import retry_url

import pymysql
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, field_validator

from app.database import insert_audit, get_audit, list_audits, get_report_html
from app.reports import SCHEMA, validate_report

from app.errors import AuditError
from app.tokens import authenticate_token

router = APIRouter()
bearer = HTTPBearer(auto_error=False)
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: Identifier = ''
    company_id: Identifier = ''
    website_url: HttpUrl = Field(max_length=2048)


    @field_validator('project_id', 'company_id', mode='before')
    @classmethod
    def normalize_missing_identifier(cls, value):
        return '' if value is None else value


class AuditResponse(BaseModel):
    status: Literal[1] = 1
    job_id: UUID
    job_status: Literal['queued'] = 'queued'


def require_token(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise AuditError(401, 'UNAUTHORIZED')
    try:
        token_id = authenticate_token(request.app.state.settings, credentials.credentials)
    except pymysql.MySQLError:
        raise AuditError(503, 'DATABASE_UNAVAILABLE') from None
    if token_id is None:
        raise AuditError(401, 'UNAUTHORIZED')
    request.state.api_token_id = token_id
    return token_id


@router.post('/audits', response_model=AuditResponse, status_code=202,
             dependencies=[Depends(require_token)])
def create_audit(payload: AuditRequest, request: Request):
    try:
        job_id = insert_audit(request.app.state.settings, payload.project_id,
                              payload.company_id, str(payload.website_url),
                              api_token_id=request.state.api_token_id)
    except pymysql.MySQLError:
        raise AuditError(503, 'DATABASE_UNAVAILABLE') from None
    return AuditResponse(job_id=UUID(job_id))


class AuditListItem(BaseModel):
    job_id: UUID
    status: Literal['queued', 'running', 'completed', 'failed']


class AuditListResponse(BaseModel):
    status: Literal[1] = 1
    lists: list[AuditListItem]
    page: int
    page_size: int
    total: int
    total_pages: int


@router.get('/audits', response_model=AuditListResponse,
            dependencies=[Depends(require_token)])
def audit_list(request: Request, response: Response,
               page: Annotated[int, Query(ge=1, le=2147483647)] = 1,
               page_size: Annotated[int, Query(ge=1, le=20)] = 20):
    try:
        items, total = list_audits(request.app.state.settings, page, page_size)
        result = AuditListResponse(lists=items, page=page, page_size=page_size,
                                   total=total, total_pages=(total + page_size - 1) // page_size)
    except pymysql.MySQLError:
        raise AuditError(503, 'DATABASE_UNAVAILABLE') from None
    except Exception:
        raise AuditError(500, 'INTERNAL_ERROR') from None
    response.headers['Cache-Control'] = 'no-store'
    return result


class AuditStatusResponse(BaseModel):
    api_token_id: int | None = None
    status: Literal[1] = 1
    job_id: UUID
    project_id: str
    company_id: str
    website_url: str
    job_status: Literal['queued', 'running', 'completed', 'failed']
    created_at: int
    updated_at: int
    started_at: int | None
    finished_at: int | None
    coverage_status: Literal['completed', 'partial', 'blocked', 'out_of_scope'] | None
    limitations: list[str]
    retry_url: str | None = None
    retry_allowed: bool = False
    report_available: bool
    error_code: str | None


class AuditReportResponse(BaseModel):
    status: Literal[1] = 1
    job_id: UUID
    report: dict = Field(json_schema_extra=SCHEMA)


def load_audit(request: Request, job_id: UUID, include_report=False):
    try:
        audit = get_audit(request.app.state.settings, str(job_id), include_report)
    except pymysql.MySQLError:
        raise AuditError(503, 'DATABASE_UNAVAILABLE') from None
    if audit is None:
        raise AuditError(404, 'AUDIT_NOT_FOUND')
    return audit


@router.get('/audits/{job_id}', response_model=AuditStatusResponse,
            dependencies=[Depends(require_token)])
def audit_status(job_id: UUID, request: Request, response: Response):
    audit = load_audit(request, job_id)
    try:
        metadata = json.loads(audit.pop('result_json') or '{}')
        if not isinstance(metadata, dict):
            raise ValueError
        failed = audit['job_status'] == 'failed'
        audit['retry_allowed'] = failed and 'outcome_unknown' not in (audit.get('error_code') or '')
        audit['retry_url'] = retry_url(request.app.state.settings, job_id) if failed else None
        audit['coverage_status'] = metadata.get('coverage_status')
        audit['limitations'] = metadata.get('limitations', [])
        audit['report_available'] = audit.pop('report_id') is not None and audit['job_status'] == 'completed'
        result = AuditStatusResponse(**audit)
    except (ValueError, TypeError):
        raise AuditError(503, 'STORED_AUDIT_INVALID') from None
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.get('/audits/{job_id}/report', response_model=AuditReportResponse,
            dependencies=[Depends(require_token)])
def audit_report(job_id: UUID, request: Request, response: Response):
    audit = load_audit(request, job_id, include_report=True)
    if audit['job_status'] != 'completed':
        raise AuditError(409, 'REPORT_NOT_AVAILABLE')
    if audit['report_id'] is None:
        raise AuditError(404, 'REPORT_NOT_FOUND')
    try:
        report = validate_report(json.loads(audit['report_json']), schema_version=audit['schema_version'])
    except (ValueError, TypeError):
        raise AuditError(503, 'STORED_REPORT_INVALID') from None
    response.headers['Cache-Control'] = 'no-store'
    return AuditReportResponse(job_id=job_id, report=report)


@router.get('/audits/{job_id}/report/html', response_class=Response,
            responses={200: {'content': {'text/html': {'schema': {'type': 'string'}}}}},
            dependencies=[Depends(require_token)])
def audit_report_html(job_id: UUID, request: Request):
    audit = load_audit(request, job_id)
    if audit['job_status'] != 'completed':
        raise AuditError(409, 'REPORT_NOT_AVAILABLE')
    if audit['report_id'] is None:
        raise AuditError(404, 'REPORT_NOT_FOUND')
    try:
        html = get_report_html(request.app.state.settings, str(job_id))
    except pymysql.MySQLError:
        raise AuditError(503, 'DATABASE_UNAVAILABLE') from None
    if html is None:
        raise AuditError(409, 'HTML_REPORT_NOT_AVAILABLE')
    return HTMLResponse(html, headers={
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        'Content-Disposition': f'inline; filename="audit-{job_id}.html"',
    })


class AuditRetryResponse(BaseModel):
    status: Literal[1] = 1
    original_job_id: UUID
    job_id: UUID
    job_status: Literal['queued', 'running', 'completed', 'failed']
    created: bool


@router.post('/audits/{job_id}/retry', response_model=AuditRetryResponse, status_code=202,
             responses={200: {'model': AuditRetryResponse}},
             dependencies=[Depends(require_token)])
def retry_failed_audit(job_id: UUID, request: Request, response: Response):
    from app.database import retry_audit

    try:
        result, created = retry_audit(request.app.state.settings, str(job_id),
                                      request.state.api_token_id)
    except pymysql.MySQLError:
        raise AuditError(503, 'DATABASE_UNAVAILABLE') from None
    response.status_code = 202 if created else 200
    response.headers['Cache-Control'] = 'no-store'
    return AuditRetryResponse(original_job_id=job_id, created=created, **result)
