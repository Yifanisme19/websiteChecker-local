"""OpenClaw Responses client. A task has its own agent session."""
import logging
from app.visual_checkpoint import maybe_compact, CheckpointError
from app.payload_preflight import assert_payload_guard
import time

import httpx
from app.config import Settings


class OpenClawError(Exception):
    pass


log = logging.getLogger(__name__)


class TerminalTransportError(OpenClawError):
    """Gateway confirmed a finished, failed run (not an unknown outcome)."""


def _terminal_transport_failure(response):
    if response.status_code not in (500, 502, 503):
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    if not isinstance(data, dict) or data.get('object') != 'response' or data.get('status') != 'failed' or not data.get('id'):
        return False
    error = data.get('error')
    if not isinstance(error, dict) or error.get('code') not in ('api_error', 'server_error', 'upstream_error'):
        return False
    message = str(error.get('message', '')).lower()
    if any(marker in message for marker in ('auth', 'token', 'quota', 'rate limit', 'tool', 'permission')):
        return False
    # Gateway sanitizes provider server errors; recognize its exact terminal
    # contract, not just raw provider text. Do not admit arbitrary proxy 502s.
    if response.status_code == 502 and message == 'upstream provider error':
        return True
    return any(marker in message for marker in (
        'transport', 'agent run failed', 'internal error', 'bad gateway',
        'stream disconnected', 'broken pipe', 'upstream connection',
    ))


def send_prompt(settings: Settings, job_id: str, prompt: str) -> dict:
    current = prompt
    for stage in range(21):
        result = _send_with_transport_recovery(settings, job_id, current)
        try:
            if not maybe_compact(settings, job_id, result['text']):
                return result
        except CheckpointError as exc:
            raise OpenClawError(str(exc)) from None
        log.info('Job %s native visual checkpoint compacted, stage %d', job_id, stage + 1)
        current = (
            'Native compaction was confirmed and the visual budget generation was advanced. '
            'Continue this SAME audit. Read audits/' + job_id + '/progress.json and current-job evidence first. '
            'Resume ONLY the existing owned TaskSpace recorded there; never create a replacement. '
            'Keep all scope and report requirements from the original instructions. '
            'Use prepare-visual.py for EVERY image sent to the model. Do not re-view already inspected images. '
            'Finish the remaining checks, checkpoint again if needed, then validate/export/read back and return the final report. '
            'Do not call task.finish until final completion. Original instruction:\n' + prompt
        )
    raise OpenClawError('visual_checkpoint_stage_limit')


def _send_with_transport_recovery(settings: Settings, job_id: str, prompt: str) -> dict:
    for attempt in range(3):
        current_prompt = prompt if attempt == 0 else (
            'The preceding run ended with a confirmed infrastructure failure. '
            'Recover this SAME audit using the existing session, owned TaskSpace, '
            'and current-job evidence. Inspect saved progress first; do not repeat '
            'completed actions. If a validated report was already exported, read '
            'it back and return its JSON envelope without reopening the browser. '
            'Never replace a closed or lost TaskSpace; return truthful blocked or '
            'partial coverage if it cannot be resumed. If no TaskSpace was ever '
            'created, begin the original task normally. Original instruction:\n' + prompt
        )
        try:
            return _send_once(settings, job_id, current_prompt)
        except TerminalTransportError:
            if attempt == 2:
                raise
            delay = 5 * (attempt + 1)
            log.warning('Job %s confirmed terminal transport failure; same-session recovery %d/2 in %ds',
                        job_id, attempt + 1, delay)
            time.sleep(delay)


def _send_once(settings: Settings, job_id: str, prompt: str) -> dict:
    if settings.payload_guard_required:
        try:
            assert_payload_guard()
        except RuntimeError as exc:
            raise OpenClawError(str(exc)) from None
    headers = {
        'Authorization': 'Bearer ' + settings.openclaw_token.get_secret_value(),
        'x-openclaw-agent-id': settings.openclaw_agent_id,
        'x-openclaw-model': settings.openclaw_model,
        'x-openclaw-session-key': f'agent:{settings.openclaw_agent_id}:website-checker:{job_id}',
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(settings.worker_request_timeout_seconds, connect=10),
                          trust_env=False, follow_redirects=False) as client:
            response = client.post(str(settings.openclaw_base_url).rstrip('/') + '/v1/responses',
                headers=headers, json={
                    'model': f'openclaw/{settings.openclaw_agent_id}',
                    'input': f'/think:{settings.openclaw_thinking}\n{prompt}',
                    'stream': False,
                })
        if response.status_code != 200:
            # Payload rejection is deterministic; never replay the same oversized context.
            try:
                error_body = response.json()
                err = error_body.get('error', {}) if isinstance(error_body, dict) else {}
                error_code = err.get('code', '') if isinstance(err, dict) else ''
                error_message = err.get('message', '') if isinstance(err, dict) else ''
            except ValueError:
                error_code = error_message = ''
            if response.status_code == 413 or error_code in ('payload_too_large', 'request_too_large', 'context_length_exceeded') or any(marker in str(error_message).lower() for marker in ('payload_too_large', 'err_buffer_too_large', 'request body too large')):
                raise OpenClawError('upstream_payload_too_large')
            if _terminal_transport_failure(response):
                raise TerminalTransportError(f'upstream_http_{response.status_code}')
            raise OpenClawError(f'upstream_http_{response.status_code}')
        data = response.json()
        if not isinstance(data, dict) or data.get('status') != 'completed' or not data.get('id'):
            raise OpenClawError('upstream_incomplete_response')
        text = '\n'.join(
            part['text'] for item in data.get('output', []) if item.get('type') == 'message'
            for part in item.get('content', []) if part.get('type') == 'output_text' and isinstance(part.get('text'), str)
        )
        if not text.strip():
            raise OpenClawError('upstream_empty_response')
        return {'response_id': data['id'], 'text': text, 'usage': data.get('usage')}
    except httpx.TimeoutException:
        raise OpenClawError('upstream_timeout_outcome_unknown') from None
    except httpx.RequestError:
        raise OpenClawError('upstream_connection_error_outcome_unknown') from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise OpenClawError('upstream_invalid_response') from None
