const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class El{
 constructor(){this.value='';this.children=[];this.checked=false;this.files=[];}
 append(n){this.children.push(n);}replaceChildren(){this.children=[];}
}
const ids=Object.fromEntries([...fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new El()]));
const doc={schema:'yiban.memory-candidates.v2',sources:[{key:'s',text:'脱敏全文：公共活动。'}],entities:[],events:[],records:[{key:'r',title:'公共活动',summary:'公共活动。',evidence:[{text:'公共活动。'}]}]};
let calls=[],adopted=null,invalidated=0,hold=null;
const review={document:doc,notice:'检查全文',paragraphs:[{id:0,text:'公共活动。',private:false,required:false},{id:1,text:'私人联系电话13812345678',private:true,required:true}],
 private_candidates:[{title:'私人候选',summary:'私人资料'}],private_count:1,warnings:[]};
const context=vm.createContext({document:{getElementById:id=>ids[id],createElement:()=>new El()},
 fetch:async(path,data)=>{calls.push([path,data]);return{};},newTurnId:()=> 'synthetic-md-turn-12345',TextDecoder,console});
vm.runInContext(fs.readFileSync('static/md-import.js','utf8'),context);
const api={
 request:async(path,data)=>{
  calls.push([path,data]);if(path==='md/start')return{extraction_token:'draft-one',parts:1};
  if(path==='md/next'){if(hold)await hold;return{done:true};}
  if(path==='md/review')return structuredClone(review);
  if(path==='validate')return{document:data.document};
  if(path==='md/discard')return{discarded:true};throw Error(path);
 },
 work:async(fn)=>fn(()=>true,null),abort:()=>{},invalidate:()=>{invalidated++;adopted=null;},setDocument:r=>adopted=r.document,controls:()=>{}
};
context.api=api;
vm.runInContext('var md=createMarkdownImport(api)',context);
(async()=>{
 ids.v2MdText.value='公共活动。\n\n私人联系电话13812345678';
 await ids.v2MdExtract.onclick();
 assert.equal(adopted,null);assert.ok(calls.some(c=>c[0]==='md/review'));
 assert.ok(!calls.some(c=>/preview|confirm/.test(c[0])),'extraction cannot save or prepare a write');
 assert.ok(ids.v2MdReview.children.length);
 let error=null;try{await ids.v2MdAccept.onclick();}catch(e){error=e;}assert.ok(error,'must inspect redacted full source first');
 ids.v2MdConsent.checked=true;await ids.v2MdAccept.onclick();
 assert.equal(adopted.sources[0].text,doc.sources[0].text);
 assert.ok(!JSON.stringify(adopted).includes('13812345678'));
 vm.runInContext('md.controls(true)',context);assert.equal(ids.v2MdExtract.disabled,true);
 vm.runInContext('md.clear()',context);
 assert.equal(ids.v2MdText.value,'');assert.equal(ids.v2MdReview.children.length,0);assert.equal(ids.v2MdConsent.checked,false);
 assert.ok(calls.some(c=>c[0]==='md/discard'));
 ids.v2MdFiles.files=[{name:'picture.png'}];await assert.rejects(ids.v2MdExtract.onclick(),/仅支持MD/);
 ids.v2MdFiles.files=[];ids.v2MdText.value='公共活动。';let release;hold=new Promise(r=>release=r);
 const running=ids.v2MdExtract.onclick();await new Promise(setImmediate);ids.v2MdStop.onclick();release();await running;
 assert.equal(adopted,null);assert.ok(calls.some(c=>c[0]==='/api/turn/cancel'));
 console.log('MD frontend passed: extraction does not save, private full source isolated, human redaction consent, logout cleanup, limits and cancellation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
