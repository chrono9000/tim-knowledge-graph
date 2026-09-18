import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,sep} from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {b64,random} from '../src/crypto.mjs';
export async function pythonFlow(env,keys,dispatch){
 const root=await mkdtemp(join(tmpdir(),'feos-e2e-'));
 const server=createServer(async(req,res)=>{
 try{const parts=[];for await(const p of req)parts.push(p);
 const response=await dispatch(env.ORIGIN+req.url,{method:req.method,headers:req.headers,body:Buffer.concat(parts)});
 res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
 }catch{res.writeHead(503);res.end('{}');}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
 const config={root,port:server.address().port,sync:JSON.parse(env.SYNC_KEYS).computer,sign:env.ENVELOPE_KEY,private:b64(await crypto.subtle.exportKey('pkcs8',keys.privateKey)),backup:random()};
 const file=join(root,'synthetic-keys.json');await writeFile(file,JSON.stringify(config));
 const python=process.env.FEOS_PYTHON||'python',cwd=fileURLToPath(new URL('../../',import.meta.url));
 const outcome=await new Promise((resolve,reject)=>{
 const child=spawn(python,['-m','hosted.tests.e2e_client',file],{cwd,env:process.env});let out='',err='';
 child.stdout.on('data',d=>out+=d);child.stderr.on('data',d=>err+=d);child.on('error',reject);child.on('exit',code=>resolve({code,out,err}));});
 assert.equal(outcome.code,0,outcome.err);return JSON.parse(outcome.out);
 }finally{
 await new Promise(r=>server.close(r));
 if(!resolve(root).startsWith(resolve(tmpdir())+sep)||!root.includes('feos-e2e-'))throw new Error('Unsafe cleanup target');
 await rm(root,{recursive:true,force:true});
 }
}

