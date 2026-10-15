"""Validated, same-session native compaction between bounded visual stages."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
from uuid import UUID

class CheckpointError(Exception):
    pass

def audit_root(settings, job_id):
    # Deployment identity, never a caller-supplied filesystem path.
    UUID(job_id)
    allowed = {'website-checker-agent','website-checker-agent-local'}
    if settings.openclaw_agent_id not in allowed:
        raise CheckpointError('visual_checkpoint_agent_not_supported')
    root = Path.home()/'.openclaw'/('workspace-'+settings.openclaw_agent_id)/'audits'/job_id
    if root.is_symlink() or root.resolve() != root:
        raise CheckpointError('visual_checkpoint_path_not_canonical')
    return root

def maybe_compact(settings, job_id, text):
    try:
        envelope=json.loads(text)
    except (ValueError,TypeError):
        return False
    if not isinstance(envelope,dict) or envelope.get('coverage_status')!='checkpoint':
        return False
    if set(envelope)!={'coverage_status','checkpoint'}:
        raise CheckpointError('invalid_visual_checkpoint')
    cp=envelope['checkpoint']
    if not isinstance(cp,dict) or set(cp)!={'version','job_id','space_id','progress_file'} or cp['version']!=1 or cp['job_id']!=job_id or type(cp['space_id']) is not int or cp['space_id']<1 or cp['progress_file']!='progress.json':
        raise CheckpointError('invalid_visual_checkpoint')
    root=audit_root(settings,job_id)
    try:
        progress_path=root/'progress.json'
        if progress_path.is_symlink() or (root/'visual-budget.json').is_symlink():
            raise ValueError('symlink')
        progress=json.loads(progress_path.read_text())
        if progress.get('job_id')!=job_id or progress.get('space_id')!=cp['space_id'] or not isinstance(progress.get('completed'),list) or not isinstance(progress.get('remaining'),list) or not progress['remaining']:
            raise ValueError('progress')
        ledger=json.loads((root/'visual-budget.json').read_text())
        if type(ledger['generation']) is not int or type(ledger['encoded_bytes']) is not int or ledger['encoded_bytes']<=0:
            raise ValueError('ledger')
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise CheckpointError('invalid_visual_checkpoint_files') from exc
    key=f'agent:{settings.openclaw_agent_id}:website-checker:{job_id}'
    try:
        result=subprocess.run(['openclaw','sessions','compact',key,'--agent',settings.openclaw_agent_id,'--json'],capture_output=True,text=True,timeout=360,check=False)
        data=json.loads(result.stdout)
        details = data.get('result', {}).get('details', {})
        if result.returncode or data.get('ok') is not True or data.get('compacted') is not True or details.get('backend') != 'codex-app-server' or details.get('completed') is not True or details.get('pending') is not False:
            raise ValueError('Native compaction did not confirm success')
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        # Unknown outcome is never retried or treated as a successfully cleared budget.
        raise CheckpointError('visual_checkpoint_compaction_unconfirmed') from exc
    with (root/'.visual-budget.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        latest=json.loads((root/'visual-budget.json').read_text())
        if latest!=ledger:
            raise CheckpointError('visual_checkpoint_state_changed')
        archive=root/f'visual-budget-generation-{ledger["generation"]}.json'
        with archive.open('x') as f: json.dump(ledger,f,indent=2)
        updated={'generation':ledger['generation']+1,'encoded_bytes':0,'reservations':[]}
        tmp=root/'visual-budget.json.tmp';tmp.write_text(json.dumps(updated,indent=2));os.replace(tmp,root/'visual-budget.json')
    return True
