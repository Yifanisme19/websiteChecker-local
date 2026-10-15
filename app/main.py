from contextlib import asynccontextmanager
from time import time
from typing import Literal

import httpx
from fastapi import FastAPI, Response
from pydantic import BaseModel

from app.config import ROOT, Settings, runtime_settings
from app.request_log import RequestLogMiddleware, RequestLogWriter
from app.audits import router
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from app.errors import (AuditError, ErrorResponse, ERROR_RESPONSES, audit_error_handler,
                        validation_error_handler, http_error_handler, internal_error_handler)

API_PREFIX = '/websiteChecker/api/v1'


class HealthResponse(BaseModel):
    status: Literal[1]
    checked_at: int


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None, *, request_log_writer=None) -> FastAPI:
    runtime = settings is None
    settings = settings or runtime_settings()
    if request_log_writer is None and runtime:
        request_log_writer = RequestLogWriter(ROOT / ".runtime/api-requests.jsonl")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        headers = {}
        if settings.openclaw_token.get_secret_value():
            headers['Authorization'] = f'Bearer {settings.openclaw_token.get_secret_value()}'
        async with httpx.AsyncClient(
            base_url=str(settings.openclaw_base_url).rstrip('/') + '/',
            headers=headers,
            timeout=settings.openclaw_timeout_seconds,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            app.state.openclaw_client = client
            yield

    app = FastAPI(
        title=f'Website Checker API - {settings.app_env.upper()}', version='0.1.0', lifespan=lifespan,
        responses=ERROR_RESPONSES,
        docs_url=f'{API_PREFIX}/docs', redoc_url=None,
        openapi_url=f'{API_PREFIX}/openapi.json',
    )

    @app.middleware("http")
    async def environment_header(request, call_next):
        response = await call_next(request)
        response.headers["X-WebsiteChecker-Environment"] = settings.app_env
        return response

    app.state.settings = settings
    app.add_exception_handler(AuditError, audit_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)
    app.include_router(router, prefix=API_PREFIX)

    @app.get(f'{API_PREFIX}/health', response_model=HealthResponse,
             responses={503: {'model': ErrorResponse, 'description': 'OpenClaw unavailable'}})
    async def health(response: Response) -> HealthResponse:
        error = None
        try:
            upstream = await app.state.openclaw_client.get('healthz')
            if upstream.status_code != 200:
                error = 'upstream_error'
            else:
                try:
                    payload = upstream.json()
                except ValueError:
                    payload = None
                if not isinstance(payload, dict) or payload.get('ok') is not True:
                    error = 'invalid_response'
        except httpx.TimeoutException:
            error = 'timeout'
        except httpx.RequestError:
            error = 'connection_error'
        if error is not None:
            raise AuditError(503, 'OPENCLAW_UNAVAILABLE')
        response.headers['Cache-Control'] = 'no-store'
        return HealthResponse(status=1, checked_at=int(time()))

    if request_log_writer is not None:
        app.add_middleware(RequestLogMiddleware, writer=request_log_writer, environment=settings.app_env)

    return app


app = create_app()
