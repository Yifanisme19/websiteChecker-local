"""Bounded local request audit log. No headers, query strings, bodies or raw URLs."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import time, monotonic
from uuid import UUID


class RequestLogWriter:
    def __init__(self, path):
        self.path = Path(path)
        self.handler = None

    def __call__(self, record):
        if self.handler is None:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.handler = RotatingFileHandler(self.path, maxBytes=2_000_000, backupCount=4, encoding='utf-8')
            self.path.chmod(0o600)
            self.handler.setFormatter(logging.Formatter('%(message)s'))
        line = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        self.handler.handle(logging.LogRecord('api_requests', logging.INFO, '', 0, line, (), None))
        self.path.chmod(0o600)


class RequestLogMiddleware:
    def __init__(self, app, writer, environment):
        self.app, self.writer, self.environment = app, writer, environment

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        started, timestamp, status = monotonic(), time(), 500

        async def capture(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            route = getattr(scope.get('route'), 'path', None)
            # Use the registered route template, never user-controlled URLs.
            route = route if isinstance(route, str) else '(unmatched)'
            method = scope.get('method', '')
            method = method if method in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'TRACE', 'CONNECT'} else 'OTHER'
            token_id = scope.get('state', {}).get('api_token_id')
            token_id = token_id if isinstance(token_id, int) and not isinstance(token_id, bool) else None
            job_id = None
            try:
                job_id = str(UUID(str(scope.get('path_params', {}).get('job_id'))))
            except (ValueError, TypeError):
                pass
            record = dict(timestamp=timestamp, environment=self.environment, token_id=token_id,
                          method=method, path=route, status=status,
                          duration_ms=round((monotonic() - started) * 1000, 2), job_id=job_id)
            try:
                # Short local file write; logging failure must not change API responses.
                self.writer(record)
            except Exception:
                logging.getLogger(__name__).error('Request audit write failed; request completed without an audit entry')
