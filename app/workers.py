"""Manage this project's independent Worker processes with a private Supervisor."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from xmlrpc.client import ServerProxy, Fault

from supervisor.xmlrpc import SupervisorTransport
from app.config import Settings, runtime_settings

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {'STOPPED', 'EXITED', 'FATAL', 'UNKNOWN'}


class WorkerPool:
    def __init__(self, root=ROOT, command=None, stop_wait=960):
        self.root = Path(root).absolute()
        self.runtime = self.root / '.runtime' / 'workers'
        self.programs = self.runtime / 'programs'
        self.logs = self.runtime / 'logs'
        self.config = self.runtime / 'supervisord.conf'
        self.socket = self.runtime / 'supervisor.sock'
        self.command = [str(value) for value in (command or [sys.executable, '-m', 'app.worker'])]
        self.stop_wait = stop_wait
        if len(os.fsencode(self.socket)) >= 104:
            raise ValueError('Project path is too long for a Unix socket')

    def _rpc(self):
        transport = SupervisorTransport(serverurl='unix://' + str(self.socket))
        return ServerProxy('http://localhost', transport=transport).supervisor

    def _live(self, allow_shutdown=False):
        try:
            rpc = self._rpc()
            state = rpc.getState()
            if state['statename'] != 'RUNNING' and not allow_shutdown:
                raise RuntimeError('Worker manager is shutting down; wait for current jobs to finish')
            return rpc
        except Fault as exc:
            if exc.faultCode == 6 and not allow_shutdown:
                raise RuntimeError('Worker manager is shutting down; wait for current jobs to finish') from None
            raise
        except OSError:
            return None

    @contextmanager
    def _lock(self):
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.runtime / 'manage.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    @staticmethod
    def _write(path, content):
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content)
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def _setup(self):
        self.programs.mkdir(exist_ok=True)
        self.logs.mkdir(exist_ok=True)
        root, runtime = str(self.root), str(self.runtime)
        for path in (root, runtime, *self.command):
            if any(c in path for c in ('\n', '\r', ';', '#', '%')):
                raise ValueError('Unsupported configuration characters in project or command path')
        config = f'''[unix_http_server]
file={self.socket}
chmod=0600

[supervisord]
logfile={self.logs / 'manager.log'}
logfile_maxbytes=5MB
logfile_backups=3
pidfile={self.runtime / 'supervisord.pid'}
childlogdir={self.logs}
directory={root}
identifier=website-checker-workers
umask=0077

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://{self.socket}

[include]
files={self.programs}/*.conf
'''
        if not self.config.exists():
            self._write(self.config, config)

    def _program(self, name):
        return f'''[program:{name}]
command={shlex.join(self.command)}
directory={self.root}
autostart=true
autorestart=unexpected
exitcodes=0
startsecs=1
startretries=3
stopsignal=TERM
stopwaitsecs={self.stop_wait}
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile={self.logs / (name + '.log')}
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
environment=PYTHONUNBUFFERED="1"
'''

    def _desired(self):
        return {p.stem for p in self.programs.glob('worker-*.conf')}

    def _resize_files(self, count, known_names):
        desired = sorted(self._desired())
        next_id = max([int(n.split('-')[1]) for n in set(desired) | set(known_names)] or [0]) + 1
        while len(desired) < count:
            name = f'worker-{next_id:04d}'
            self._write(self.programs / (name + '.conf'), self._program(name))
            desired.append(name)
            next_id += 1
        for name in desired[count:]:
            (self.programs / (name + '.conf')).unlink()

    def _apply(self, rpc):
        added, changed, removed = rpc.reloadConfig()[0]
        if changed:
            raise RuntimeError('Existing Worker configuration changed; restore it before scaling')
        for name in removed:
            info = rpc.getProcessInfo(name)
            if info['statename'] not in TERMINAL and info['statename'] != 'STOPPING':
                rpc.stopProcessGroup(name, False)  # Drain asynchronously, do not block expansion.
            info = rpc.getProcessInfo(name)
            if info['statename'] in TERMINAL:
                rpc.removeProcessGroup(name)
        for name in added:
            rpc.addProcessGroup(name)

    def resize(self, count, start=False):
        if count < 0:
            raise ValueError('Worker count cannot be negative')
        with self._lock():
            self._setup()
            rpc = self._live()
            if rpc is None and not start:
                raise RuntimeError('Worker manager is not running; use start --count N')
            known = [p['name'] for p in rpc.getAllProcessInfo()] if rpc else []
            self._resize_files(count, known)
            if rpc is None:
                # Refresh timeout/command settings only when no worker is running.
                for name in self._desired():
                    self._write(self.programs / (name + '.conf'), self._program(name))
                subprocess.run([sys.executable, '-m', 'supervisor.supervisord', '-c', str(self.config)],
                               cwd=self.root, check=True, timeout=10, capture_output=True)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    rpc = self._live()
                    if rpc:
                        break
                    time.sleep(0.1)
                if rpc is None:
                    raise RuntimeError('Worker manager did not start; inspect manager.log')
            else:
                self._apply(rpc)
        return self.status()

    def status(self):
        desired = self._desired()
        stopped = {'manager_running': False, 'desired_count': len(desired), 'workers': []}
        try:
            rpc = self._live(allow_shutdown=True)
            if rpc is None:
                return stopped
            processes = rpc.getAllProcessInfo()
            workers = [dict(name=p['name'], pid=p['pid'], state=p['statename'],
                            draining=p['name'] not in desired, log=p['stdout_logfile'])
                       for p in processes if p['name'] in desired or p['statename'] not in TERMINAL]
            return {'manager_running': True, 'manager_state': rpc.getState()['statename'],
                    'manager_pid': rpc.getPID(), 'desired_count': len(desired), 'workers': workers}
        except Fault as exc:
            if exc.faultCode == 6:  # Supervisor stops serving process RPCs during shutdown.
                return {'manager_running': True, 'manager_state': 'SHUTDOWN',
                        'desired_count': len(desired), 'workers': [],
                        'message': 'Waiting for workers to finish; process details unavailable during shutdown.'}
            raise
        except OSError:
            return stopped

    def stop(self):
        with self._lock():
            rpc = self._live()
            if rpc is not None:
                rpc.shutdown()
        return {'message': 'Shutdown requested. Busy workers finish their current job before exiting.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('start', 'scale'):
        sub = commands.add_parser(name)
        sub.add_argument('--count', type=int, required=True)
    commands.add_parser('status')
    commands.add_parser('stop')
    args = parser.parse_args()
    try:
        settings = runtime_settings()
        if args.action in ('start', 'scale'):
            if args.count < 0:
                parser.error('--count must be zero or greater')
            if not settings.openclaw_token.get_secret_value():
                parser.error('OPENCLAW_TOKEN is required')
            if settings.worker_stale_seconds <= settings.worker_heartbeat_seconds * 3:
                parser.error('WORKER_STALE_SECONDS must exceed three heartbeat intervals')
        pool = WorkerPool(stop_wait=settings.worker_request_timeout_seconds + 60)
        if args.action in ('start', 'scale'):
            result = pool.resize(args.count, start=args.action == 'start')
        elif args.action == 'stop':
            result = pool.stop()
        else:
            result = pool.status()
        result['environment'] = settings.app_env
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, Fault, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Worker manager error: {exc}\n')


if __name__ == '__main__':
    main()
