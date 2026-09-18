import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,sep} from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {setup,login,rpc,example} from './helpers.mjs';
import {handle} from '../src/worker.mjs';
import {b64,random} from '../src/crypto.mjs';
test('end-to-end OAuth/tool -> encrypted D1 -> outbound Python -> FEOS -> selected private approval',async()=>{
 const {env,keys}=await setup(),{tokens}=await login(env);
 const d=example();
 for(let i=0;i<2;i++){
 d.claims[0].id='claim-'+i;d.claims[0].value=i?'Use short paragraphs for Juniper notes.':'Use structured tables for Juniper notes.';
 const r=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d})).json();assert.equal(r.result.isError,false);
 }
 const root=await mkdtemp(join(tmpdir(),'feos-e2e-'));
 const server=createServer(async(req,res)=>{
 try{const parts=[];for await(const p of req)parts.push(p);
 const response=await handle(new Request(env.ORIGIN+req.url,{method:req.method,headers:req.headers,body:Buffer.concat(parts)}),env);
 res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
 }catch{res.writeHead(503);res.end('{}');}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
 const config={root,port:server.address().port,sync:JSON.parse(env.SYNC_KEYS).computer,sign:env.ENVELOPE_KEY,private:b64(await crypto.subtle.exportKey('pkcs8',keys.privateKey)),backup:random()};
 const file=join(root,'synthetic-keys.json');await writeFile(file,JSON.stringify(config));
 const python=process.env.FEOS_PYTHON||'python';
 const cwd=fileURLToPath(new URL('../../',import.meta.url));
 const outcome=await new Promise((resolve,reject)=>{
 const child=spawn(python,['-m','hosted.tests.e2e_client',file],{cwd,env:process.env});let out='',err='';
 child.stdout.on('data',d=>out+=d);child.stderr.on('data',d=>err+=d);child.on('error',reject);child.on('exit',code=>resolve({code,out,err}));});
 assert.equal(outcome.code,0,outcome.err);const report=JSON.parse(outcome.out);assert.equal(report.publicUnchanged,true);
 assert.equal(env.DB.db.prepare('SELECT count(*) n FROM queue WHERE ack IS NOT NULL').get().n,2);
 console.log('Synthetic E2E evidence:',report);
 }finally{await new Promise(r=>server.close(r));if(!resolve(root).startsWith(resolve(tmpdir())+sep)||!root.includes('feos-e2e-'))throw new Error('Unsafe cleanup target');await rm(root,{recursive:true,force:true});}
});
