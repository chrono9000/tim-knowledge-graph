export class Fault extends Error {constructor(code,status=400){super(code);this.status=status;}}
export const stmt=(env,sql,...args)=>env.DB.prepare(sql).bind(...args);
export const one=(env,sql,...args)=>stmt(env,sql,...args).first();
export const now=()=>Math.floor(Date.now()/1000);
export async function active(env){
 if(env.ENABLED!=='true'||env.BILLING_MODE!=='free-only')throw new Fault('disabled',503);
 const c=await one(env,'SELECT * FROM control WHERE id=1');if(!c?.enabled)throw new Fault('disabled',503);return c;
}
export async function limit(env,id,max,seconds,t=now()){
 const key=id+':'+Math.floor(t/seconds);
 const row=await stmt(env,'INSERT INTO rates VALUES(?,1,?) ON CONFLICT(id) DO UPDATE SET n=n+1 WHERE n<? RETURNING n',key,t+seconds,max).first();
 if(!row)throw new Fault('rate-limit',429);
}
export async function event(env,kind,ref,t=now()){await stmt(env,'INSERT INTO audit(at,kind,ref) VALUES(?,?,?)',t,kind,ref).run();}
export async function putAuth(env,id,kind,payload,expires){await stmt(env,'INSERT INTO auth VALUES(?,?,?,?,0)',id,kind,JSON.stringify(payload),expires).run();}
export async function consume(env,id,kind,t=now()){
 const r=await stmt(env,'UPDATE auth SET used=1 WHERE id=? AND kind=? AND used=0 AND expires>? RETURNING payload',id,kind,t).first();
 if(!r)throw new Fault('invalid-or-replayed-credential',401);return JSON.parse(r.payload);
}
export async function maintenance(env,t=now()){
 // Operator-run only; no scheduled handler exported. Expired items remain tombstones.
 await env.DB.batch([
 stmt(env,"INSERT INTO audit(at,kind,ref) SELECT ?,CASE WHEN ack IS NULL THEN 'expired-undelivered' ELSE 'payload-deleted' END,id FROM queue WHERE envelope IS NOT NULL AND (expires<=? OR ack<=?)",t,t,t-86400),
 stmt(env,'UPDATE queue SET envelope=NULL WHERE envelope IS NOT NULL AND (expires<=? OR ack<=?)',t,t-86400),
 stmt(env,'DELETE FROM queue WHERE envelope IS NULL AND received<?',t-365*86400),
 stmt(env,'DELETE FROM nonces WHERE expires<?',t),stmt(env,'DELETE FROM rates WHERE expires<?',t),
 stmt(env,'DELETE FROM auth WHERE expires<?',t-86400),
 stmt(env,'DELETE FROM audit WHERE at<?',t-30*86400)]);
}

