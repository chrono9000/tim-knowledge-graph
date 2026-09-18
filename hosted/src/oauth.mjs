import {Fault,active,one,stmt,now,limit,putAuth,consume} from './storage.mjs';
import {random,hash,b64,bytes} from './crypto.mjs';
export const json=(v,status=200,headers={})=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...headers}});
const go=(url,cookie)=>new Response(null,{status:302,headers:{Location:url,'Cache-Control':'no-store',...(cookie?{'Set-Cookie':cookie}:{})}});
const cookie=s=>'__Host-capture='+s+'; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=600';
const browserCookie=req=>req.headers.get('cookie')?.split(';').map(s=>s.trim()).find(s=>s.startsWith('__Host-capture='))?.slice(15);
const allowed=(env,id)=>JSON.parse(env.ALLOWED_GITHUB_IDS).includes(id);
const clients=env=>JSON.parse(env.OAUTH_CLIENTS);
function checkClient(env,p){
 const c=clients(env)[p.client_id];
 if(!c||!c.redirect_uris.includes(p.redirect_uri)||p.resource!==env.ORIGIN+'/mcp'||p.scope!=='capture:write'||p.response_type!=='code'||p.code_challenge_method!=='S256'||!/^[-_a-zA-Z0-9]{43}$/.test(p.code_challenge)||typeof p.state!=='string'||p.state.length<8||p.state.length>256)throw new Fault('invalid-oauth-request');
 return c;
}
async function issue(env,p,t){
 const access=random(),refresh=random(),family=p.family||random();
 const payload={user:p.user,client:p.client,resource:env.ORIGIN+'/mcp',scope:'capture:write',epoch:(await active(env)).epoch,family};
 await env.DB.batch([
 stmt(env,'INSERT INTO auth VALUES(?,?,?,?,0)',await hash(access),'access',JSON.stringify(payload),t+600),
 stmt(env,'INSERT INTO auth VALUES(?,?,?,?,0)',await hash(refresh),'refresh',JSON.stringify({...payload,max:p.max||t+30*86400}),Math.min(p.max||t+30*86400,t+30*86400))]);
 return json({access_token:access,token_type:'Bearer',expires_in:600,scope:'capture:write',refresh_token:refresh});
}
export async function bearer(req,env){
 const t=now(),token=req.headers.get('authorization')?.match(/^Bearer ([-_a-zA-Z0-9]{43})$/)?.[1];
 if(!token)throw new Fault('unauthorized',401);
 const row=await one(env,"SELECT * FROM auth WHERE id=? AND kind='access' AND used=0 AND expires>?",await hash(token),t);
 if(!row)throw new Fault('unauthorized',401);
 const p=JSON.parse(row.payload),control=await active(env);
 if(p.epoch!==control.epoch||!allowed(env,p.user)||p.scope!=='capture:write'||p.resource!==env.ORIGIN+'/mcp'||await one(env,"SELECT id FROM auth WHERE id=? AND kind='revoked'",p.family))throw new Fault('unauthorized',401);
 return p.user;
}
export async function oauth(req,env,body,fetcher=fetch){
 const u=new URL(req.url),path=u.pathname,t=now();
 if(path==='/.well-known/oauth-protected-resource/mcp')return json({resource:env.ORIGIN+'/mcp',authorization_servers:[env.ORIGIN],scopes_supported:['capture:write'],bearer_methods_supported:['header']});
 if(path==='/.well-known/oauth-authorization-server')return json({issuer:env.ORIGIN,authorization_endpoint:env.ORIGIN+'/authorize',token_endpoint:env.ORIGIN+'/token',response_types_supported:['code'],grant_types_supported:['authorization_code','refresh_token'],code_challenge_methods_supported:['S256'],token_endpoint_auth_methods_supported:['none'],scopes_supported:['capture:write']});
 await active(env);
 await limit(env,'oauth-global',120,60,t);
 if(path==='/authorize'&&req.method==='GET'){
 const p=Object.fromEntries(u.searchParams);checkClient(env,p);
 const state=random(),binding=random();
 await putAuth(env,await hash(state),'login',{...p,binding:await hash(binding)},t+600);
 const target=new URL('https://github.com/login/oauth/authorize');
 target.search=new URLSearchParams({client_id:env.GITHUB_CLIENT_ID,redirect_uri:env.ORIGIN+'/callback',state,scope:''}).toString();
 return go(target.toString(),cookie(binding));
 }
 if(path==='/callback'&&req.method==='GET'){
 const state=u.searchParams.get('state'),code=u.searchParams.get('code'),binding=browserCookie(req);
 if(!state||!code||!binding)throw new Fault('invalid-login',401);
 const p=await consume(env,await hash(state),'login',t);
 if(p.binding!==await hash(binding))throw new Fault('invalid-login',401);
 const tr=await fetcher('https://github.com/login/oauth/access_token',{method:'POST',headers:{Accept:'application/json','Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({client_id:env.GITHUB_CLIENT_ID,client_secret:env.GITHUB_CLIENT_SECRET,code,redirect_uri:env.ORIGIN+'/callback'}),redirect:'manual',signal:AbortSignal.timeout(15000)});
 const token=await tr.json();
 // Empty scope is sufficient for the public numeric identity. Refuse unexpectedly broad grants.
 if(!tr.ok||!token.access_token||token.scope)throw new Fault('identity-exchange-failed',401);
 const ur=await fetcher('https://api.github.com/user',{headers:{Authorization:'Bearer '+token.access_token,Accept:'application/vnd.github+json','User-Agent':'private-knowledge-intake'},redirect:'manual',signal:AbortSignal.timeout(15000)});
 const user=await ur.json();
 if(!ur.ok||!Number.isSafeInteger(user.id)||!allowed(env,String(user.id)))throw new Fault('user-not-allowed',403);
 const consent=random();
 await putAuth(env,await hash(consent),'consent',{...p,user:String(user.id)},t+300);
 return new Response('<!doctype html><meta charset="utf-8"><title>Private capture consent</title><h1>Allow private proposal capture?</h1><p>This client may submit knowledge proposals. It cannot read your graph, approve, publish or delete knowledge.</p><form method="post" action="/consent"><input type="hidden" name="ticket" value="'+consent+'"><button name="decision" value="allow">Allow proposals</button><button name="decision" value="deny">Deny</button></form>',{headers:{'Content-Type':'text/html;charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",'X-Content-Type-Options':'nosniff'}});
 }
 if(path==='/consent'&&req.method==='POST'){
 if(req.headers.get('origin')!==env.ORIGIN)throw new Fault('invalid-origin',403);
 const form=new URLSearchParams(body),p=await consume(env,await hash(form.get('ticket')||''),'consent',t);
 if(p.binding!==await hash(browserCookie(req)||''))throw new Fault('invalid-consent',401);
 const dest=new URL(p.redirect_uri);dest.searchParams.set('state',p.state);
 if(form.get('decision')!=='allow'){dest.searchParams.set('error','access_denied');return go(dest.toString(),cookie(''));}
 const code=random();await putAuth(env,await hash(code),'code',p,t+120);dest.searchParams.set('code',code);return go(dest.toString(),cookie(''));
 }
 if(path==='/token'&&req.method==='POST'){
 const f=new URLSearchParams(body),client=f.get('client_id'),resource=f.get('resource');
 if(!clients(env)[client]||resource!==env.ORIGIN+'/mcp')throw new Fault('invalid-client-or-resource',401);
 if(f.get('grant_type')==='authorization_code'){
 const p=await consume(env,await hash(f.get('code')||''),'code',t),v=f.get('code_verifier')||'';
 const challenge=b64(await crypto.subtle.digest('SHA-256',bytes(v)));
 if(!/^[a-zA-Z0-9._~-]{43,128}$/.test(v)||challenge!==p.code_challenge||client!==p.client_id||f.get('redirect_uri')!==p.redirect_uri||!allowed(env,p.user))throw new Fault('invalid-grant',401);
 return issue(env,{user:p.user,client},t);
 }
 if(f.get('grant_type')==='refresh_token'){
 const id=await hash(f.get('refresh_token')||''),row=await one(env,"SELECT * FROM auth WHERE id=? AND kind='refresh'",id);
 if(!row)throw new Fault('invalid-grant',401);
 const p=JSON.parse(row.payload);
 if(p.client!==client)throw new Fault('invalid-client',401);
 if(row.used){await stmt(env,"INSERT OR IGNORE INTO auth VALUES(?,'revoked','{}',?,0)",p.family,p.max).run();throw new Fault('refresh-replay',401);}
 await consume(env,id,'refresh',t);
 if(p.max<=t||p.epoch!==(await active(env)).epoch||!allowed(env,p.user)||await one(env,"SELECT id FROM auth WHERE id=? AND kind='revoked'",p.family))throw new Fault('revoked',401);
 return issue(env,p,t);
 }
 throw new Fault('unsupported-grant');
 }
 throw new Fault('not-found',404);
}
