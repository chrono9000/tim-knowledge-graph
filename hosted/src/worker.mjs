import {Fault,active,one,stmt,now,limit,event} from './storage.mjs';
import {hash,mac,verifyMac,seal,canonical,bytes} from './crypto.mjs';
import {validate,strict,schema} from './validate.mjs';
import {oauth,bearer,json} from './oauth.mjs';
const versions=['2025-06-18','2025-03-26','2025-11-25'];
async function bodyText(req,max){
 if(Number(req.headers.get('content-length'))>max)throw new Fault('oversized',413);
 const reader=req.body?.getReader();if(!reader)return '';
 const chunks=[];let n=0;
 while(true){const {value,done}=await reader.read();if(done)break;n+=value.length;if(n>max){await reader.cancel();throw new Fault('oversized',413);}chunks.push(value);}
 const out=new Uint8Array(n);let i=0;for(const c of chunks){out.set(c,i);i+=c.length;}
 try{return new TextDecoder('utf-8',{fatal:true}).decode(out);}catch{throw new Fault('malformed');}
}
async function computer(req,env,body){
 await active(env);const t=now(),stamp=req.headers.get('x-timestamp')||'',nonce=req.headers.get('x-nonce')||'',key=req.headers.get('x-key-id');
 const secrets=JSON.parse(env.SYNC_KEYS),secret=secrets[key];
 if(!secret||!/^\d+$/.test(stamp)||Math.abs(t-Number(stamp))>300||!/^[-_a-zA-Z0-9]{32,96}$/.test(nonce))throw new Fault('unauthorized',401);
 const signed=[req.method,new URL(req.url).pathname,stamp,nonce,await hash(body)].join('\n');
 if(!await verifyMac(secret,signed,req.headers.get('x-signature')||''))throw new Fault('unauthorized',401);
 try{await stmt(env,'INSERT INTO nonces VALUES(?,?)',key+':'+nonce,t+900).run();}catch{throw new Fault('replay',409);}
 await limit(env,'computer',120,60,t);
}
async function submit(env,principal,d){
 validate(d);const t=now(),text=canonical(d),receiptKeys=JSON.parse(env.RECEIPT_KEYS);
 const fingerprint=env.RECEIPT_KEY_ID+':'+await mac(receiptKeys[env.RECEIPT_KEY_ID],text);
 const identity=await hash(canonical([principal,d.conversationId,d.claims.map(c=>c.id).sort()]));
 const prior=await one(env,'SELECT hash FROM queue WHERE id=?',identity);
 if(prior){const [kid,value]=prior.hash.split(':');if(!receiptKeys[kid]||!await verifyMac(receiptKeys[kid],text,value))throw new Fault('idempotency-conflict',409);return {receipt:identity,status:'already-queued'};}
 await limit(env,'capture-minute:'+principal,20,60,t);await limit(env,'capture-day:'+principal,400,86400,t);
 const usage=await stmt(env,'SELECT 1').all();
 if(!Number.isFinite(usage.meta?.size_after)||usage.meta.size_after>=300*1024*1024)throw new Fault('capacity-unavailable-or-full',503);
 const packet={version:1,receipt:identity,principal,received:t,expires:t+30*86400,deltaText:text,contentHash:await hash(text)};
 const envelope=await seal(JSON.stringify(packet),JSON.parse(env.RECIPIENT_JWK),env.RECIPIENT_KEY_ID);
 try{await env.DB.batch([
 stmt(env,'INSERT INTO queue(id,principal,hash,envelope,received,expires) VALUES(?,?,?,?,?,?)',identity,principal,fingerprint,envelope,t,t+30*86400),
 stmt(env,"INSERT INTO audit(at,kind,ref) VALUES(?,'received',?)",t,identity)]);
 }catch{
 const won=await one(env,'SELECT hash FROM queue WHERE id=?',identity);
 if(won?.hash===fingerprint)return {receipt:identity,status:'already-queued'};
 if(won)throw new Fault('idempotency-conflict',409);
 throw new Fault('intake-unavailable',503);
 }
 return {receipt:identity,status:'queued'};
}
async function sync(req,env,body){
 await computer(req,env,body);const p=strict(body),path=new URL(req.url).pathname,t=now();
 if(path==='/sync/pull'){
 if(Object.keys(p).some(k=>k!=='after')||!Number.isSafeInteger(p.after??0)||(p.after??0)<0)throw new Fault('malformed');
 // A cyclic scan cursor avoids poison-message starvation. It never acknowledges data.
 const {results}=await stmt(env,'SELECT seq,id,envelope FROM queue WHERE ack IS NULL AND envelope IS NOT NULL AND expires>? AND seq>? ORDER BY seq LIMIT 20',t,p.after??0).all();
 const items=[];for(const r of results)items.push({receipt:r.id,envelope:r.envelope,signature:await mac(env.ENVELOPE_KEY,r.id+'\n'+r.envelope),keyId:env.ENVELOPE_KEY_ID});
 return json({items,next:results.length===20?results.at(-1).seq:0});
 }
 if(path==='/sync/ack'){
 if(Object.keys(p).sort().join(',')!=='backupHash,receipt'||! /^[a-f0-9]{64}$/.test(p.receipt)||! /^[a-f0-9]{64}$/.test(p.backupHash))throw new Fault('malformed');
 const r=await one(env,'SELECT id,ack FROM queue WHERE id=?',p.receipt);if(!r)throw new Fault('unknown-receipt',404);
 await env.DB.batch([stmt(env,'UPDATE queue SET ack=coalesce(ack,?),backup=coalesce(backup,?) WHERE id=?',t,p.backupHash,p.receipt),stmt(env,"INSERT INTO audit(at,kind,ref) VALUES(?,'delivery-backed-up',?)",t,p.receipt)]);
 return json({status:'acknowledged'});
 }
 if(path==='/health'){
 const count=await one(env,'SELECT count(*) pending,min(received) oldest FROM queue WHERE ack IS NULL AND envelope IS NOT NULL');
 return json({...count,enabled:true,schedulerActive:false,graphWriterAvailable:false});
 }
 throw new Fault('not-found',404);
}
export async function handle(req,env,fetcher=fetch){
 try{
 const url=new URL(req.url);
 if(url.origin!==env.ORIGIN||!env.ORIGIN.startsWith('https://'))throw new Fault('invalid-host',403);
 if(req.headers.get('origin')&&req.headers.get('origin')!==env.ORIGIN)throw new Fault('invalid-origin',403);
 if(url.pathname==='/live'&&req.method==='GET')return json({service:'private-intake'});
 if(url.pathname.startsWith('/.well-known/')||['/authorize','/callback','/consent','/token'].includes(url.pathname))return await oauth(req,env,await bodyText(req,8192),fetcher);
 if(req.method!=='POST')throw new Fault('method-not-allowed',405);
 const body=await bodyText(req,40000);
 if(url.pathname.startsWith('/sync/')||url.pathname==='/health')return await sync(req,env,body);
 if(url.pathname!=='/mcp')throw new Fault('not-found',404);
 const user=await bearer(req,env);
 await limit(env,'mcp:'+user,60,60);
 if(!req.headers.get('content-type')?.startsWith('application/json'))throw new Fault('content-type',415);
 const accept=req.headers.get('accept')||'';if(!accept.includes('application/json')||!accept.includes('text/event-stream'))throw new Fault('accept',406);
 const p=strict(body);
 if(p.jsonrpc!=='2.0'||typeof p.method!=='string'||(p.id!==undefined&&typeof p.id!=='string'&&typeof p.id!=='number'))throw new Fault('invalid-rpc');
 if(p.method==='notifications/initialized'&&p.id===undefined)return new Response(null,{status:202});
 if(p.id===undefined)throw new Fault('missing-id');
 const result=x=>json({jsonrpc:'2.0',id:p.id,result:x});
 if(p.method==='initialize')return result({protocolVersion:versions.includes(p.params?.protocolVersion)?p.params.protocolVersion:versions[0],capabilities:{tools:{listChanged:false}},serverInfo:{name:'private-knowledge-intake',version:'0.1.0'},instructions:'Submit minimal durable candidate knowledge for later private human review. No approval, publication or graph access.'});
 if(!versions.includes(req.headers.get('mcp-protocol-version')))throw new Fault('protocol-version');
 if(p.method==='ping')return result({});
 if(p.method==='tools/list')return result({tools:[{name:'capture_knowledge_delta',description:'Submit bounded knowledge proposals from the current conversation to a private human review inbox. Never send full transcripts.',inputSchema:schema,annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:true,openWorldHint:false},securitySchemes:[{type:'oauth2',scopes:['capture:write']}]}]});
 if(p.method==='tools/call'&&p.params?.name==='capture_knowledge_delta'){
 try{const receipt=await submit(env,user,p.params.arguments);return result({content:[{type:'text',text:JSON.stringify(receipt)}],structuredContent:receipt,isError:false});}
 catch(e){if(!(e instanceof Fault))throw e;return result({content:[{type:'text',text:e.message}],isError:true});}
 }
 return json({jsonrpc:'2.0',id:p.id,error:{code:-32601,message:'Method not found'}});
 }catch(e){
 const status=e instanceof Fault?e.status:503,code=e instanceof Fault?e.message:'service-unavailable';
 return json({error:code},status,{...(status===401?{'WWW-Authenticate':'Bearer resource_metadata="'+env.ORIGIN+'/.well-known/oauth-protected-resource/mcp"'}:{}),...(status===429?{'Retry-After':'60'}:{})});
 }
}
// Cloudflare passes ExecutionContext as the third argument, not an HTTP fetcher.
export default {fetch(request,env){return handle(request,env);}};
