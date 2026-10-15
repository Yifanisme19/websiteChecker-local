import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {zstdCompressSync} from 'node:zlib';
import {Readable} from 'node:stream';
import http from 'node:http';
const dir=process.argv[2];
const {createCodexInferenceDispatch}=await import(pathToFileURL(dir+'/.setup/inference-dispatch-B5UKEUyC.mjs'));
const {createCodexInferenceProxy}=await import(pathToFileURL(dir+'/.setup/inference-proxy-DUO5E2QQ.mjs'));
const dispatch=createCodexInferenceDispatch({context:{},assertCurrent(){}});
const signal=new AbortController().signal;
let count=0;
for(const size of [33554431,33554432]) {
 const r=Readable.from([zstdCompressSync(Buffer.alloc(size,32))]);r.headers={'content-encoding':'zstd'};
 const prepared=await dispatch.prepareHttp(r,false,'/responses',signal,()=>{});prepared.settle();prepared.releaseModelExecution();count++;
}
let upstreamCalls=0;
globalThis.fetch=async()=>{upstreamCalls++;throw new Error('No external requests in regression test');};
const proxy=await createCodexInferenceProxy({upstream:'https://api.openai.com/v1',assertCurrent(){}});
async function post(body,encoding,declared=true){return await new Promise((resolve,reject)=>{
 const req=http.request(proxy.baseUrl+'/responses',{method:'POST',headers:{...(encoding?{'content-encoding':encoding}:{}),...(declared?{'content-length':body.length}:{})}},res=>{let s='';res.on('data',b=>s+=b);res.on('end',()=>resolve({status:res.statusCode,body:s}));});req.on('error',reject);req.end(body);
});}
try {
 for(const [body,encoding,declared] of [[zstdCompressSync(Buffer.alloc(37579056,32)),'zstd',true],[Buffer.alloc(33554433,32),null,true],[Buffer.alloc(33554433,32),null,false]]){
  const r=await post(body,encoding,declared);assert.equal(r.status,413);assert.equal(JSON.parse(r.body).error.code,'payload_too_large');count++;
 }
 // Malformed compression remains transport failure, not a fabricated payload diagnosis.
 assert.equal((await post(Buffer.from('not zstd'),'zstd')).status,502);count++;
 assert.equal(upstreamCalls,0);
 console.log(JSON.stringify({passed:count,upstreamCalls}));
} finally {proxy.close();}
// Verify the Gateway's outward normalization, not only the private relay response.
const {execFileSync}=await import('node:child_process');
const {readdirSync}=await import('node:fs');
const core=execFileSync('npm',['root','-g'],{encoding:'utf8'}).trim()+'/openclaw/dist';
const compat=readdirSync(core).find(n=>/^openai-compat-errors-.*\.mjs$/.test(n));
const {t:resolveCompat}=await import(pathToFileURL(core+'/'+compat));
const outward=resolveCompat(new Error('unexpected status 413 Payload Too Large: Codex request body too large: decoded/wire limit is 33554432 bytes. Save progress and compact context; do not retry unchanged.'));
assert.equal(outward.status,413);assert.equal(outward.error.type,'invalid_request_error');
console.log(JSON.stringify({gatewayNormalization:'passed',status:outward.status}));
