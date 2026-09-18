// Standard JWE compact RSA-OAEP-256/A256GCM. No home-grown cipher.
const enc=new TextEncoder();
export const bytes=s=>enc.encode(s);
export const b64=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
export const un64=s=>Uint8Array.from(atob(s.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));
export const random=()=>b64(crypto.getRandomValues(new Uint8Array(32)));
export async function hash(s){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes(s)))].map(x=>x.toString(16).padStart(2,'0')).join('');}
export async function mac(key,s){const raw=un64(key);if(raw.length<32)throw new Error('256-bit signing key required');const k=await crypto.subtle.importKey('raw',raw,{name:'HMAC',hash:'SHA-256'},false,['sign']);return b64(await crypto.subtle.sign('HMAC',k,bytes(s)));}
export async function verifyMac(key,s,sig){try{const k=await crypto.subtle.importKey('raw',un64(key),{name:'HMAC',hash:'SHA-256'},false,['verify']);return await crypto.subtle.verify('HMAC',k,un64(sig),bytes(s));}catch{return false;}}
export async function seal(text,jwk,kid){
 const header=b64(bytes(JSON.stringify({alg:'RSA-OAEP-256',enc:'A256GCM',kid})));
 const recipient=await crypto.subtle.importKey('jwk',jwk,{name:'RSA-OAEP',hash:'SHA-256'},false,['encrypt']);
 if(jwk.d||recipient.algorithm.modulusLength<2048)throw new Error('Public RSA key of at least 2048 bits required');
 const raw=crypto.getRandomValues(new Uint8Array(32)),iv=crypto.getRandomValues(new Uint8Array(12));
 const key=await crypto.subtle.importKey('raw',raw,'AES-GCM',false,['encrypt']);
 const encrypted=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:bytes(header),tagLength:128},key,bytes(text)));
 return [header,b64(await crypto.subtle.encrypt('RSA-OAEP',recipient,raw)),b64(iv),b64(encrypted.slice(0,-16)),b64(encrypted.slice(-16))].join('.');
}
export function canonical(x){if(Array.isArray(x))return '['+x.map(canonical).join(',')+']';if(x&&typeof x==='object')return '{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';return JSON.stringify(x);}
