import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {posix} from 'node:path';
import {Miniflare} from 'miniflare';
import {setup,login,rpc,example,request} from './helpers.mjs';
import {mac,hash,random} from '../src/crypto.mjs';
import {pythonFlow} from './python-flow.mjs';

test('Cloudflare workerd and real local D1: OAuth, MCP, encrypted persistence, sync and kill switch',async()=>{
 const {env,keys}=await setup();
 const bundle=await build({entryPoints:['/src/worker.mjs'],bundle:true,write:false,format:'esm',platform:'browser',target:'es2022',tsconfigRaw:{},plugins:[{name:'workspace-only',setup(b){
 b.onResolve({filter:/.*/},args=>({path:posix.resolve(posix.dirname(args.importer||'/src/worker.mjs'),args.path),namespace:'workspace'}));
 b.onLoad({filter:/.*/,namespace:'workspace'},async args=>({contents:await readFile(new URL('..'+args.path,import.meta.url),'utf8'),loader:args.path.endsWith('.json')?'json':'js'}));
 }}]});
 const {DB,...bindings}=env;
 const mf=new Miniflare({modules:true,script:bundle.outputFiles[0].text,compatibilityDate:'2026-07-30',bindings,d1Databases:['DB'],host:'127.0.0.1',port:0,
 outboundService:async req=>{
 const u=new URL(req.url);
 if(u.href==='https://github.com/login/oauth/access_token'){
 const f=new URLSearchParams(await req.text());assert.equal(f.get('client_id'),'synthetic');
 return new Response(JSON.stringify({access_token:'synthetic',scope:''}),{headers:{'Content-Type':'application/json'}});
 }
 if(u.href==='https://api.github.com/user')return new Response(JSON.stringify({id:12345}),{headers:{'Content-Type':'application/json'}});
 throw new Error('Unexpected outbound request blocked');
 }});
 try{
 const db=await mf.getD1Database('DB');
 const migration=await readFile(new URL('../migrations/0001.sql',import.meta.url),'utf8');
 await db.batch(migration.split('\n').filter(line=>line.trim()&&!line.startsWith('--')).map(line=>db.prepare(line)));
 await db.prepare('UPDATE control SET enabled=1').run();
 env.dispatch=(url,opts)=>mf.dispatchFetch(url,{...opts,redirect:'manual'});
 const logged=await login(env);assert.ok(logged.response,logged.callback?await logged.callback.text():'No login result');
 const {tokens,response}=logged;assert.equal(response.status,200);
 const result=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:example()})).json();
 assert.equal(result.result.isError,false,JSON.stringify(result));
 const row=await db.prepare('SELECT * FROM queue').first();assert.ok(row.envelope);assert.ok(!row.envelope.includes('Juniper'));
 const key=JSON.parse(env.SYNC_KEYS).computer,body='{}',stamp=String(Math.floor(Date.now()/1000)),nonce=random();
 const headers={'x-key-id':'computer','x-timestamp':stamp,'x-nonce':nonce,'x-signature':await mac(key,['POST','/sync/pull',stamp,nonce,await hash(body)].join('\n'))};
 const pulled=await (await request(env,'/sync/pull',{method:'POST',body,headers})).json();assert.equal(pulled.items.length,1);
 const second=example();second.claims[0].id='second-claim';second.claims[0].value='Use short paragraphs for Juniper notes.';
 const secondResult=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:second})).json();assert.equal(secondResult.result.isError,false);
 const report=await pythonFlow(env,keys,env.dispatch);assert.equal(report.publicUnchanged,true);
 assert.equal((await db.prepare('SELECT count(*) n FROM queue WHERE ack IS NOT NULL').first()).n,2);
 await db.prepare('UPDATE control SET enabled=0,epoch=epoch+1').run();
 assert.equal((await rpc(env,tokens.access_token,'tools/list')).status,503);
 }finally{await mf.dispose();}
});
