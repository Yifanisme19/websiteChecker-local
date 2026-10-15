"""Fail closed after plugin replacement instead of silently regressing to oversized 502s."""
import json
from pathlib import Path
import subprocess


def assert_payload_guard():
    try:
        subprocess.run(['python3','-c','from PIL import Image'],capture_output=True,timeout=10,check=True)
        result=subprocess.run(['openclaw','plugins','list','--json'],text=True,capture_output=True,timeout=20,check=True)
        plugin=next(p for p in json.loads(result.stdout)['plugins'] if p['id']=='codex' and p.get('status')=='loaded')
        root=Path(plugin['rootDir'])/'dist'/'.setup'
        dispatch=(root/'inference-dispatch-B5UKEUyC.mjs').read_text()
        proxy=(root/'inference-proxy-DUO5E2QQ.mjs').read_text()
        if 'website-checker-payload-guard-v1' not in dispatch or 'const oversized = payloadFailure(error)' not in proxy:
            raise ValueError('Payload guard not present')
    except (OSError,ValueError,KeyError,StopIteration,subprocess.SubprocessError) as exc:
        raise RuntimeError('payload_guard_unverified_after_runtime_change') from exc
