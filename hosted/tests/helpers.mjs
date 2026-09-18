import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {random} from '../src/crypto.mjs';
import {handle} from '../src/worker.mjs';
export class D1 {
 constructor(){this.db=new DatabaseSync(':memory:');this.db.exec(readFileSync(new URL('../migrations/0001.sql',import.meta.url),'utf8'));}
 prepare(sql){const db=this.db;let args=[];return {bind(...a){args=a;return this;},async first(){return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args),meta:{size_after:db.prepare('PRAGMA page_count').get().page_count*db.prepare('PRAGMA page_size').get().page_size}};},async run(){return db.prepare(sql).run(...args);}};}
 async batch(stmts){this.db.exec('BEGIN');try{const result=[];for(const s of stmts)result.push(await s.run());this.db.exec('COMMIT');return result;}catch(e){this.db.exec('ROLLBACK');throw e;}}
}
export async function setup(){
 const keys=await crypto.subtle.generateKey({name:'RSA-OAEP',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['encrypt','decrypt']);
 const env={DB:new D1(),ORIGIN:'https://intake.test',ENABLED:'true',BILLING_MODE:'free-only',ALLOWED_GITHUB_IDS:'["12345"]',OAUTH_CLIENTS:JSON.stringify({synthetic:{redirect_uris:['https://client.test/callback']}}),GITHUB_CLIENT_ID:'synthetic',GITHUB_CLIENT_SECRET:'synthetic-not-real',RECIPIENT_JWK:JSON.stringify(await crypto.subtle.exportKey('jwk',keys.publicKey)),RECIPIENT_KEY_ID:'test-rsa',RECEIPT_KEY:random(),ENVELOPE_KEY:random(),ENVELOPE_KEY_ID:'test-hmac',SYNC_KEYS:JSON.stringify({computer:random()})};
 env.DB.db.exec('UPDATE control SET enabled=1');
 env.RECEIPT_KEY_ID='receipt-v1';env.RECEIPT_KEYS=JSON.stringify({'receipt-v1':env.RECEIPT_KEY});delete env.RECEIPT_KEY;
 return {env,keys};
}
export const example=()=>({version:1,conversationId:'synthetic-chat-1',topic:'Juniper',sourceTimestamp:'2026-09-16T12:00:00Z',entities:[{id:'ava',label:'Ava Example',type:'person'},{id:'juniper',label:'Juniper',type:'project'}],claims:[{id:'format-1',type:'preference',subject:'juniper',scope:'notes',predicate:'format',value:'Use structured tables for Juniper notes.',speaker:{role:'user',identity:'ava'},confidence:0.9,authority:'owner',materiality:{criterion:'preference',durability:'durable',reason:'A reusable documentation preference.'},owner:'ava',due:{date:null,time:null,timezone:'UTC',ambiguity:'none'},supersedes:null,relationship:null,sourceRef:'synthetic-chat-1/message-1',evidence:'I prefer structured tables for Juniper notes.',uncertainty:[]}]});
export const request=(env,path,options={},fetcher)=>env.dispatch?env.dispatch(env.ORIGIN+path,options):handle(new Request(env.ORIGIN+path,options),env,fetcher);
export async function login(env,user=12345){
 const verifier='v'.repeat(43),challenge=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))).toString('base64url');
 const p=new URLSearchParams({client_id:'synthetic',redirect_uri:'https://client.test/callback',resource:env.ORIGIN+'/mcp',scope:'capture:write',response_type:'code',code_challenge_method:'S256',code_challenge:challenge,state:'synthetic-state'});
 const start=await request(env,'/authorize?'+p),cookie=start.headers.get('set-cookie').split(';')[0],state=new URL(start.headers.get('location')).searchParams.get('state');
 const fetcher=async url=>new Response(JSON.stringify(url.includes('access_token')?{access_token:'synthetic-provider-token',scope:''}:{id:user,login:'not-an-identity'}),{headers:{'Content-Type':'application/json'}});
 const callback=await request(env,'/callback?'+new URLSearchParams({state,code:'synthetic'}),{headers:{Cookie:cookie}},fetcher);
 if(callback.status!==200)return {callback};
 const text=await callback.text(),ticket=text.match(/name="ticket" value="([^"]+)"/)[1];
 const consent=await request(env,'/consent',{method:'POST',headers:{Cookie:cookie,Origin:env.ORIGIN},body:new URLSearchParams({ticket,decision:'allow'}).toString()});
 const code=new URL(consent.headers.get('location')).searchParams.get('code');
 const tokenArgs={grant_type:'authorization_code',code,client_id:'synthetic',resource:env.ORIGIN+'/mcp',redirect_uri:'https://client.test/callback',code_verifier:verifier};
 const response=await request(env,'/token',{method:'POST',body:new URLSearchParams(tokenArgs).toString()});
 return {tokens:await response.json(),tokenArgs,response};
}
export async function rpc(env,token,method,params={},id=1){
 return request(env,'/mcp',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json',Accept:'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'},body:JSON.stringify({jsonrpc:'2.0',id,method,params})});
}
