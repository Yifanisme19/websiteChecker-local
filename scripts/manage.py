"""Manage one explicitly selected deployment, without touching its peer."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import runtime_settings
from app.workers import WorkerPool


def listener(port):
    result = subprocess.run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'],
                            text=True, capture_output=True)
    pids = set(result.stdout.split())
    if len(pids) > 1:
        raise RuntimeError('Multiple processes own the API port; inspect manually')
    return int(next(iter(pids))) if pids else None


def owned_listener(settings):
    pid = listener(settings.api_port)
    if pid is not None:
        cwd = subprocess.check_output(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], text=True)
        command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
        if 'n' + str(ROOT) not in cwd.splitlines() or '-m uvicorn app.main:app' not in command:
            raise RuntimeError('API port belongs to another process; refusing to stop or replace it')
    return pid


def api_status(settings):
    return {'environment': settings.app_env, 'port': settings.api_port,
            'pid': owned_listener(settings), 'log': str(ROOT / '.runtime/api.log')}


def stop_api(settings):
    pid = owned_listener(settings)
    if pid is None:
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(.1)
    raise RuntimeError('API is still draining; no forced termination was performed')


def start_api(settings):
    if owned_listener(settings) is not None:
        return
    runtime = ROOT / '.runtime'
    runtime.mkdir(exist_ok=True, mode=0o700)
    with (runtime / 'api.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
                                    '--host', settings.api_host, '--port', str(settings.api_port)],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('API failed to start; inspect .runtime/api.log')
        if owned_listener(settings) == process.pid:
            return
        time.sleep(.1)
    raise RuntimeError('API startup not confirmed; inspect .runtime/api.log before retrying')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('environment', choices=['local', 'prod'])
    parser.add_argument('component', choices=['api', 'workers', 'status', 'update', 'rollback', 'tokens'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    settings = runtime_settings()
    if not (ROOT / '.environment.json').is_file() or args.environment != settings.app_env:
        parser.error('Target does not match deployment binding')
    os.chdir(ROOT)
    runtime = ROOT / '.runtime'
    runtime.mkdir(exist_ok=True, mode=0o700)
    # Serializes managed starts/stops with updates. Worker stop remains asynchronous.
    with (runtime / 'deployment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.component == 'tokens':
            from app.tokens import main as manage_tokens
            manage_tokens(settings, args.arguments)
            return
        if args.component in ('update', 'rollback'):
            if settings.app_env != 'prod':
                parser.error('Updates target prod only')
            from promote import main as promote
            promote(settings, args.component, args.arguments)
            return
        if args.component == 'workers':
            result = subprocess.run([sys.executable, '-m', 'app.workers', *args.arguments], cwd=ROOT)
            raise SystemExit(result.returncode)
        if args.component == 'status':
            if args.arguments:
                parser.error('status takes no arguments')
            print(json.dumps({'api': api_status(settings), 'workers': WorkerPool().status(),
                              'database': settings.db_name, 'agent': settings.openclaw_agent_id}, indent=2))
            return
        if len(args.arguments) != 1 or args.arguments[0] not in ('start', 'stop', 'restart', 'status'):
            parser.error('api expects start, stop, restart or status')
        action = args.arguments[0]
        if action in ('stop', 'restart'):
            stop_api(settings)
        if action in ('start', 'restart'):
            start_api(settings)
        print(json.dumps(api_status(settings), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Management error: {exc}')
