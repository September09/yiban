// Text-only DOM simulation: preview, consent, dirty drafts and profile-switch races.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{constructor(tag=''){this.tag=tag;this.children=[];this.value='';this.textContent='';this.disabled=false;this.checked=false;this.hidden=false;}append(...x){this.children.push(...x);}replaceChildren(){this.children=[];}setAttribute(k,v){this[k]=v;}}
const ids=Object.fromEntries([...fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const a={id:'left',content:'居住城市：南京',status:'pending',event_date:'',status_reason:'',revision:1};
const b={id:'right',content:'我住在上海',status:'pending',event_date:'',status_reason:'',revision:2};
const plan={kind:'conflict',token:'synthetic-pair-token',target:{id:'a',name:'虚构甲'},action:'prefer',changes:[{before:a,after:{...a,status:'valid',status_reason:'已核对'}},{before:b,after:{...b,status:'outdated',status_reason:'已核对'}}]};
const requests=[];let writes=0,late=false,resolveLate;
const context=vm.createContext({document:{createElement:tag=>new Element(tag)},$:id=>ids[id],console,
 api:async(path,data)=>{requests.push({path,data});let result={changed:true};if(path==='/api/memory/conflicts/preview'){if(late)return await new Promise(r=>resolveLate=()=>r({json:async()=>plan}));result=plan;}if(path.endsWith('/confirm'))writes++;return{json:async()=>result};},
 memoryTime:x=>x||'未记录',notice(){},showWorkspacePage(){},resetMemoryEditor(){},refreshMemories:async()=>{},personalMemoryCancel(){},personalMemoryControls(){},personalQueryState:{running:false}});
vm.runInContext("let activeProfile='a',memoryBusy=false,memoryEdit=null;function memoryLocked(){return memoryBusy;}function personalControls(){memoryReviewControls();if(typeof personalStateControls==='function')personalStateControls();}function renderMemories(){}",context);
const source=fs.readFileSync('static/personal.js','utf8');vm.runInContext(source.slice(source.indexOf('const personalStatusNames'),source.indexOf('let profiles=')),context);
vm.runInContext(fs.readFileSync('static/memory-review.js','utf8'),context);
vm.runInContext(fs.readFileSync('static/personal-state.js','utf8'),context);
context.pairs={pairs:[{left:a,right:b,field:'居住城市',reason:'疑似不同'}],total:1,limited:false};
const call=code=>vm.runInContext(code,context);
function form(){const box=ids.personalConflictItems.children[0];return{choice:box.children.find(x=>x.tag==='select'),reason:box.children.find(x=>x.tag==='input'),button:box.children.find(x=>x.tag==='button')};}
(async()=>{
 call('personalConflictRender(pairs)');let f=form();await f.button.onclick();assert.equal(requests.length,0,'missing choice and reason cannot preview');
 f.choice.value='left';f.reason.value='已核对';await f.button.onclick();
 assert.equal(writes,0);assert.equal(ids.memoryReviewPanel.hidden,false);assert.equal(ids.confirmMemoryReview.disabled,true);
 const text=JSON.stringify(ids.memoryReviewDiff.children);assert.ok(text.includes('南京')&&text.includes('上海')&&text.includes('过时'));
 await ids.confirmMemoryReview.onclick();assert.equal(writes,0);
 ids.memoryReviewConsent.checked=true;await ids.confirmMemoryReview.onclick();assert.equal(writes,1);assert.equal(requests.find(x=>x.path.endsWith('/conflicts/confirm')).data.confirmed,true);
 call('personalConflictRender(pairs)');f=form();f.choice.value='right';f.reason.value='新核对';await f.button.onclick();ids.memoryReviewConsent.checked=true;
 f.reason.oninput();assert.equal(ids.memoryReviewPanel.hidden,true);assert.equal(ids.memoryReviewConsent.checked,false);assert.equal(writes,1);
 // A late preview for another profile cannot reappear or write anything.
 late=true;const pending=f.button.onclick();await new Promise(r=>setImmediate(r));call("activeProfile='b';personalConflictClear()");resolveLate();await pending;
 assert.equal(ids.memoryReviewPanel.hidden,true);assert.equal(writes,1);assert.ok(requests.some(x=>x.path.endsWith('/review/cancel')));
 context.statePlan={kind:'save',token:'synthetic-state-token',target:{id:'a',name:'虚构甲'},original:a,content:a.content,status:'valid',status_reason:'已核对',event_date:'',conflicts:[],can_append:false,stale:false};
 call("activeProfile='a';showMemoryReview(statePlan)");assert.equal(ids.memoryReviewChoice.value,'left');assert.equal(ids.memoryReviewConsent.checked,false);
 ids.memoryState.value='valid';ids.memoryStateReason.value='已核对';call("memoryEdit='left'");ids.memoryEventDate.oninput();
 assert.equal(ids.memoryState.value,'pending');assert.equal(ids.memoryStateReason.value,'');assert.equal(ids.memoryReviewPanel.hidden,true);
 assert.ok(requests.every(x=>x.path.startsWith('/api/memory/')),'no capture, playback or cloud request');
 console.log('Personal state frontend passed: diff preview, consent, dirty-state reset, profile isolation and late preview cancellation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
