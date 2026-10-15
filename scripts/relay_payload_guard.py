"""Version/hash-gated Codex relay hotfix. Stage first; apply only during Gateway maintenance.
No cap increase, no credentials, no payload logging. Upgrade replaces installed patches;
run --check after upgrades. --restore only reverts files still matching this patch.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

MARK='website-checker-payload-guard-v1'
HELPER='''// website-checker-payload-guard-v1
const PAYLOAD_MESSAGE = "Codex request body too large: decoded/wire limit is 33554432 bytes. Save progress and compact context; do not retry unchanged.";
class CodexPayloadTooLarge extends Error {
 constructor(stage, observedBytes) { super(PAYLOAD_MESSAGE); this.code = "payload_too_large"; this.stage = stage; this.observedBytes = observedBytes; }
}
function payloadFailure(error) {
 if (!(error instanceof CodexPayloadTooLarge) && error?.code !== "ERR_BUFFER_TOO_LARGE") return;
 const facts = {stage:error.stage ?? "decoded",limit_bytes:33554432,...Number.isFinite(error.observedBytes) ? {observed_bytes:error.observedBytes} : {}};
 console.warn(JSON.stringify({event:"codex_payload_rejected",...facts}));
 return JSON.stringify({type:"error",status:413,error:{type:"invalid_request_error",code:"payload_too_large",message:PAYLOAD_MESSAGE,...facts}});
}
'''

def transform_dispatch(s):
    s=s.replace('const MAX_BODY_BYTES = 33554432;',HELPER+'\nconst MAX_BODY_BYTES = 33554432;',1)
    for a,b in [('if (rewritten.length > 33554432) throw new Error(FAILURE);','if (rewritten.length > MAX_BODY_BYTES) throw new CodexPayloadTooLarge("rewritten", rewritten.length);'),('for await (const chunk of stream) {', 'for await (const chunk of (stream.iterator ? stream.iterator({ destroyOnReturn: false }) : stream)) {'),('if (size > maxBytes) throw new Error(FAILURE);','if (size > maxBytes) { stream.resume?.(); throw new CodexPayloadTooLarge("wire", size); }'),('export { CODEX_INFERENCE_TRANSPORT_FAILURE,','export { payloadFailure, CODEX_INFERENCE_TRANSPORT_FAILURE,')]:
        if a not in s:raise RuntimeError('Unsupported dispatch layout')
        s=s.replace(a,b,1)
    return s

def transform_proxy(s):
    a='import { CODEX_INFERENCE_TRANSPORT_FAILURE,'
    if a not in s:raise RuntimeError('Unsupported proxy imports')
    s=s.replace(a,'import { payloadFailure, CODEX_INFERENCE_TRANSPORT_FAILURE,',1)
    a='const denied = authorizationFailure(error);\n\t\t\t\t\tif (denied)'
    b='''const oversized = payloadFailure(error);
                    const denied = authorizationFailure(error);
                    if (oversized) res.writeHead(413, { "content-type": "application/json" }).end(oversized);
                    else if (denied)'''
    if a not in s:raise RuntimeError('Unsupported proxy catch layout')
    s = s.replace(a,b,1)
    # Structured size failure on WebSocket request preparation too; frame maxPayload
    # remains bounded by ws (1009), and its HTTP fallback receives the same 413.
    a = 'const denied = authorizationFailure(error);\n\t\t\t\t\t\t\t\t\t\tif (denied && accepted.readyState'
    b = 'const denied = payloadFailure(error) ?? authorizationFailure(error);\n\t\t\t\t\t\t\t\t\t\tif (denied && accepted.readyState'
    if a not in s: raise RuntimeError('Unsupported WebSocket error layout')
    return s.replace(a,b,1)

def resolve():
    raw=subprocess.check_output(['openclaw','plugins','list','--json'],text=True)
    p=next(x for x in json.loads(raw)['plugins'] if x['id']=='codex')
    return Path(p['rootDir']),p['version']

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path);p.add_argument('--apply',type=Path);p.add_argument('--restore',type=Path);p.add_argument('--check',action='store_true')
    a=p.parse_args();root,version=resolve();setup=root/'dist'/'.setup'
    names=['inference-dispatch-B5UKEUyC.mjs','inference-proxy-DUO5E2QQ.mjs']
    if a.check:
        ok=all((MARK in (setup/n).read_text() if i==0 else 'const oversized = payloadFailure(error)' in (setup/n).read_text()) for i,n in enumerate(names))
        print(json.dumps({'version':version,'payload_guard_active_on_disk':ok,'note':'Loaded Gateway still needs verified restart after apply.'}));return 0 if ok else 1
    if a.stage:
        if version not in ('2026.10.2','2026.10.3'):raise RuntimeError('Unreviewed plugin version')
        a.stage.mkdir(parents=True,exist_ok=False)
        shutil.copytree(setup,a.stage/'.setup')
        (a.stage/'node_modules').mkdir()
        core=Path(subprocess.check_output(['npm','root','-g'],text=True).strip())/'openclaw'
        (a.stage/'node_modules'/'openclaw').symlink_to(core)
        manifest={'version':version,'root':str(root),'files':{}}
        for name,fn in zip(names,[transform_dispatch,transform_proxy]):
            src=setup/name
            if MARK in src.read_text():raise RuntimeError('Already patched; do not restage')
            shutil.copy2(src,a.stage/(name+'.original'))
            dst=a.stage/'.setup'/name;dst.write_text(fn(src.read_text()))
            manifest['files'][name]={'before':digest(src),'after':digest(dst)}
        (a.stage/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest));return 0
    stage=a.apply or a.restore
    if not stage: p.error('Choose --stage, --apply, --restore or --check')
    m=json.loads((stage/'manifest.json').read_text())
    if m['root']!=str(root) or m['version']!=version:raise RuntimeError('Plugin changed since staging')
    for name,h in m['files'].items():
        if digest(setup/name)!=h['before' if a.apply else 'after']:raise RuntimeError('Hash drift; refusing overwrite: '+name)
        source=stage/'.setup'/name if a.apply else stage/(name+'.original')
        if digest(source)!=h['after' if a.apply else 'before']:raise RuntimeError('Candidate/backup corrupted')
    for name in m['files']:
        source=stage/'.setup'/name if a.apply else stage/(name+'.original')
        target=setup/name;tmp=target.with_suffix('.guard-tmp');shutil.copy2(source,tmp);tmp.replace(target)
    print(json.dumps({'applied':bool(a.apply),'restored':bool(a.restore),'restart_required':True}))
    return 0
if __name__=='__main__':raise SystemExit(main())
