import test from 'node:test';
import assert from 'node:assert/strict';
import {setup,login,rpc,request,example} from './helpers.mjs';
import {maintenance} from '../src/storage.mjs';
import {strict,validate} from '../src/validate.mjs';
import {mac,hash,random} from '../src/crypto.mjs';

test('receipt key rotation preserves old idempotency tombstones',async()=>{
 const {env}=await setup(),{tokens}=await login(env),d=example();
 const a=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d})).json();
 env.RECEIPT_KEYS=JSON.stringify({...JSON.parse(env.RECEIPT_KEYS),'receipt-v2':random()});env.RECEIPT_KEY_ID='receipt-v2';
 const b=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d})).json();
 assert.equal(b.result.structuredContent.receipt,a.result.structuredContent.receipt);assert.equal(b.result.structuredContent.status,'already-queued');
});


test('PKCE mismatch consumes code without issuing a token',async()=>{
 const {env}=await setup(),{tokenArgs}=await login(env);
 // Put a fresh synthetic authorization code into the same token path with wrong verifier.
 const {putAuth,now}=await import('../src/storage.mjs');
 const code=random();
 await putAuth(env,await hash(code),'code',{client_id:'synthetic',redirect_uri:'https://client.test/callback',resource:env.ORIGIN+'/mcp',user:'12345',code_challenge:'x'.repeat(43)},now()+120);
 const r=await request(env,'/token',{method:'POST',body:new URLSearchParams({...tokenArgs,code,code_verifier:'z'.repeat(43)}).toString()});
 assert.equal(r.status,401);
});
test('schema rejects extra transcript, malformed dates, ownership endpoints and blanket flags',()=>{
 for(const change of [
 d=>d.transcript='forbidden',d=>d.sourceTimestamp='2026-02-31T12:00:00Z',
 d=>d.claims[0].evidence='x'.repeat(401),d=>d.claims[0].confidence=2,
 d=>d.claims[0].uncertainty=[{field:'subject',reason:'maybe'}],
 d=>{d.claims[0].type='relationship';d.claims[0].relationship={type:'owner-of',source:'juniper',target:'ava'};},
 d=>d.claims[0].due={date:'2026-02-31',time:null,timezone:'UTC',ambiguity:'none'}
 ]){const d=example();change(d);assert.throws(()=>validate(d));}
});
test('lost response retry survives process state and unavailable storage fails closed',async()=>{
 const {env}=await setup(),{tokens}=await login(env),d=example();
 await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d});
 const retried=await (await rpc({...env},tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d})).json();
 assert.equal(retried.result.structuredContent.status,'already-queued');
 const failed=await rpc({...env,DB:{prepare(){throw new Error('outage');}}},tokens.access_token,'tools/list');
 assert.equal(failed.status,503);assert.deepEqual(await failed.json(),{error:'service-unavailable'});
});
test('delivered payload deletion retains idempotency and nonces expire only through maintenance',async()=>{
 const {env}=await setup(),{tokens}=await login(env);
 await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:example()});
 const t=Math.floor(Date.now()/1000);env.DB.db.prepare('UPDATE queue SET ack=?').run(t-86401);
 env.DB.db.prepare('INSERT INTO nonces VALUES(?,?)').run('expired',t-1);
 await maintenance(env,t);
 assert.equal(env.DB.db.prepare('SELECT envelope FROM queue').get().envelope,null);
 assert.equal(env.DB.db.prepare('SELECT count(*) n FROM nonces').get().n,0);
 const retried=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:example()})).json();
 assert.equal(retried.result.structuredContent.status,'already-queued');
});
test('generated provider schema stays identical to the Python contract artifact',async()=>{
 const {readFile}=await import('node:fs/promises');
 assert.deepEqual(JSON.parse(await readFile(new URL('../agent-schema.json',import.meta.url))),JSON.parse(await readFile(new URL('../../agent/capture/schema.json',import.meta.url))));
});


test('OAuth numeric identity, consent, PKCE, MCP initialize/list/call and encrypted idempotency',async()=>{
 const {env}=await setup(),{tokens,response}=await login(env);assert.equal(response.status,200);
 const init=await (await rpc(env,tokens.access_token,'initialize',{protocolVersion:'2025-06-18'})).json();assert.equal(init.result.serverInfo.name,'private-knowledge-intake');
 const list=await (await rpc(env,tokens.access_token,'tools/list')).json();assert.equal(list.result.tools.length,1);
 const d=example(),a=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d})).json();
 assert.equal(a.result.isError,false);
 const b=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d})).json();
 assert.equal(b.result.structuredContent.receipt,a.result.structuredContent.receipt);
 const stored=env.DB.db.prepare('SELECT * FROM queue').get();assert.ok(!stored.envelope.includes('Juniper'));assert.equal(stored.envelope.split('.').length,5);
 d.claims[0].value='Changed wording';const bad=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d})).json();
 assert.equal(bad.result.isError,true);assert.equal(bad.result.content[0].text,'idempotency-conflict');
});
test('wrong numeric ID denied even with matching username',async()=>{const {env}=await setup();assert.equal((await login(env,999)).callback.status,403);});
test('missing auth, revoked epoch, origin, host, protocol and forbidden tools fail',async()=>{
 const {env}=await setup();assert.equal((await rpc(env,'bad','tools/list')).status,401);
 const {tokens}=await login(env);
 assert.equal((await request(env,'/mcp',{method:'POST',headers:{Origin:'https://evil.test'},body:'{}'})).status,403);
 const absent=await (await rpc(env,tokens.access_token,'tools/call',{name:'approve',arguments:{}})).json();assert.equal(absent.error.code,-32601);
 env.DB.db.exec('UPDATE control SET epoch=1');assert.equal((await rpc(env,tokens.access_token,'tools/list')).status,401);
});
test('OAuth code replay and refresh reuse revoke the token family',async()=>{
 const {env}=await setup(),{tokens,tokenArgs}=await login(env);
 assert.equal((await request(env,'/token',{method:'POST',body:new URLSearchParams(tokenArgs).toString()})).status,401);
 const args={grant_type:'refresh_token',client_id:'synthetic',resource:env.ORIGIN+'/mcp',refresh_token:tokens.refresh_token};
 const fresh=await (await request(env,'/token',{method:'POST',body:new URLSearchParams(args).toString()})).json();assert.ok(fresh.access_token);
 assert.equal((await request(env,'/token',{method:'POST',body:new URLSearchParams(args).toString()})).status,401);
 assert.equal((await rpc(env,fresh.access_token,'tools/list')).status,401);
});
test('arbitrary redirect, repository scope and wrong audience rejected',async()=>{
 const {env}=await setup();
 for(const p of [{redirect_uri:'https://evil.test'},{scope:'repo'},{resource:'https://evil.test/mcp'}]){
 const q={client_id:'synthetic',redirect_uri:'https://client.test/callback',scope:'capture:write',resource:env.ORIGIN+'/mcp',response_type:'code',code_challenge_method:'S256',code_challenge:'x'.repeat(43),state:'state-123',...p};
 assert.equal((await request(env,'/authorize?'+new URLSearchParams(q))).status,400);
 }
});
test('strict JSON, payload and semantic bounds reject unsupported claims',async()=>{
 for(const raw of ['{"x":1,"x":2}','{"x":NaN}','[1,]','1e400'])assert.throws(()=>strict(raw));
 const d=example();d.claims[0].type='decision';d.claims[0].owner=null;assert.throws(()=>validate(d));
 const {env}=await setup();assert.equal((await request(env,'/mcp',{method:'POST',body:' '.repeat(40001)})).status,413);
});
test('kill switches and billing guard default closed',async()=>{
 const {env}=await setup(),{tokens}=await login(env);
 for(const change of [{ENABLED:'false'},{BILLING_MODE:'paid'}])assert.equal((await rpc({...env,...change},tokens.access_token,'tools/list')).status,503);
 env.DB.db.exec('UPDATE control SET enabled=0');assert.equal((await rpc(env,tokens.access_token,'tools/list')).status,503);
});
test('signed computer sync rejects replay and expired stamps',async()=>{
 const {env}=await setup(),key=JSON.parse(env.SYNC_KEYS).computer,body='{}',stamp=String(Math.floor(Date.now()/1000)),nonce=random();
 const headers={'x-key-id':'computer','x-timestamp':stamp,'x-nonce':nonce,'x-signature':await mac(key,['POST','/sync/pull',stamp,nonce,await hash(body)].join('\n'))};
 assert.equal((await request(env,'/sync/pull',{method:'POST',headers,body})).status,200);
 assert.equal((await request(env,'/sync/pull',{method:'POST',headers,body})).status,409);
 assert.equal((await request(env,'/sync/pull',{method:'POST',headers:{...headers,'x-timestamp':'1'},body})).status,401);
});
test('retention deletes bodies, keeps replay tombstones, records undelivered expiry',async()=>{
 const {env}=await setup(),{tokens}=await login(env);
 await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:example()});
 const t=Math.floor(Date.now()/1000);await maintenance(env,t+31*86400);
 assert.equal(env.DB.db.prepare('SELECT envelope FROM queue').get().envelope,null);
 assert.equal(env.DB.db.prepare("SELECT count(*) n FROM audit WHERE kind='expired-undelivered'").get().n,1);
});
test('atomic admission limits and immutable receipt fields',async()=>{
 const {env}=await setup(),{tokens}=await login(env);
 for(let i=0;i<21;i++){const d=example();d.claims[0].id='c'+i;const r=await (await rpc(env,tokens.access_token,'tools/call',{name:'capture_knowledge_delta',arguments:d})).json();assert.equal(r.result.isError,i===20);}
 assert.equal(env.DB.db.prepare('SELECT count(*) n FROM queue').get().n,20);
 assert.throws(()=>env.DB.db.exec("UPDATE queue SET principal='evil'"));
});
