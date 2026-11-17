const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Node{constructor(){this.children=[];this.value='';this.checked=false;this.hidden=false;this.disabled=false;this.dataset={};this.files=[];this.textContent='';}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}querySelectorAll(){return [];}setAttribute(k,v){this[k]=v;}}
const nodes={};for(const m of fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g))nodes[m[1]]=new Node();
const calls=[];let callback=null;
const result={mode:'manual',batch_name:'人工审阅',sources:[{name:'原文',text:'原文摘录',warnings:[]}],candidates:[{review_key:'M001',content:'审阅后的事实',evidence:'原文摘录',source_name:'原文',event_date:'',status:'valid',status_reason:'测试已确认',source_note:'来源1',evidence_ref:{draft:'test',candidate:0}}],notice:'尚未入库'};
const context=vm.createContext({console,AbortController,crypto:require('node:crypto').webcrypto,
 document:{getElementById:id=>nodes[id],createElement:()=>new Node(),createTextNode:text=>Object.assign(new Node(),{textContent:text})},$:id=>nodes[id],
 sharedState:{epoch:1,space:{id:'space1',name:'测试空间',role:'owner'},account:{id:'owner',name:'owner'},members:[],token:'mock-only',busy:false,asking:false},
 sharedControls:()=>{},sharedVoiceActive:()=>false,sharedStatusNames:{valid:'有效',pending:'待核实',outdated:'过时'},
 fetch:async()=>({}),sharedApi:async(path,body)=>{calls.push({path,body});if(callback)return callback(path,body);return result;}});
vm.runInContext(fs.readFileSync('static/material-import.js','utf8'),context);
const call=code=>vm.runInContext(code,context);
(async()=>{
 const packet={schema:'yiban.reviewed-materials.v1'};nodes.materialVisibility.value='selected';nodes.materialPolicy.value='author';
 nodes.materialReviewedFile.files=[{size:80,text:async()=>JSON.stringify(packet)}];await nodes.materialReviewedFile.onchange();
 assert.equal(calls[0].path,'/import/reviewed');assert.equal(calls[0].body.space_id,'space1');
 assert.equal(call('materialState.candidates[0].content'),'审阅后的事实');assert.equal(call('materialState.candidates[0].status'),'valid');
 assert.equal(nodes.materialVisibility.value,'selected','file must not widen audience');assert.equal(nodes.materialPolicy.value,'author');
 assert.equal(nodes.materialKeepEvidence.checked,true);assert.equal(nodes.materialOrganize.disabled,true);assert.equal(nodes.materialConsent.checked,false);
 assert.ok(!calls.some(c=>c.path.endsWith('/confirm')),'loading cannot confirm');
 nodes.materialReviewedFile.files=[{size:2,text:async()=>'{broken'}];await nodes.materialReviewedFile.onchange();assert.equal(call('materialState.candidates.length'),1);assert.match(nodes.materialStatus.textContent,/无法读取/);
 let release;callback=async path=>path==='/import/reviewed'?new Promise(resolve=>release=resolve):({discarded:true});
 nodes.materialReviewedFile.files=[{size:80,text:async()=>JSON.stringify(packet)}];const pending=nodes.materialReviewedFile.onchange();
 await new Promise(setImmediate);call('materialStop();materialReset();sharedState.epoch++;sharedState.space={id:"space2",role:"owner"};');release(result);await pending;
 assert.equal(call('materialState.candidates.length'),0,'late file response cannot restore old-space material');
 assert.ok(!calls.some(c=>c.path.endsWith('/confirm')));
 console.log('Reviewed import frontend: explicit load, no automatic write/permission change, state retention, bad-file and late-space isolation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
