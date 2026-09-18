import schema from '../agent-schema.json' with {type:'json'};
import {Fault} from './storage.mjs';
import {canonical,bytes} from './crypto.mjs';
function need(ok){if(!ok)throw new Fault('malformed');}
function shape(v,s){
 const type=v===null?'null':Array.isArray(v)?'array':typeof v;
 need((Array.isArray(s.type)?s.type:[s.type]).includes(type));if(s.enum)need(s.enum.includes(v));
 if(type==='object'){need(Object.keys(v).every(k=>Object.hasOwn(s.properties,k)));need(s.required.every(k=>Object.hasOwn(v,k)));for(const k of Object.keys(v))shape(v[k],s.properties[k]);}
 if(type==='array')for(const x of v)shape(x,s.items);
 if(type==='string')need(v.trim().length>0&&Array.from(v).length<=600&&v.isWellFormed());
 if(type==='number')need(Number.isFinite(v));
}
export function validate(d){
 shape(d,schema);need(bytes(canonical(d)).length<=32768);
 need(/T.*(Z|[+-]\d\d:\d\d)$/.test(d.sourceTimestamp)&&Number.isFinite(Date.parse(d.sourceTimestamp)));
 const sourceDate=d.sourceTimestamp.slice(0,10);need(new Date(sourceDate).toISOString().slice(0,10)===sourceDate);
 need(d.entities.length>=1&&d.entities.length<=30&&d.claims.length>=1&&d.claims.length<=20);
 const es=new Map(d.entities.map(e=>[e.id,e]));need(es.size===d.entities.length&&new Set(d.claims.map(c=>c.id)).size===d.claims.length);
 for(const c of d.claims){
 need(es.has(c.subject)&&c.confidence>=0&&c.confidence<=1);
 for(const r of [c.owner,c.speaker.identity])need(r===null||es.get(r)?.type==='person');
 if(c.type==='decision')need(c.owner&&c.speaker.role==='user');
 if(['decision','change','commitment','ownership','risk'].includes(c.type))need(c.materiality.criterion!=='context');
 need(c.evidence.length<=400&&c.materiality.reason.length>=15&&c.uncertainty.length<=3&&c.uncertainty.every(f=>f.reason.length>=15));
 if(c.type==='commitment'&&!c.owner)need(c.uncertainty.some(f=>f.field==='owner'));
 if(c.due.date){need(/^\d{4}-\d{2}-\d{2}$/.test(c.due.date)&&Number.isFinite(Date.parse(c.due.date))&&new Date(c.due.date).toISOString().slice(0,10)===c.due.date&&c.due.timezone);}
 if(c.due.time)need(c.due.date&&/^([01]\d|2[0-3]):[0-5]\d$/.test(c.due.time));
 if(c.due.ambiguity!=='none')need(c.uncertainty.some(f=>f.field==='due'));
 if(c.type==='change')need(c.supersedes);
 need((c.type==='relationship')===(c.relationship!==null));
 if(c.relationship){const r=c.relationship;need(es.has(r.source)&&es.has(r.target)&&r.source!==r.target);
 if(r.type==='owner-of')need(es.get(r.source).type==='person'&&['entity','project','system'].includes(es.get(r.target).type));
 else need([r.source,r.target].every(x=>['entity','project','system'].includes(es.get(x).type)));}
 }return d;
}
// Parse JSON without allowing duplicate keys, nonfinite values or deep nesting.
export function strict(text){
 let i=0;const ws=()=>{while(/\s/.test(text[i]||'')&&i<text.length)i++;};
 function string(){const start=i++;while(i<text.length){if(text[i]==='\\'){i+=2;continue;}if(text[i++]==='"')return JSON.parse(text.slice(start,i));}throw new Fault('malformed');}
 function value(depth=0){need(depth<32);ws();const c=text[i];
 if(c==='"')return string();
 if(c==='{'||c==='['){i++;ws();const obj=c==='{',out=obj?Object.create(null):[],end=obj?'}':']';if(text[i]===end){i++;return out;}
 while(true){ws();if(obj){need(text[i]==='"');const k=string();need(!Object.hasOwn(out,k));ws();need(text[i++]===':');out[k]=value(depth+1);}else out.push(value(depth+1));
 ws();if(text[i]===end){i++;return out;}need(text[i++ ]===',');}}
 const m=/^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(i));need(m);i+=m[0].length;const v=JSON.parse(m[0]);need(typeof v!=='number'||Number.isFinite(v));return v;
 }try{const out=value();ws();need(i===text.length);return out;}catch{throw new Fault('malformed');}
}
export {schema};
