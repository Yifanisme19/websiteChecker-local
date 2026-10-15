"""Run with python -m app.worker; connection-only check: --probe."""
import argparse
import json
import logging
import os
from pathlib import Path
import signal
from threading import Event, Thread
from uuid import UUID, uuid4

import pymysql

from app.config import Settings, runtime_settings
from app.openclaw import OpenClawError, send_prompt
from app import queue
from app.discussion_notifications import dispatch_safely
from app.reports import ReportError, parse_delivery

log = logging.getLogger('website_checker.worker')
PROMPT = Path(__file__).resolve().parent.parent / 'prompts' / 'web-checker-v3.txt'


def _terminal_blocked(delivery):
    """Return true when a blocked result cannot be resumed safely.

    A closed or ownership-lost TaskSpace is terminal. Re-sending the same
    continuation prompt only creates an OpenClaw run loop and cannot collect
    new evidence.
    """
    if delivery is None or delivery.coverage_status != 'blocked':
        return False
    text = ' '.join(delivery.limitations).lower()
    return any(marker in text for marker in (
        'task space not found', 'taskspace not found', 'space not found',
        'already closed', 'cannot be resumed', 'loses ownership',
        'lost ownership', 'ownership prohibited',
    ))


def process_one(settings, worker_id, job_id=None):
    task = queue.claim(settings, worker_id, job_id)
    if task is None:
        return False
    stop = Event()

    def keep_alive():
        while not stop.wait(settings.worker_heartbeat_seconds):
            try:
                queue.heartbeat(settings, task['job_id'], worker_id)
            except pymysql.MySQLError:
                log.error('Heartbeat database failure for %s', task['job_id'])

    thread = Thread(target=keep_alive, daemon=True)
    thread.start()
    log.info('Started job %s', task['job_id'])
    result = None
    try:
        try:
            prompt = PROMPT.read_text() + '\nTask JSON:\n' + json.dumps(task, ensure_ascii=False)
            result = send_prompt(settings, task['job_id'], prompt)
            try:
                delivery = parse_delivery(result['text'], allow_blocked_findings=True)
            except ReportError as exc:
                # A blocked checkpoint may contain findings already confirmed
                # before the interruption. It is still resumable; ask the same
                # session to finish coverage before treating it as invalid.
                if str(exc) != 'report_blocked_with_findings':
                    raise
                delivery = None
            # Deliver valid partial reports immediately. Coverage and limitations
            # remain truthful metadata; never send a continuation for partial.
            # Recovery below applies only to blocked/out-of-scope responses.
            max_continuations = 6
            seen_checkpoints = set()
            for attempt in range(max_continuations):
                if delivery is not None and delivery.coverage_status in ('completed', 'partial'):
                    break
                if _terminal_blocked(delivery):
                    log.warning('Job %s has terminal blocked TaskSpace; stopping recovery', task['job_id'])
                    break
                if delivery is not None:
                    fingerprint = (delivery.coverage_status, tuple(delivery.limitations))
                    if fingerprint in seen_checkpoints:
                        log.warning('Job %s repeated the same checkpoint; stopping recovery', task['job_id'])
                        break
                    seen_checkpoints.add(fingerprint)
                checkpoint = ''
                if delivery is not None and delivery.limitations:
                    checkpoint = ' The current checkpoint limitations are: ' + '; '.join(delivery.limitations[:20]) + '.'
                continuation = (
                    'Resume this same audit in the existing owned Ego TaskSpace. '
                    'The previous response was a partial checkpoint. Do not call '
                    'task.finish() yet. Complete every remaining required page, '
                    'viewport, detail URL, screenshot, and evidence check from the '
                    'Skill, then rerun evidence validation and return the final '
                    'JSON envelope. Do not invent coverage or reuse historical evidence.'
                    + checkpoint +
                    ' If a required check is marked NOT TESTED because a control is ambiguous, '
                    'inspect its screenshot and DOM, try each plausible control once, and if '
                    'the control is absent record explicit inapplicable evidence rather than '
                    'leaving the check unresolved.'
                )
                log.warning('Job %s partial checkpoint; requesting continuation %d/%d',
                            task['job_id'], attempt + 1, max_continuations)
                result = send_prompt(settings, task['job_id'], continuation)
                try:
                    delivery = parse_delivery(result['text'], allow_blocked_findings=True)
                except ReportError as exc:
                    if str(exc) != 'report_blocked_with_findings' or attempt == max_continuations - 1:
                        raise
                    delivery = None
            if delivery is None:
                raise ReportError('report_incomplete_after_resume')
            result.update(coverage_status=delivery.coverage_status, limitations=delivery.limitations)
        except OpenClawError as exc:
            queue.finish(settings, task['job_id'], worker_id, error=str(exc))
            log.warning('Job %s failed: %s', task['job_id'], exc)
        except ReportError as exc:
            queue.finish(settings, task['job_id'], worker_id, result=result, error=str(exc))
            log.warning('Job %s rejected report: %s', task['job_id'], exc)
        except (OSError, ValueError):
            queue.finish(settings, task['job_id'], worker_id, error='worker_prompt_error')
        else:
            if queue.finish(settings, task['job_id'], worker_id, result=result):
                if delivery.coverage_status in ('completed', 'partial'):
                    log.info('Finished job %s: completed', task['job_id'])
                else:
                    log.warning('Job %s saved an incomplete checkpoint: %s', task['job_id'], delivery.coverage_status)
            else:
                log.error('Job %s no longer owned; result not saved', task['job_id'])
    finally:
        stop.set()
        thread.join(timeout=12)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Process at most one queued job')
    parser.add_argument('--job-id', type=UUID, help='Only process this queued job (requires --once)')
    parser.add_argument('--probe', action='store_true', help='Send a small connection test without touching the queue')
    args = parser.parse_args()
    if args.job_id and not args.once:
        parser.error('--job-id requires --once')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(process)d %(message)s')
    settings = runtime_settings()
    if settings.worker_stale_seconds <= settings.worker_heartbeat_seconds * 3:
        parser.error('WORKER_STALE_SECONDS must exceed three heartbeat intervals')
    if not settings.openclaw_token.get_secret_value():
        parser.error('OPENCLAW_TOKEN is required')
    if args.probe:
        session = str(uuid4())
        log.info('Probe session %s, agent %s, requested model %s, thinking %s', session,
                 settings.openclaw_agent_id, settings.openclaw_model, settings.openclaw_thinking)
        result = send_prompt(settings, session, 'Connection test only. Do not use tools or browse. Reply exactly WORKER_CONNECTION_OK.')
        print(json.dumps(result, ensure_ascii=False))
        return
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    worker_id = str(uuid4())
    log.info('Worker %s started as %s', worker_id, os.environ.get('SUPERVISOR_PROCESS_NAME', 'standalone'))
    while not stop.is_set():
        try:
            if not args.job_id:
                queue.recover_stale(settings)
            worked = process_one(settings, worker_id, str(args.job_id) if args.job_id else None)
        except pymysql.MySQLError:
            log.error('Database operation failed; no automatic upstream retry')
            if args.once:
                raise SystemExit(1)
            worked = False
        if not stop.is_set():
            dispatch_safely(settings, str(args.job_id) if args.job_id else None)
        if args.once:
            break
        if not worked:
            stop.wait(settings.worker_poll_seconds)
    log.info('Worker %s stopped after completing its current task', worker_id)


if __name__ == '__main__':
    main()
