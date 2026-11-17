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
const sharedResult={answer:'第二届负责人是角色乙。',sources:[{id:'mem',content:'第二届负责人是角色乙。',status:'valid',source_note:'虚构资料',event_date:'2026-01-01',reference:1,version:1,has_evidence:true}],answer_source_ids:['mem'],source_usage:'model_reported',conversation:{id:'topic-test',choices:[]},agent:{status:'done',steps:[]}};
const context=vm.createContext({
  document:{getElementById:id=>{assert.ok(ids[id],id);return ids[id];},createElement:()=>new Element(),createTextNode:text=>({textContent:text}),querySelectorAll:()=>[],body:{className:''}},
  localStorage:{getItem:k=>saved[k]??null,setItem:(k,v)=>saved[k]=v,removeItem:k=>delete saved[k]},
  window:{matchMedia:()=>({matches:false}),addEventListener(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>{micCalls++;throw new Error('No device in logic test');}}},
  fetch:async(path,options)=>{
    if(path==='/api/audio/devices')return{ok:true,json:async()=>({available:false,devices:[],message:'模拟平台无系统元数据'})};
    if(path==='/api/diagnostics/trace')return{ok:true,json:async()=>({steps:[],available:true})};
    calls.push({path,data:options?.body?JSON.parse(options.body):null,headers:options?.headers});
    if(path==='/api/shared/agent/ask'){if(heldReply)await heldReply;return sharedFailure?{ok:false,status:401,json:async()=>({error:'会话已失效'})}:{ok:true,json:async()=>sharedResult};}
    if(path==='/api/shared/memories/evidence')return{ok:true,json:async()=>({available:true,quotes:[{location:'原文第1行',text:'虚构原文摘录'}]})};
    if(path==='/api/shared/ask/reset'||path==='/api/shared/auth/logout')return{ok:true,json:async()=>({})};
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
  run("messages=[{role:'user',content:'私人的茶叶偏好'},{role:'assistant',content:'私人回答'}];persist();render();");
  const personal=JSON.stringify(saved);
  const login=()=>run("sharedState.token='synthetic-token';sharedState.account={id:'owner',name:'测试账号'};sharedState.spaces=[{id:'space-a',version:1,name:'虚构团队',role:'owner',kind:'team'}];sharedState.space=sharedState.spaces[0];sharedControls();");
  login();ids.autoplay.checked=true;
  await run("companionMemorySetMode('shared')");
  assert.equal(ids.mic.disabled,true);assert.equal(ids.wake.disabled,true);assert.equal(ids.autoplay.disabled,true);
  assert.equal(ids.chatMemorySpace.hidden,false);assert.match(ids.chatMemoryHint.textContent,/虚构团队/);
  await run("send('第二届负责人是谁？')");
  let request=calls.find(c=>c.path==='/api/shared/agent/ask');
  assert.ok(request);assert.equal(request.headers.Authorization,'Bearer synthetic-token');
  assert.equal(request.data.space_id,'space-a');assert.equal(request.data.query,'第二届负责人是谁？');
  assert.equal(request.data.messages,undefined);assert.equal(request.data.profile_id,undefined);
  assert.ok(!JSON.stringify(request.data).includes('私人的茶叶偏好'));
  assert.equal(bubble(),'第二届负责人是角色乙。');
  assert.equal(JSON.stringify(saved),personal,'shared turns must not enter personal history or storage');
  assert.equal(calls.filter(c=>c.path==='/api/chat').length,0);
  assert.equal(calls.filter(c=>c.path==='/api/speak').length,0,'shared text must remain silent even if autoplay checked');
  const details=ids.messages.children.at(-1).children[2],card=details.children[1].children[0];
  assert.match(details.children[0].textContent,/实际引用 1 条/);
  await card.children[3].onclick();assert.match(card.children[4].textContent,/虚构原文摘录/);
  await run("send('那第二届是什么时候？')");
  assert.equal(calls.filter(c=>c.path==='/api/shared/agent/ask').at(-1).data.conversation_id,'topic-test');
  ids.clear.onclick();assert.equal(JSON.stringify(saved),personal,'new shared topic must not erase personal history');
  assert.equal(run('companionMemory.conversation'),null);
  await run("companionMemorySetMode('personal')");
  assert.equal(bubble(),'私人回答');assert.equal(ids.autoplay.disabled,false);
  await run("companionMemorySetMode('shared')");
  let stopRelease;heldReply=new Promise(resolve=>stopRelease=resolve);
  const stopped=run("send('等待停止的问题')");await new Promise(r=>setImmediate(r));
  await run('stopInteraction()');
  assert.equal(run('busy'),false);assert.equal(ids.sharedRefresh.disabled,false,'stop must release management controls');
  stopRelease();await stopped;heldReply=null;
  let release;heldReply=new Promise(resolve=>release=resolve);
  const pending=run("send('一个慢问题')");await new Promise(r=>setImmediate(r));
  run('sharedLock()');release();await pending;heldReply=null;
  assert.ok(!ids.messages.children.some(row=>row.children[1]?.textContent==='第二届负责人是角色乙。'),'logout clears sources and discards late answers');
  assert.equal(run('busy'),false);assert.equal(run('companionMemory.conversation'),null);
  login();
  let release2;heldReply=new Promise(resolve=>release2=resolve);
  const pending2=run("send('另一个慢问题')");await new Promise(r=>setImmediate(r));
  run("sharedClearSpace();sharedState.space={id:'space-b',version:1,name:'另一个团队',role:'owner',kind:'team'};companionMemorySync();");
  release2();await pending2;heldReply=null;
  assert.match(bubble(),/另一个团队/);assert.equal(run('busy'),false);
  sharedFailure=true;await run("send('会话过期问题')");sharedFailure=false;
  assert.equal(run('sharedState.token'),null);assert.equal(run('busy'),false);
  assert.ok(!ids.messages.children.some(row=>row.children[1]?.textContent==='第二届负责人是角色乙。'));
  assert.equal(JSON.stringify(saved),personal);
  assert.equal(micCalls,0);
  assert.ok(!calls.some(c=>/preview|confirm|\/voice\//.test(c.path)),'the front chat must only query');
  console.log('Companion memory checks passed: read-only routing, scope, citations, excerpts, personal-history isolation, logout, stale responses, expiry, cancellation and silence.');
})().catch(error=>{console.error(error);process.exitCode=1;});
