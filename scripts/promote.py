"""Allowlisted, offline Local -> Prod updates and file rollback; no database migrations."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from app.database import connect
from app.workers import WorkerPool
from manage import ROOT, owned_listener

DIRECTORIES = ('app', 'tests', 'contracts', 'prompts', 'sql', 'scripts')
FILES = ('manage', 'README.md', 'requirements.txt', 'requirements.lock.txt', '.env.example', '.gitignore', 'ENVIRONMENTS.md')


def inventory(root, directories, files):
    result = {}
    for name in (*directories, *files):
        base = root / name
        if base.is_symlink():
            raise RuntimeError(f'Symlink is not allowed in release content: {base}')
        paths = base.rglob('*') if base.is_dir() else [base]
        for path in paths:
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if path.is_symlink():
                raise RuntimeError(f'Symlink is not allowed in release content: {path}')
            if path.is_file():
                result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(result.items()))


def snapshot(source, destination, contents):
    destination.mkdir(parents=True, exist_ok=False)
    for name, digest in contents.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Source changed while packaging; no deployment performed')


def replace_files(source, target, old, new):
    # Only names from the allowlisted inventory can be removed or replaced.
    for name in old.keys() - new.keys():
        (target / name).unlink(missing_ok=True)
    for name in new:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.release-tmp')
        shutil.copy2(source / name, temp)
        temp.replace(path)


def skill_inventory(workspace):
    return inventory(workspace, ('skills/web-checker',), ())


def require_offline(settings):
    if owned_listener(settings) is not None:
        raise RuntimeError('Stop Prod API first: ./manage prod api stop')
    if WorkerPool().status()['manager_running']:
        raise RuntimeError('Stop Prod workers and wait for draining: ./manage prod workers stop')
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM audits WHERE job_status='running'")
            if cursor.fetchone()[0]:
                raise RuntimeError('Prod still has running audits; wait and resolve them before updating')
    # Catch unmanaged workers too: a running module can otherwise use files mid-update.
    result = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True)
    for line in result.splitlines():
        if '-m app.worker' in line:
            pid = line.strip().split(None, 1)[0]
            cwd = subprocess.run(['lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'], capture_output=True, text=True)
            if 'n' + str(ROOT) in cwd.stdout.splitlines():
                raise RuntimeError('An unmanaged Prod worker is still running')


def main(settings, action, arguments):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--apply', action='store_true', help='Otherwise show a read-only preview')
    args = parser.parse_args(arguments)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', args.version):
        parser.error('Version must be a short safe filename')
    binding = json.loads((ROOT / '.environment.json').read_text())
    workspace = Path(binding['agent_workspace'])
    local = ROOT.parent / 'websiteChecker-local'
    release = ROOT / '.runtime/releases' / args.version
    current = inventory(ROOT, DIRECTORIES, FILES)
    current_skill = skill_inventory(workspace)
    if action == 'update':
        local_binding = json.loads((local / '.environment.json').read_text())
        if local_binding['app_env'] != 'local' or Path(local_binding['agent_workspace']) == workspace:
            raise RuntimeError('Invalid Local deployment binding')
        source, skill_source = local, Path(local_binding['agent_workspace'])
        candidate = inventory(source, DIRECTORIES, FILES)
        candidate_skill = skill_inventory(skill_source)
        if not candidate_skill or 'app/main.py' not in candidate:
            raise RuntimeError('Incomplete Local source')
    else:
        manifest = json.loads((release / 'manifest.json').read_text())
        source, skill_source = release / 'backup/website', release / 'backup/agent'
        candidate = inventory(source, DIRECTORIES, FILES)
        candidate_skill = skill_inventory(skill_source)
        if candidate != manifest['before'] or candidate_skill != manifest['skill_before']:
            raise RuntimeError('Rollback files do not match saved checksums')
    changes = lambda before, after: [name for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    print(json.dumps({'action': action, 'version': args.version, 'apply': args.apply,
                      'website_changes': changes(current, candidate),
                      'skill_changes': changes(current_skill, candidate_skill),
                      'preserved': ['.env', '.environment.json', '.venv', '.runtime', 'database',
                                    'agent identity/config/sessions', 'audits', 'Gateway']}, indent=2))
    if not args.apply:
        return
    require_offline(settings)
    for name in ('requirements.txt', 'requirements.lock.txt'):
        if current.get(name) != candidate.get(name):
            raise RuntimeError('Dependency changes require a separate planned release; this updater preserves .venv')
    # This updater deliberately never applies schema migrations automatically.
    if action == 'update':
        release.mkdir(parents=True, exist_ok=False, mode=0o700)
        snapshot(source, release / 'candidate/website', candidate)
        snapshot(skill_source, release / 'candidate/agent', candidate_skill)
        source, skill_source = release / 'candidate/website', release / 'candidate/agent'
        snapshot(ROOT, release / 'backup/website', current)
        snapshot(workspace, release / 'backup/agent', current_skill)
        (release / 'manifest.json').write_text(json.dumps({
            'version': args.version, 'created_at': datetime.now(timezone.utc).isoformat(),
            'before': current, 'after': candidate, 'skill_before': current_skill,
            'skill_after': candidate_skill}, indent=2))
    else:
        # A rollback must not silently destroy edits made after this release.
        if current != manifest['after'] or current_skill != manifest['skill_after']:
            raise RuntimeError('Prod changed after this release; inspect differences before rollback')
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=source)
    if result.returncode:
        raise RuntimeError('Candidate tests failed; Prod files were not changed')
    for script in ('test-report.py', 'test-evidence.py'):
        test = skill_source / 'skills/web-checker/scripts' / script
        if test.is_file():
            subprocess.run([sys.executable, str(test)], cwd=skill_source, check=True)
    visual_test = skill_source / 'skills/web-checker/scripts/test-visual-budget.py'
    if visual_test.is_file():
        # Inspection helper runs in host python3 with Pillow, not app .venv.
        subprocess.run(['python3', str(visual_test)], cwd=skill_source, check=True)
    try:
        replace_files(source, ROOT, current, candidate)
        replace_files(skill_source, workspace, current_skill, candidate_skill)
    except Exception:
        if action == 'update':
            replace_files(release / 'backup/website', ROOT, candidate, current)
            replace_files(release / 'backup/agent', workspace, candidate_skill, current_skill)
        raise
    (ROOT / '.runtime/current-release.json').write_text(json.dumps({'action': action, 'version': args.version}))
    print('Files updated. Review any SQL migrations, then start Prod API and choose its worker count. Gateway was not restarted.')
