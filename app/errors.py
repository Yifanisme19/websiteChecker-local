"""Shared public error contract. Never expose exception details to callers."""
import logging

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Literal

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    status: Literal[0] = 0
    error_code: str


ERROR_RESPONSES = {
    code: {'model': ErrorResponse}
    for code in (401, 404, 405, 409, 422, 500, 503)
}


class AuditError(Exception):
    def __init__(self, code: int, error_code: str):
        super().__init__(error_code)
        self.code = code
        self.error_code = error_code


def error_response(code: int, error_code: str, headers=None):
    headers = dict(headers or {})
    headers['Cache-Control'] = 'no-store'
    if code == 401:
        headers['WWW-Authenticate'] = 'Bearer'
    return JSONResponse(status_code=code,
                        content={'status': 0, 'error_code': error_code}, headers=headers)


async def audit_error_handler(request, exc):
    return error_response(exc.code, exc.error_code)


async def validation_error_handler(request, exc):
    return error_response(422, 'VALIDATION_ERROR')


async def http_error_handler(request, exc):
    codes = {400: 'BAD_REQUEST', 401: 'UNAUTHORIZED', 403: 'FORBIDDEN',
             404: 'NOT_FOUND', 405: 'METHOD_NOT_ALLOWED', 422: 'VALIDATION_ERROR',
             500: 'INTERNAL_ERROR', 503: 'SERVICE_UNAVAILABLE'}
    return error_response(exc.status_code, codes.get(exc.status_code, 'HTTP_ERROR'), exc.headers)


async def internal_error_handler(request, exc):
    logger.error('Unhandled API error', exc_info=(type(exc), exc, exc.__traceback__))
    return error_response(500, 'INTERNAL_ERROR')
