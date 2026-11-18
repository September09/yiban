// Logic-level checks with DOM/audio stubs; this does not launch or control a browser.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
class Element{
  constructor(){this.children=[];this.dataset={};this.style={};this.textContent='';this.className='';this.hidden=false;this.checked=false;this.disabled=false;this.value='';this.classList={toggle(){},remove(){}};}
  scrollIntoView(){}focus(){}click(){this.clicked=true;}remove(){this.removed=true;}
  append(...children){this.children.push(...children);}replaceChildren(){this.children=[];}
  setAttribute(k,v){this[k]=v;}querySelector(){return new Element();}
  querySelectorAll(selector){const all=this.children.flatMap(c=>[c,...(c.querySelectorAll?.('input')||[])]).filter(c=>c.type==='checkbox');return selector.includes(':checked')?all.filter(c=>c.checked):all;}
  animate(frames,options){this.animation={frames,options};return{cancel(){}};}
}
const ids=Object.fromEntries([...fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const calls=[],saved={};let micCalls=0,heldReply=null,sharedFailure=false;
const sharedResult={answer:'第二届负责人是角色乙。',sources:[{id:'mem',scope_kind:'team',memory_version:2,content:'第二届负责人是角色乙。',status:'valid',source_note:'虚构资料',event_date:'2026-01-01',reference:1,version:1,has_evidence:true}],answer_source_ids:['mem'],source_usage:'model_reported',conversation:{id:'topic-test',choices:[]},agent:{status:'done',steps:[]}};
const context=vm.createContext({
  document:{getElementById:id=>{assert.ok(ids[id],id);return ids[id];},createElement:()=>new Element(),createTextNode:text=>({textContent:text}),querySelectorAll:()=>[],body:{className:''}},
  localStorage:{getItem:k=>saved[k]??null,setItem:(k,v)=>saved[k]=v,removeItem:k=>delete saved[k]},
  window:{matchMedia:()=>({matches:false}),addEventListener(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>{micCalls++;throw new Error('No device in logic test');}}},
  fetch:async(path,options)=>{
    if(path==='/api/audio/devices')return{ok:true,json:async()=>({available:false,devices:[],message:'模拟平台无系统元数据'})};
    if(path==='/api/diagnostics/trace')return{ok:true,json:async()=>({steps:[],available:true})};
    calls.push({path,data:options?.body?JSON.parse(options.body):null,headers:options?.headers});
    if(path==='/api/shared/agent/ask'||path==='/api/memories/ask'){if(heldReply)await heldReply;return sharedFailure?{ok:false,status:401,json:async()=>({error:'会话已失效'})}:{ok:true,json:async()=>sharedResult};}
    if(path==='/api/memories/evidence')return{ok:true,json:async()=>({available:true,quotes:[{location:'原文第1行',text:'虚构原文摘录'}]})};
    if(path==='/api/memories/reset'||path==='/api/shared/ask/reset'||path==='/api/shared/auth/logout')return{ok:true,json:async()=>({})};
    if(path==='/api/status')return{json:async()=>({chat:true,asr:true,wake:true,vision:true})};
    if(path==='/api/turn/cancel')return{ok:true,json:async()=>({cancelled:true})};
    if(path==='/api/profiles/list')return{ok:true,json:async()=>({profiles:[]})};
    if(path==='/api/memory/list')return{ok:true,json:async()=>({memories:[]})};
    if(path==='/api/chat'){
      let consumed=false;return{ok:true,body:{getReader:()=>({read:async()=>consumed?{done:true}:(consumed=true,{done:false,value:new TextEncoder().encode(JSON.stringify({message:{content:'已收到'},done:true})+'\n')})})}};
    }
    throw new Error('Unexpected fetch '+path);
  },
  TextDecoder,TextEncoder,Float32Array,Uint8Array,ArrayBuffer,DataView,AbortController,crypto:require('node:crypto').webcrypto,
  performance:{now:()=>0},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},
  console,URL,Blob, btoa:s=>Buffer.from(s,'binary').toString('base64')
});
(async()=>{
  vm.runInContext(fs.readFileSync('static/conversation-diagnostics.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/personal.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/personal-retrieval.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/memory-import.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/memory-review.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/personal-state.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-answer-sources.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-agent.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-replacement.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-audit.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-feedback.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-backup.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-recovery.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-batches.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-voice.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-evidence.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/material-import.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/speech-settings.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/audio-devices.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/diagnostic-overview.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/model-settings.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/workspace.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/companion-memory.js','utf8'),context);


  await new Promise(r=>setImmediate(r));
  const run=s=>vm.runInContext(s,context),bubble=()=>ids.messages.children.at(-1)?.children[1]?.textContent;
  assert.equal(run('companionMemory.mode'),'auto');
  run("activeProfile='profile-a';messages=[{role:'user',content:'私人聊天内容'},{role:'assistant',content:'私人回答'}];persist();render();");
  const original=JSON.stringify(saved);
  run("sharedState.token='test-token';sharedState.account={id:'test-owner',name:'测试账号'};sharedState.spaces=[{id:'team-a',name:'甲团队',version:1},{id:'team-b',name:'乙团队',version:1}];sharedState.space=null;sharedControls();");
  assert.equal(run('companionMemoryActive()'),true,'login automatically enables joint memory without selecting a space');
  assert.equal(ids.chatMemorySpace.hidden,true);assert.equal(ids.send.disabled,false);
  sharedResult.sources[0].origin='shared';sharedResult.sources[0].scope_name='团队 · 甲团队';
  sharedResult.sources[0].space_id='team-a';sharedResult.sources[0].record_id='raw-memory-id';
  await run("send('自动查询的测试问题')");
  const request=calls.find(c=>c.path==='/api/memories/ask');
  assert.ok(request);assert.equal(request.headers.Authorization,'Bearer test-token');
  assert.equal(request.data.profile_id,undefined,'称呼档案不参与账号授权');assert.equal(request.data.space_id,undefined);
  assert.equal(request.data.messages,undefined);assert.ok(!JSON.stringify(request.data).includes('私人聊天内容'));
  assert.equal(bubble(),sharedResult.answer);assert.equal(JSON.stringify(saved),original);
  const card=ids.messages.children.at(-1).children[2].children[1].children[0];
  assert.match(card.children[0].textContent,/团队 · 甲团队/);
  await card.children[3].onclick();
  const evidence=calls.find(c=>c.path==='/api/memories/evidence');
  assert.equal(evidence.data.space_id,'team-a');assert.equal(evidence.data.id,'raw-memory-id');
  sharedResult.space_choices=[{id:'team-b',label:'乙团队'}];
  await run("send('另一个问题')");
  const choices=ids.messages.children.at(-1).children[3];await choices.children[0].onclick();
  assert.equal(calls.filter(c=>c.path==='/api/memories/ask').at(-1).data.space_choice,'team-b');
  delete sharedResult.space_choices;
  ids.clear.onclick();assert.equal(JSON.stringify(saved),original);
  let release;heldReply=new Promise(resolve=>release=resolve);
  const late=run("send('退出前的慢问题')");await new Promise(r=>setImmediate(r));
  run('sharedLock()');release();await late;heldReply=null;
  assert.equal(run('busy'),false);assert.equal(bubble(),'私人回答','logout must restore personal history and remove all team results');
  assert.ok(!ids.messages.children.some(row=>row.children[1]?.textContent===sharedResult.answer));
  assert.equal(JSON.stringify(saved),original);
  assert.equal(micCalls,0);assert.equal(calls.filter(c=>/\/api\/(speak|listen|transcribe)/.test(c.path)).length,0);
  console.log('Automatic memory frontend passed: default auto, no selected team required, profile-independent account scope, no chat-history export/storage, scoped evidence, team clarification, logout late-response isolation and no audio.');
})().catch(error=>{console.error(error);process.exitCode=1;});
