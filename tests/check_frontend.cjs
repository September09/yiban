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
const calls=[],saved={};let micCalls=0;
const context=vm.createContext({
  document:{getElementById:id=>{assert.ok(ids[id],id);return ids[id];},createElement:()=>new Element(),createTextNode:text=>({textContent:text}),querySelectorAll:()=>[],body:{className:''}},
  localStorage:{getItem:k=>saved[k]??null,setItem:(k,v)=>saved[k]=v,removeItem:k=>delete saved[k]},
  window:{matchMedia:()=>({matches:false}),addEventListener(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>{micCalls++;throw new Error('No device in logic test');}}},
  fetch:async(path,options)=>{
    if(path==='/api/audio/devices')return{ok:true,json:async()=>({available:false,devices:[],message:'模拟平台无系统元数据'})};
    if(path==='/api/diagnostics/trace')return{ok:true,json:async()=>({steps:[],available:true})};
    calls.push({path,data:options?.body?JSON.parse(options.body):null});
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
  await new Promise(r=>setImmediate(r));
  assert.equal(micCalls,0,'page load must not acquire media');
  assert.equal(vm.runInContext("isVisualRequest('请向左看看')",context),false);
  assert.equal(vm.runInContext("isVisualRequest('帮我看看这是什么')",context),true);
  assert.equal(vm.runInContext("isVisualRequest('不要拍照')",context),false);
  assert.equal(vm.runInContext("isVisualRequest('请看看能不能认出我的声音')",context),false,'voice identification must not open camera');
  assert.equal(vm.runInContext("isVisualRequest('帮我看看我是谁')",context),false);
  assert.equal(vm.runInContext("isVisualRequest('看看照片里我是谁')",context),true,'explicit image requests remain visual');
  await vm.runInContext("send('请向左看看')",context);
  assert.equal(calls.filter(c=>c.path==='/api/chat').length,1,'left action must not require camera');
  assert.match(ids.speakerDiagnostics.textContent,/文字发送/);
  assert.match(ids.speakerDiagnostics.textContent,/不采集声音/);
  vm.runInContext("transcriptionDiagnostic({text:'不应进入诊断的私人话语',diagnostics:{code:'insufficient_speech',audio_seconds:8,speech_seconds:.7,min_speech_seconds:1.2,trace_id:'test-trace'}},'manual')",context);
  assert.match(ids.speakerDiagnostics.textContent,/有效语音不足/);
  assert.match(ids.speakerDiagnostics.textContent,/0.7/);
  assert.ok(!ids.speakerDiagnostics.textContent.includes('不应进入诊断的私人话语'));
  vm.runInContext("for(let i=0;i<15;i++)speakerDiagnostic('诊断'+i)",context);
  assert.equal(vm.runInContext('speakerDiagnosticSteps.length',context),10);
  const count=calls.length;
  await vm.runInContext("send('帮我看看这是什么')",context);
  assert.equal(calls.length,count,'no picture captured or sent with camera closed');
  assert.match(ids.notice.textContent,/开启摄像头/);
  await vm.runInContext("send('照片里是什么？',{image:'test-image-in-memory'})",context);
  assert.equal(calls.at(-1).data.image,'test-image-in-memory');
  assert.ok(!saved['xiaoban-history'].includes('test-image-in-memory'),'image must never be persisted');
  vm.runInContext("animateAction({target:'screen',action:'nod',label:'点头',duration_ms:99999})",context);
  assert.equal(ids.robot.animation.options.duration,2400,'bound animation duration');
  const before=ids.robot.animation;
  vm.runInContext("animateAction({target:'hardware',action:'nod',duration_ms:1000})",context);
  assert.equal(ids.robot.animation,before,'screen adapter ignores hardware target');
  const wav=vm.runInContext('encodeWav([new Float32Array([0,1,-1])],16000)',context);
  assert.equal(wav.byteLength,50);assert.equal(new DataView(wav).getUint32(24,true),16000);
  assert.equal(new DataView(wav).getInt16(46,true),32767);
  vm.runInContext("profiles=[{id:'alice',name:'甲'},{id:'bob',name:'乙'}];switchProfile('alice');messages=[{role:'user',content:'甲的私有上下文'}];applySpeaker({matched:false,id:null,reason:'不确定'});",context);
  assert.equal(vm.runInContext('activeProfile',context),'');
  assert.equal(vm.runInContext('messages.length',context),0,'unknown voice clears prior identity and history');
  // Only successful voice matches get one greeting per person per conversation.
  vm.runInContext("profiles=[{id:'alice',name:'甲',salutation:'王先生'},{id:'bob',name:'乙',salutation:'李女士'}];applySpeaker({matched:true,id:'alice',name:'甲'});",context);
  assert.equal(vm.runInContext("voiceGreeting('text')",context),null);
  await vm.runInContext("send('测试问候',{source:'manual'})",context);
  assert.equal(vm.runInContext('messages.at(-1).content',context),'王先生，你好。已收到');
  vm.runInContext("applySpeaker({matched:true,id:'alice',name:'甲'})",context);
  await vm.runInContext("send('再问一次',{source:'manual'})",context);
  assert.equal(vm.runInContext('messages.at(-1).content',context),'已收到');
  vm.runInContext("applySpeaker({matched:true,id:'bob',name:'乙'})",context);
  await vm.runInContext("send('测试第二位用户',{source:'wake'})",context);
  assert.equal(vm.runInContext('messages.at(-1).content',context),'李女士，你好。已收到');
  vm.runInContext("applySpeaker({matched:false,id:null,reason:'不确定'})",context);
  assert.equal(vm.runInContext("voiceGreeting('manual')",context),null);
  vm.runInContext("applySpeaker({matched:true,id:'alice',name:'甲'})",context);
  assert.equal(vm.runInContext("voiceGreeting('manual')",context),null,'switching back must not repeat greeting');
  vm.runInContext("$('clear').onclick()",context);
  assert.equal(vm.runInContext("voiceGreeting('manual').text",context),'王先生，你好。');
  vm.runInContext("switchProfile('alice')",context);
  assert.equal(vm.runInContext("voiceGreeting('manual')",context),null,'manual selection is not a voice match');
  // Memory filtering and metadata must be text-only, scoped to the active profile.
  vm.runInContext("switchProfile('alice')",context);
  await new Promise(r=>setImmediate(r));
  vm.runInContext(`memoryLoaded=true;memoryItems=[
    {id:'m1',content:'喜欢乌龙茶 <script>unsafe</script>',created:'2026-01-01 00:00:00',updated:'2026-01-02 00:00:00',source:'manual'},
    {id:'m2',content:'周末跑步',created:null,updated:'2026-01-02 00:00:00',source:'legacy'}];renderMemories();`,context);
  assert.equal(ids.memoryCount.textContent,'已保存 2 / 200 条');
  assert.match(ids.memoryList.children[0].children[0].textContent,/<script>/,'content remains literal text');
  assert.match(ids.memoryList.children[0].children[1].textContent,/手动保存/);
  assert.match(ids.memoryList.children[1].children[1].textContent,/未知/);
  assert.match(vm.runInContext("memoryTime('2026-01-01 00:00:00')",context),/08:00:00/);
  ids.memorySearch.value='乌龙茶';ids.memorySearch.oninput();
  assert.equal(ids.memoryCount.textContent,'找到 1 / 2 条');
  ids.memorySearch.value='不存在';ids.memorySearch.oninput();
  assert.match(ids.memoryList.children[0].textContent,/没有匹配/);
  vm.runInContext("switchProfile('bob')",context);
  assert.equal(ids.memorySearch.value,'');
  assert.equal(vm.runInContext('memoryItems.length',context),0,'switch immediately clears old user memories');
  await new Promise(r=>setImmediate(r));
  // A delayed memory response cannot repopulate another person's panel.
  const originalFetch=context.fetch;let resolveMemory;
  context.fetch=async(path,options)=>path==='/api/memory/list'?new Promise(resolve=>{resolveMemory=resolve;}):originalFetch(path,options);
  const pendingMemory=vm.runInContext('refreshMemories()',context);
  vm.runInContext("switchProfile('')",context);
  resolveMemory({ok:true,json:async()=>({memories:[{id:'old',content:'上一用户资料'}]})});
  await pendingMemory;
  assert.equal(vm.runInContext('memoryItems.length',context),0);
  assert.equal(ids.exportMemories.disabled,true,'guest cannot export');
  context.fetch=originalFetch;
  // File selection/preview never writes; confirmation is explicit and profile-bound.
  vm.runInContext("switchProfile('alice')",context);
  await new Promise(r=>setImmediate(r));
  const beforeImportFetch=context.fetch;let importWrites=0,previewCalls=0;
  context.fetch=async(path,options)=>{
    if(path==='/api/memory/import/preview'){
      previewCalls++;return{ok:true,json:async()=>({target:{id:'alice',name:'甲'},source_name:'备份甲',total:2,
        counts:{new:1,existing:1,file_duplicate:0},result_count:2,can_import:true,token:'preview-token',
        items:[{content:'<script>literal</script>',status:'new'},{content:'已有',status:'existing'}]})};
    }
    if(path==='/api/memory/import/confirm'){
      importWrites++;assert.deepEqual(JSON.parse(options.body),{profile_id:'alice',token:'preview-token',confirmed:true,acknowledge_conflicts:true});
      return{ok:true,json:async()=>({imported:1,total:2})};
    }
    return beforeImportFetch(path,options);
  };
  ids.memoryImportFile.files=[{size:100,text:async()=>'{"format":"test"}'}];
  ids.memoryImportFile.onchange();
  assert.equal(importWrites,0);
  await vm.runInContext('previewMemoryImport()',context);
  assert.equal(importWrites,0);assert.equal(previewCalls,1);
  assert.equal(ids.confirmMemoryImport.disabled,true);
  assert.match(ids.memoryImportSummary.textContent,/恢复到：甲/);
  assert.equal(ids.memoryImportItems.children[0].children[1].textContent,'<script>literal</script>');
  await vm.runInContext('confirmMemoryImport()',context);assert.equal(importWrites,0);
  vm.runInContext('memoryImportPlan.requires_conflict_confirmation=true',context);
  ids.memoryImportConsent.checked=true;ids.memoryImportConsent.onchange();
  assert.equal(ids.confirmMemoryImport.disabled,true,'possible conflicts require separate consent');
  await vm.runInContext('confirmMemoryImport()',context);assert.equal(importWrites,0);
  ids.memoryImportConflictConsent.checked=true;ids.memoryImportConflictConsent.onchange();
  assert.equal(ids.confirmMemoryImport.disabled,false);
  await vm.runInContext('confirmMemoryImport()',context);assert.equal(importWrites,1);
  assert.match(ids.memoryImportStatus.textContent,/恢复完成/);
  await vm.runInContext('confirmMemoryImport()',context);assert.equal(importWrites,1);
  ids.memoryImportFile.files=[{size:3*1024*1024,text:async()=>{throw Error('must not read oversized file');}}];
  await vm.runInContext('previewMemoryImport()',context);assert.equal(previewCalls,1);
  assert.match(ids.memoryImportStatus.textContent,/2 MB/);
  // A profile switch during file reading invalidates the pending import.
  let releaseImportFile;
  ids.memoryImportFile.files=[{size:100,text:()=>new Promise(resolve=>{releaseImportFile=resolve;})}];
  const pendingImport=vm.runInContext('previewMemoryImport()',context);
  vm.runInContext("switchProfile('bob')",context);
  releaseImportFile('{}');await pendingImport;
  assert.equal(previewCalls,1);assert.equal(vm.runInContext('memoryImportPlan',context),null);
  assert.equal(ids.confirmMemoryImport.disabled,true);
  context.fetch=beforeImportFetch;
  // Review is bound to the visible profile and changes never save before consent.
  vm.runInContext("switchProfile('alice');showMemoryReview({target:{id:'alice',name:'甲'},token:'review-token',content:'我不喜欢咖啡',original:null,conflicts:[{id:'m1',content:'我喜欢咖啡'}],stale:false,can_append:true})",context);
  assert.equal(ids.memoryReviewPanel.hidden,false);
  assert.equal(ids.confirmMemoryReview.disabled,true);
  assert.match(ids.memoryReviewTarget.textContent,/甲/);
  let reviewWrites=0;const reviewFetch=context.fetch;
  context.fetch=async(path,options)=>{
    if(path==='/api/memory/review/confirm'){reviewWrites++;assert.equal(JSON.parse(options.body).replace_id,'m1');return{ok:true,json:async()=>({id:'m1'})};}
    return reviewFetch(path,options);
  };
  await vm.runInContext('confirmMemoryReview()',context);assert.equal(reviewWrites,0);
  ids.memoryReviewChoice.value='m1';ids.memoryReviewConsent.checked=true;ids.memoryReviewConsent.onchange();
  await vm.runInContext('confirmMemoryReview()',context);assert.equal(reviewWrites,1);
  assert.equal(ids.memoryReviewPanel.hidden,true);
  vm.runInContext("showMemoryReview({target:{id:'alice',name:'甲'},token:'old',content:'旧草稿',original:null,conflicts:[],stale:false,can_append:true});switchProfile('bob')",context);
  assert.equal(ids.memoryReviewPanel.hidden,true);assert.equal(ids.memoryHistoryPanel.hidden,true);
  await vm.runInContext('confirmMemoryReview()',context);assert.equal(reviewWrites,1);
  context.fetch=reviewFetch;
  // A delayed old response must never enter the new conversation after cancellation.
  let release;
  context.fetch=async(path,options)=>{
    if(path==='/api/turn/cancel')return{ok:true,json:async()=>({cancelled:true})};
    if(path==='/api/chat')return{ok:true,body:{getReader:()=>({cancel:async()=>{},read:()=>new Promise(resolve=>{release=resolve;})})}};
    throw new Error('Unexpected path '+path);
  };
  vm.runInContext("applySpeaker({matched:true,id:'alice',name:'甲'})",context);
  const pending=vm.runInContext("send('请讲一个故事',{source:'manual'})",context);
  await new Promise(r=>setImmediate(r));
  vm.runInContext('cancelTurn()',context);
  release({done:false,value:new TextEncoder().encode('{"message":{"content":"旧回复"},"done":true}\n')});
  await pending;
  assert.equal(vm.runInContext("messages.some(m=>m.content==='旧回复')",context),false);
  assert.equal(vm.runInContext('busy',context),false);
  assert.equal(vm.runInContext("voiceGreeting('manual').text",context),'王先生，你好。','cancel before first response must not consume greeting');
  context.fetch=async()=>({json:async()=>({chat:true,asr:false,wake:false,vision:false,tts:false})});
  await vm.runInContext('status()',context);
  assert.equal(ids.autoplay.disabled,true,'text-only configuration disables speech');
  await vm.runInContext("speak('test')",context);
  assert.match(ids.notice.textContent,/未启用语音播放/);
  // Common spaces use their own bearer session; stale responses never cross accounts.
  context.fetch=async(path,options)=>{
    calls.push({path,options});
    if(path==='/api/shared/auth/logout'||path==='/api/turn/cancel')return{ok:true,json:async()=>({})};
    if(path==='/api/shared/memories/preview')return{ok:true,json:async()=>({token:'plan',kind:'family',space_name:'测试家庭',author_name:'测试作者',actor_name:'测试作者',audience:['测试作者'],before:null,after:{content:'共同内容',visibility:'selected',edit_policy:'author'},conflicts:[]})};
    throw Error('Unexpected shared request '+path);
  };
  vm.runInContext("sharedState.token='test-bearer';sharedState.account={id:'a',name:'测试作者'};sharedState.space={id:'s',name:'测试家庭',kind:'family',role:'owner',owner:'a',version:1};sharedState.members=[{id:'a',name:'测试作者',role:'owner'},{id:'b',name:'测试成员',role:'reader'}];sharedResetEditor();sharedControls()",context);
  const boxes=ids.sharedAudience.querySelectorAll('input');
  assert.equal(boxes[0].disabled,true,'owner always visible, even after control refresh');
  assert.equal(boxes[0].checked,true);assert.equal(boxes[1].disabled,false);
  ids.sharedMemoryText.value='共同内容';await ids.sharedPreview.onclick();
  assert.equal(calls.at(-1).options.headers.Authorization,'Bearer test-bearer');
  assert.equal(JSON.parse(calls.at(-1).options.body).profile_id,undefined);
  assert.equal(ids.sharedConfirm.disabled,true,'publish needs consent');
  ids.sharedPublishConsent.checked=true;ids.sharedPublishConsent.onchange();assert.equal(ids.sharedConfirm.disabled,false);
  vm.runInContext("sharedState.memories=[{id:'m',content:'共同秘密',author_name:'测试作者',editor_name:'测试作者',visibility:'all',edit_policy:'author',updated:'2026-01-01',can_edit:false,can_delete:false,can_history:false}];sharedState.space.role='reader';sharedRenderMemories();sharedControls()",context);
  assert.equal(ids.sharedPreview.disabled,true);assert.equal(ids.sharedMemoryList.children[0].children.length,4,'reader sees state/content/metadata/evidence only');assert.equal(ids.sharedMemoryList.children[0].children.at(-1).disabled,true);
  assert.ok(!JSON.stringify(saved).includes('共同秘密'),'shared content never enters browser history storage');
  let resolveShared;
  context.fetch=async()=>new Promise(resolve=>{resolveShared=resolve;});
  const pendingShared=vm.runInContext('sharedRefresh()',context);
  vm.runInContext("sharedLock();sharedState.token='new-bearer';sharedState.account={id:'new',name:'新账号'}",context);
  resolveShared({ok:true,json:async()=>({account:{id:'old',name:'旧账号'},spaces:[{id:'old',name:'秘密空间'}]})});await pendingShared;
  assert.equal(vm.runInContext('sharedState.account.id',context),'new');
  assert.equal(ids.sharedMemoryList.children.length,0);assert.equal(ids.sharedReviewPanel.hidden,true);
  const staleFailure=vm.runInContext("sharedApi('/spaces/list').catch(()=>{})",context);
  vm.runInContext("sharedLock();sharedState.token='third-bearer';sharedState.account={id:'third',name:'第三账号'}",context);
  resolveShared({ok:false,status:401,json:async()=>({error:'old failure'})});await staleFailure;
  assert.equal(vm.runInContext('sharedState.token',context),'third-bearer','old unauthorized response cannot log out new account');
  context.fetch=async()=>({ok:true,json:async()=>({})});
  vm.runInContext('sharedPersonalChanged()',context);
  assert.equal(vm.runInContext('sharedState.token',context),null,'personal/voice switch locks shared session');

  // Shared text conversations carry only opaque context, reset on scope changes, and ignore late replies.
  vm.runInContext("sharedState.token='dialogue-token';sharedState.account={id:'a',name:'甲'};sharedState.space={id:'s',name:'测试空间',kind:'team',role:'owner',version:1};sharedState.busy=false;sharedState.asking=false;",context);
  ids.sharedQuestion.value='科学活动在哪里？';
  const dialogueRequests=[];
  context.fetch=async(path,options)=>{dialogueRequests.push({path,data:JSON.parse(options.body)});return{ok:true,json:async()=>path.endsWith('/ask')?{answer:'请选择活动',sources:[],conversation:{id:'opaque-1',needs_clarification:true,choices:[{id:'option-1',label:'11月科学活动'}]}}:{reset:true}};};
  await ids.sharedAsk.onclick();
  assert.equal(dialogueRequests[0].data.conversation_mode,true);
  assert.equal(ids.sharedChoices.children.length,1);
  context.fetch=async(path,options)=>{dialogueRequests.push({path,data:JSON.parse(options.body)});return{ok:true,json:async()=>({answer:'十点集合',sources:[],conversation:{id:'opaque-2',topic:'11月科学活动',choices:[]}})};};
  await ids.sharedChoices.children[0].onclick();
  assert.equal(dialogueRequests.at(-1).data.choice_id,'option-1');
  assert.equal(dialogueRequests.at(-1).data.conversation_id,'opaque-1');
  assert.match(ids.sharedTopic.textContent,/11月科学活动/);
  ids.sharedIncludeOutdated.onchange();
  assert.equal(vm.runInContext('sharedState.conversation',context),null,'history scope clears topic');
  assert.equal(ids.sharedChoices.children.length,0);
  ids.sharedQuestion.value='旧空间的问题';vm.runInContext('sharedClearSpace()',context);assert.equal(ids.sharedQuestion.value,'');
  vm.runInContext("sharedState.space={id:'s',name:'测试空间',kind:'team',role:'owner',version:1}",context);
  vm.runInContext("sharedState.conversation='previous-topic'",context);
  ids.sharedNewTopic.onclick();assert.equal(ids.sharedQuestion.value,'');
  ids.sharedQuestion.value='谁带队？';let resolveDialogue;
  context.fetch=async path=>path.endsWith('/ask')?new Promise(resolve=>{resolveDialogue=resolve;}):({ok:true,json:async()=>({})});
  const lateDialogue=ids.sharedAsk.onclick();
  vm.runInContext('sharedLock()',context);
  resolveDialogue({ok:true,json:async()=>({answer:'旧空间秘密',sources:[],conversation:{id:'stale-context',topic:'旧话题',choices:[]}})});
  await lateDialogue;
  assert.equal(vm.runInContext('sharedState.conversation',context),null);
  assert.equal(ids.sharedTopic.textContent,'');assert.equal(ids.sharedAnswer.textContent,'');
  assert.ok(!JSON.stringify(saved).includes('opaque-'),'shared dialogue is not persisted in localStorage');
  context.fetch=async()=>({ok:true,json:async()=>({})});

  // Read-only agent mode routes explicitly and clears diagnostics on mode/account changes.
  vm.runInContext("sharedState.token='agent-token';sharedState.account={id:'a',name:'甲'};sharedState.space={id:'s',name:'测试空间',kind:'team',role:'owner',version:1};sharedState.busy=false;sharedState.asking=false;",context);
  ids.sharedAgentMode.checked=true;ids.sharedQuestion.value='今天几号？';
  let agentPath;
  context.fetch=async(path)=>{agentPath=path;return {ok:true,json:async()=>({answer:'当前电脑时间',sources:[],conversation:{},agent:{status:'done',steps:[{name:'get_current_time',status:'done',elapsed_ms:1}]}})};};
  await ids.sharedAsk.onclick();assert.equal(agentPath,'/api/shared/agent/memory/ask');
  assert.equal(ids.sharedAgentPanel.hidden,false);assert.match(ids.sharedAgentSteps.children[0].textContent,/获取当前时间.*完成/);
  ids.sharedAgentMode.checked=false;ids.sharedAgentMode.onchange();assert.equal(ids.sharedAgentPanel.hidden,true);assert.equal(ids.sharedAgentSteps.children.length,0);
  await ids.sharedAsk.onclick();assert.equal(agentPath,'/api/shared/ask');
  ids.sharedAgentMode.checked=true;let resolveAgent;
  context.fetch=async path=>path.endsWith('/ask')?new Promise(resolve=>{resolveAgent=resolve;}):({ok:true,json:async()=>({})});
  const lateAgent=ids.sharedAsk.onclick();vm.runInContext('sharedLock()',context);
  resolveAgent({ok:true,json:async()=>({answer:'秘密',sources:[],agent:{status:'done',steps:[{name:'read_material',status:'done'}]}})});await lateAgent;
  assert.equal(ids.sharedAgentPanel.hidden,true);assert.equal(ids.sharedAnswer.textContent,'');
  ids.sharedAgentMode.checked=false;context.fetch=async()=>({ok:true,json:async()=>({})});

  // Program-resolved replacement roots are distinguished from model-reported citations.
  context.linkedResult={source_usage:'linked_record',answer_source_ids:['root'],sources:[{id:'root',content:'旧安排原文',status:'outdated',reference:1}]};
  vm.runInContext('sharedRenderAnswerSources(linkedResult)',context);
  assert.equal(ids.sharedCitedSources.children.length,1);assert.match(ids.sharedCitationStatus.textContent,/程序按替代关联/);
  vm.runInContext('sharedClearSourceCards()',context);
  // Agent drafts require an explicit checkbox + button; cancel/reset revokes the server plan.
  vm.runInContext("sharedState.token='draft-auth';sharedState.account={id:'a',name:'甲'};sharedState.space={id:'s',name:'测试空间',kind:'team',role:'owner',version:1};sharedState.busy=false;sharedState.asking=false;",context);
  const draftPlan={token:'preview-one',space_id:'s',operation:'update',kind:'team',space_name:'测试空间',author_name:'甲',actor_name:'甲',audience:['甲','乙'],before:{content:'十点集合',visibility:'selected',event_date:''},after:{content:'十一点集合',visibility:'selected',edit_policy:'author',status:'pending',status_reason:'需要重新核实',event_date:''},conflicts:[]};
  context.draftPlan=draftPlan;let draftWrites=0,draftCancels=0;
  context.fetch=async(path,options)=>{const body=JSON.parse(options.body);if(path.endsWith('/confirm')){draftWrites++;assert.equal(body.token,'preview-one');assert.equal(body.content,undefined);}if(path.endsWith('/cancel'))draftCancels++;return {ok:true,json:async()=>({saved:true})};};
  vm.runInContext('sharedRenderAgentDraft({memory_preview:draftPlan});sharedControls()',context);
  assert.equal(ids.sharedAgentReview.hidden,false);assert.equal(ids.sharedAgentConfirm.disabled,true);
  assert.match(ids.sharedAgentReviewSummary.textContent,/测试空间.*甲.*乙/s);
  assert.match(ids.sharedAgentReviewBody.children.map(x=>x.textContent).join(' '),/修改前：十点集合.*准备保存：十一点集合/);
  await ids.sharedAgentConfirm.onclick();assert.equal(draftWrites,0,'no checked consent means no confirmation request');
  ids.sharedAgentCancel.onclick();assert.equal(draftCancels,1);assert.equal(ids.sharedAgentReview.hidden,true);
  vm.runInContext('sharedRenderAgentDraft({memory_preview:{...draftPlan,conflicts:[{content:"十二点集合"}]}})',context);
  ids.sharedAgentConsent.checked=true;ids.sharedAgentConsent.onchange();assert.equal(ids.sharedAgentConfirm.disabled,true);
  await ids.sharedAgentConfirm.onclick();assert.equal(draftWrites,0,'conflicts need independent acknowledgement');
  ids.sharedAgentConflictConsent.checked=true;ids.sharedAgentConflictConsent.onchange();assert.equal(ids.sharedAgentConfirm.disabled,false);
  vm.runInContext('var savedDraftRefresh=sharedRefresh; sharedRefresh=async()=>{};',context);
  await ids.sharedAgentConfirm.onclick();assert.equal(draftWrites,1);assert.equal(ids.sharedAgentReview.hidden,true);
  await ids.sharedAgentConfirm.onclick();assert.equal(draftWrites,1,'double click cannot repeat a consumed draft');
  vm.runInContext('sharedRefresh=savedDraftRefresh;sharedRenderAgentDraft({memory_preview:draftPlan});',context);
  ids.sharedNewTopic.onclick();assert.equal(ids.sharedAgentReview.hidden,true);assert.equal(ids.sharedAgentConsent.checked,false);
  assert.ok(!JSON.stringify(saved).includes('preview-one'),'draft confirmation tokens must not persist');
  // A replacement requires a separate transition acknowledgement, in both button and handler.
  vm.runInContext("sharedRenderAgentDraft({memory_preview:{...draftPlan,operation:'replace',before:{...draftPlan.before,status:'valid'},after:{...draftPlan.after,status:'valid'}}});sharedRefresh=async()=>{};",context);
  assert.equal(ids.sharedAgentReplaceLabel.hidden,false);assert.equal(ids.sharedAgentReplaceConsent.checked,false);
  ids.sharedAgentConsent.checked=true;ids.sharedAgentConsent.onchange();assert.equal(ids.sharedAgentConfirm.disabled,true);
  const beforeReplacementWrites=draftWrites;await ids.sharedAgentConfirm.onclick();assert.equal(draftWrites,beforeReplacementWrites);
  context.fetch=async(path,options)=>{if(path.endsWith('/confirm')){draftWrites++;assert.equal(JSON.parse(options.body).replacement_confirmed,true);}return {ok:true,json:async()=>({saved:true})};};
  ids.sharedAgentReplaceConsent.checked=true;ids.sharedAgentReplaceConsent.onchange();assert.equal(ids.sharedAgentConfirm.disabled,false);
  await ids.sharedAgentConfirm.onclick();assert.equal(draftWrites,beforeReplacementWrites+1);assert.match(ids.sharedAnswer.textContent,/旧安排已标为过时/);
  assert.equal(ids.sharedAgentReplaceConsent.checked,false);
  vm.runInContext("sharedRenderAgentDraft({memory_preview:draftPlan});sharedRefresh=savedDraftRefresh;",context);
  assert.equal(ids.sharedAgentReplaceLabel.hidden,true);ids.sharedAgentCancel.onclick();
  // Manual editor sends a frozen target/version; cancel does not write, and logout clears it.
  const replacementRow=new Element();context.replacementRow=replacementRow;
  vm.runInContext("sharedReplacementButtons(replacementRow,{id:'old',version:3,content:'旧安排',event_date:'2026-11-21',status:'valid',can_replace:true});",context);
  replacementRow.children[0].onclick();assert.equal(ids.sharedReplacementText.value,'旧安排');
  ids.sharedReplacementCancel.onclick();assert.equal(ids.sharedReplacementPanel.hidden,true);
  replacementRow.children[0].onclick();ids.sharedReplacementText.value='新安排';ids.sharedReplacementReason.value='负责人确认';
  context.fetch=async(path,options)=>{if(path.endsWith('/replace-preview')){const body=JSON.parse(options.body);assert.equal(body.id,'old');assert.equal(body.version,3);assert.equal(body.content,'新安排');return {ok:true,json:async()=>({answer:'尚未生效',memory_preview:{...draftPlan,operation:'replace'}})};}return {ok:true,json:async()=>({})};};
  await ids.sharedReplacementPreview.onclick();assert.equal(ids.sharedAgentReview.hidden,false);assert.equal(ids.sharedReplacementPanel.hidden,true);
  ids.sharedAgentCancel.onclick();replacementRow.children[0].onclick();vm.runInContext('sharedClearSpace()',context);
  assert.equal(ids.sharedReplacementPanel.hidden,true);assert.equal(ids.sharedReplacementText.value,'');
  vm.runInContext("sharedState.space={id:'s',name:'测试空间',kind:'team',role:'owner',version:1}",context);
  // Late previews are discarded with the original session instead of appearing in a new account.
  ids.sharedAgentMode.checked=true;ids.sharedQuestion.value='保存活动安排';let resolveDraft;
  context.fetch=async(path,options)=>path.endsWith('/ask')?new Promise(resolve=>{resolveDraft=resolve;}):(draftCancels++,{ok:true,json:async()=>({cancelled:true})});
  const lateDraft=ids.sharedAsk.onclick();vm.runInContext('sharedLock()',context);
  resolveDraft({ok:true,json:async()=>({answer:'预览',memory_preview:draftPlan,agent:{status:'needs_confirmation',steps:[]}})});await lateDraft;
  assert.equal(ids.sharedAgentReview.hidden,true);assert.equal(vm.runInContext('sharedAgentDraft',context),null);
  ids.sharedAgentMode.checked=false;context.fetch=async()=>({ok:true,json:async()=>({})});

  // Shared voice consumes ASR receipts, keeps personal history intact, and arms only after playback.
  const audioInstances=[];
  context.Audio=class{constructor(){audioInstances.push(this);}async play(){}pause(){}};
  context.URL={createObjectURL:()=> 'blob:voice-test',revokeObjectURL(){}};
  vm.runInContext("pauseWake=async()=>{wakePhase='';};resumeWake=async mode=>{wakePhase=mode||'wake';};monitorInterrupt=async()=>{};sharedLoadCurrent=async()=>{};ready=true;asrReady=true;wakeReady=true;speakerReady=true;ttsReady=true;busy=false;recording=false;enrolling=false;wakeEnabled=true;sharedState.token='voice-token';sharedState.account={id:'a',name:'测试账号'};sharedState.space={id:'s',name:'测试家庭',kind:'family',role:'owner',owner:'a',version:1};sharedVoiceState.session='voice-session';sharedVoiceState.token='voice-token';sharedVoiceState.profile='alice';messages=[{role:'user',content:'原个人聊天'}];controls()",context);
  const originalPersonalHistory=vm.runInContext('JSON.stringify(messages)',context);
  let voiceWrites=0,playedCalls=0,cancelCalls=0;
  const voiceFetch=async(path,options)=>{
    if(path==='/api/shared/voice/start')return{ok:true,json:async()=>({session:'started-voice',reply:'已开启测试空间语音'})};
    if(path==='/api/shared/voice/utterance'){
      voiceWrites++;const body=JSON.parse(options.body);assert.equal(body.utterance_id,'server-issued');assert.equal(body.text,undefined);
      return{ok:true,json:async()=>({reply:'准备保存到测试家庭，全体成员可见。',needs_confirmation:true,review_id:'spoken-review',saved:false})};
    }
    if(path==='/api/shared/voice/played'){playedCalls++;return{ok:true,json:async()=>({})};}
    if(path==='/api/shared/voice/cancel'){cancelCalls++;return{ok:true,json:async()=>({})};}
    if(path==='/api/shared/voice/stop'||path==='/api/turn/cancel')return{ok:true,json:async()=>({})};
    if(path==='/api/speak')return{ok:true,blob:async()=>new Blob(['test'])};
    throw Error('Unexpected speech fetch '+path);
  };
  context.fetch=voiceFetch;
  await vm.runInContext("send('确认保存')",context);assert.equal(voiceWrites,0,'typed confirmation never submits');
  await vm.runInContext("sharedVoiceReceive({text:'记住测试安排',utterance_id:'server-issued'},'manual')",context);
  assert.equal(voiceWrites,1);assert.equal(playedCalls,0,'TTS request or play start does not arm confirmation');
  assert.equal(vm.runInContext('JSON.stringify(messages)',context),originalPersonalHistory);
  assert.ok(!JSON.stringify(saved).includes('记住测试安排'));
  await audioInstances.at(-1).onended();
  assert.equal(vm.runInContext('conversationDiagnostics.exportData().turns[0].steps.at(-1).stage',context),'confirmation');
  assert.equal(playedCalls,1);assert.equal(vm.runInContext('wakePhase',context),'command','complete playback opens follow-up listening');
  await vm.runInContext("sharedVoiceReceive({text:'新的草稿',utterance_id:'server-issued'},'wake')",context);
  const interruptedAudio=audioInstances.at(-1);
  vm.runInContext('cancelTurn()',context);await interruptedAudio.onended();
  assert.equal(playedCalls,1,'interrupted playback cannot arm old review');assert.ok(cancelCalls>0);
  vm.runInContext("sharedVoiceStop();",context);
  assert.equal(vm.runInContext('sharedVoiceActive()',context),false);
  assert.equal(vm.runInContext('JSON.stringify(messages)',context),originalPersonalHistory);
  ids.sharedPanel.open=true;
  vm.runInContext("activeProfile='alice';sharedState.busy=false;sharedState.asking=false;showWorkspacePage('shared');showSharedPage('members');sharedVoiceControls();",context);
  assert.equal(ids.sharedVoiceStart.disabled,false);
  await ids.sharedVoiceStart.onclick();
  assert.equal(ids.viewChat.hidden,false,'starting shared voice opens the conversation');
  assert.equal(ids.sharedPanel.open,true,'the shared workspace remains accessible');
  assert.equal(ids.sharedVoiceBanner.hidden,false);
  assert.equal(ids.workspaceSharedVoice.hidden,true);
  vm.runInContext("sharedVoiceStop();applySpeaker({matched:false,diagnostics:{code:'low_score'}});",context);
  assert.equal(ids.workspaceSharedVoice.hidden,false);
  assert.match(ids.workspaceSpeakerResult.textContent,/本轮按访客处理：声纹相似度未达标/);
  // Leaving shared mode while a microphone permission/start is pending cannot leak audio into personal chat.
  let releaseMic,stoppedMic=0;
  context.TestMicrophone=class{async start(){await new Promise(resolve=>{releaseMic=resolve;});}async stop(){stoppedMic++;}};
  vm.runInContext("Microphone=TestMicrophone;sharedVoiceState.session='pending-mic-session';sharedVoiceState.token='voice-token';speaking=false;busy=false;recording=false;wakePhase='';",context);
  const pendingMic=vm.runInContext('startRecording()',context);await new Promise(r=>setImmediate(r));
  vm.runInContext('sharedVoiceStop()',context);releaseMic();await pendingMic;
  assert.equal(vm.runInContext('recording',context),false);assert.ok(stoppedMic>0);
  assert.equal(ids.autoplay.disabled,false,'leaving voice mode restores ordinary autoplay control');
  // Material drafts stay ephemeral, and logout invalidates in-flight extraction.
  context.fetch=async()=>({ok:true,json:async()=>({discarded:true})});
  vm.runInContext("sharedState.token='material-token';sharedState.account={id:'actor',name:'测试者'};sharedState.space={id:'space',name:'测试团队',kind:'team',role:'owner',version:1};sharedState.members=[{id:'actor',name:'测试者',role:'owner'}];sharedState.busy=false;sharedState.asking=false;materialReset();materialAudience();",context);
  ids.materialAddText.onclick();
  assert.equal(vm.runInContext('materialState.sources.length',context),1);
  vm.runInContext("materialState.sources[0].text='<script>literal source</script>';materialState.candidates=[{selected:true,content:'draft',source_name:'test',evidence:'original',event_date:'',source_note:'test'}];materialRenderCandidates();",context);
  assert.ok(ids.materialCandidates.children.length);
  vm.runInContext("materialState.plan={token:'review',conflicts:[]};materialControls();",context);
  assert.equal(ids.materialEditor.disabled,true,'frozen preview locks all draft fields');
  assert.equal(ids.materialConfirm.disabled,true,'confirmation is explicit');
  ids.materialConsent.checked=true;ids.materialConsent.onchange();
  assert.equal(ids.materialConfirm.disabled,false);
  ids.materialBack.onclick();assert.equal(ids.materialConfirm.disabled,true);
  let releaseMaterial;
  context.fetch=async(path)=>path!=='/api/shared/import/organize'?({ok:true,json:async()=>({})}):new Promise(resolve=>{releaseMaterial=()=>resolve({ok:true,json:async()=>({candidates:[{content:'old private data',evidence:'old',source_name:'old'}],notice:'old'})});});
  ids.materialMode.value='paragraphs';
  const materialPending=ids.materialOrganize.onclick();
  await new Promise(r=>setImmediate(r));
  vm.runInContext("sharedLock('退出');",context);releaseMaterial();await materialPending;
  assert.equal(vm.runInContext('materialState.candidates.length',context),0,'late response cannot restore private drafts');
  assert.equal(ids.materialSources.children.length,0);
  assert.ok(!JSON.stringify(saved).includes('old private data'));
  // Answer citations are distinct from candidates; missing/malformed references cannot become evidence.
  vm.runInContext("sharedState.token='source-token';sharedState.account={id:'a',name:'甲'};sharedState.space={id:'s',name:'测试空间',kind:'team',role:'owner',version:1};sharedState.busy=false;sharedState.asking=false;",context);
  const sourceFixture=[{id:'a',reference:1,content:'科学活动十点集合',author_name:'甲',status:'valid',status_reason:'已核对',event_date:'2026-11-21',updated:'2026-11-10 00:00:00',source_note:'活动通知',has_evidence:true,evidence_source_name:'通知.txt',version:3},
    {id:'b',reference:2,content:'观鸟暂定湿地',author_name:'乙',status:'pending',event_date:'',source_note:'<script>仅作文本</script>',has_evidence:false,version:1}];
  context.sourceFixture=sourceFixture;
  vm.runInContext("sharedRenderAnswerSources({sources:sourceFixture,answer_source_ids:['a'],source_usage:'model_reported'})",context);
  assert.equal(ids.sharedCitedSources.children.length,1);assert.equal(ids.sharedSources.children.length,1);
  assert.match(ids.sharedCitedSources.children[0].children[0].textContent,/资料 1.*实际引用/);
  assert.match(ids.sharedCitedSources.children[0].children[3].textContent,/事件日期：2026-11-21/);
  assert.match(ids.sharedCitedSources.children[0].children[5].textContent,/通知.txt/);
  assert.match(ids.sharedSources.children[0].children[3].textContent,/事件日期：未填写/);
  assert.match(ids.sharedSources.children[0].children[3].textContent,/<script>/,'untrusted notes are plain text');
  assert.equal(ids.sharedSources.children[0].children.at(-1).disabled,true);
  assert.match(ids.sharedCandidatesTitle.textContent,/未被引用/);
  for(const value of [null,['unknown'],['a','a']]){
    context.testCitationIds=value;vm.runInContext("sharedRenderAnswerSources({sources:sourceFixture,answer_source_ids:testCitationIds,source_usage:'model_reported'})",context);
    assert.equal(ids.sharedCitedPanel.hidden,true);assert.equal(ids.sharedSources.children.length,2);assert.match(ids.sharedCitationStatus.textContent,/引用关系未确认/);
  }
  vm.runInContext("sharedRenderAnswerSources({sources:sourceFixture,answer_source_ids:[],source_usage:'model_reported'})",context);
  assert.match(ids.sharedCitationStatus.textContent,/没有实际引用/);assert.equal(ids.sharedCitedPanel.hidden,true);
  vm.runInContext("sharedRenderAnswerSources({sources:sourceFixture,answer_source_ids:['a'],source_usage:'unavailable'})",context);
  assert.equal(ids.sharedCitedPanel.hidden,true);
  vm.runInContext("sharedRenderAnswerSources({sources:[]})",context);assert.equal(ids.sharedAnswerSources.hidden,true);assert.equal(ids.sharedSources.children.length,0);

  // Evidence contains private text; closing/locking invalidates late responses.
  vm.runInContext("sharedState.token='evidence-token';sharedState.account={id:'author',name:'作者'};sharedState.space={id:'evidence-space',name:'测试空间',kind:'team',role:'owner',version:1};sharedState.members=[];sharedState.busy=false;sharedState.asking=false;",context);
  let evidenceBody;
  context.fetch=async(path,options)=>{
    evidenceBody=JSON.parse(options.body);
    return{ok:true,json:async()=>({available:true,source_name:'原文.txt',created:'2026-11-09',captured_content:'活动安排',quotes:[{location:'第 2 行',text:'核对摘录'}],can_read_full:true,content_changed:true})};
  };
  await vm.runInContext("sharedOpenEvidence({id:'e1'})",context);
  assert.equal(evidenceBody.include_full,false,'full source requires a separate action');
  assert.equal(ids.sharedEvidenceQuotes.children[0].textContent,'第 2 行\n核对摘录');
  assert.equal(ids.sharedEvidenceFull.textContent,'');
  assert.ok(ids.sharedEvidenceSummary.textContent.includes('当前正文已修改'));
  vm.runInContext("sharedRenderAnswerSources({sources:sourceFixture,answer_source_ids:['a'],source_usage:'model_reported'})",context);
  ids.sharedCitedSources.children[0].children.at(-1).onclick();await new Promise(r=>setImmediate(r));
  assert.equal(evidenceBody.expected_version,3,'answer source evidence is version-bound');
  assert.equal(evidenceBody.id,'a');
  context.fetch=async()=>({ok:false,status:409,json:async()=>({error:'这条记忆已更新，请重新提问。'})});
  await vm.runInContext("sharedOpenEvidence({id:'a',version:3})",context);
  assert.equal(ids.sharedEvidencePanel.hidden,true);assert.equal(ids.sharedAnswerSources.hidden,true);assert.equal(ids.sharedAnswer.textContent,'');
  assert.match(ids.sharedStatus.textContent,/已更新/);
  let releaseEvidence;
  context.fetch=async(path)=>path==='/api/shared/memories/evidence'?new Promise(resolve=>{releaseEvidence=()=>resolve({ok:true,json:async()=>({available:true,source_name:'秘密',created:'2026-11-09',captured_content:'私密',quotes:[],can_read_full:true,source_text:'不能迟到显示的原文'})});}):({ok:true,json:async()=>({})});
  const pendingEvidence=vm.runInContext("sharedOpenEvidence({id:'e1'},true)",context);
  await new Promise(r=>setImmediate(r));
  vm.runInContext("sharedLock()",context);releaseEvidence();await pendingEvidence;
  assert.equal(ids.sharedEvidencePanel.hidden,true);
  assert.equal(ids.sharedEvidenceFull.textContent,'');
  assert.ok(!JSON.stringify(saved).includes('不能迟到显示的原文'));
  // One manual turn keeps its trace through capture, ASR, chat, TTS and playback.
  vm.runInContext("sharedVoiceState.session=null;wakeEnabled=false;wakePhase='';busy=false;recording=false;speaking=false;enrolling=false;ready=true;ttsReady=true;$('autoplay').checked=true;$('speakerAuto').checked=false;",context);
  const tracedRequests=[];let diagnosticSpeaker=null;
  context.TestMicrophone=class{async start(callback){callback(new Float32Array([0,.1,0]));}async stop(){}};
  vm.runInContext('Microphone=TestMicrophone',context);
  context.fetch=async(path,options)=>{
    if(path==='/api/diagnostics/trace')return{ok:true,json:async()=>({available:true,steps:[]})};
    if(['/api/transcribe','/api/chat','/api/speak'].includes(path))tracedRequests.push({path,trace:options.headers['X-Conversation-Trace']});
    if(path==='/api/transcribe')return{ok:true,json:async()=>({text:'测试诊断串联',speaker:diagnosticSpeaker,diagnostics:{speaker_enabled:!!diagnosticSpeaker,audio_seconds:2,secret:'must not retain'}})};
    if(path==='/api/speak')return{ok:true,blob:async()=>new Blob(['test'])};
    return originalFetch(path,options);
  };
  await vm.runInContext('startRecording()',context);
  assert.match(ids.conversationState.textContent,/收音/);
  await vm.runInContext('stopRecording()',context);await new Promise(r=>setImmediate(r));
  assert.deepEqual(tracedRequests.map(r=>r.path),['/api/transcribe','/api/chat','/api/speak']);
  assert.equal(new Set(tracedRequests.map(r=>r.trace)).size,1);
  assert.ok(tracedRequests[0].trace);
  assert.match(ids.conversationState.textContent,/播放/);
  await audioInstances.at(-1).onended();
  let traceData=JSON.parse(vm.runInContext('JSON.stringify(conversationDiagnostics.exportData())',context));
  assert.equal(traceData.turns[0].outcome,'done');
  assert.deepEqual(traceData.turns[0].steps.map(s=>s.stage),['capture','listening','recognizing','request','tts','playback']);
  assert.equal(traceData.turns[0].audio.speaker,'disabled');
  assert.ok(!JSON.stringify(traceData).includes('测试诊断串联'));
  assert.ok(!JSON.stringify(traceData).includes('must not retain'));
  assert.ok(!JSON.stringify(saved).includes(tracedRequests[0].trace),'traces never enter localStorage');
  // An uncertain voice prompts a fresh recording and never submits the question to chat.
  ids.speakerAuto.checked=true;
  for(const code of ['low_score','insufficient_speech','ambiguous']){
    diagnosticSpeaker={matched:false,diagnostics:{code}};
    const requestCount=tracedRequests.length;
    const microphoneCount=micCalls;
    await vm.runInContext('startRecording()',context);
    await vm.runInContext('stopRecording()',context);
    await new Promise(r=>setImmediate(r));
    assert.deepEqual(tracedRequests.slice(requestCount).map(r=>r.path),['/api/transcribe']);
    assert.equal(vm.runInContext('activeProfile',context),'');
    assert.equal(ids.speakerRetry.hidden,false);
    assert.match(ids.workspaceSpeakerResult.textContent,/重新识别声音/);
    assert.equal(micCalls,microphoneCount,'the retry prompt must not acquire a microphone');
    assert.equal(vm.runInContext('conversationDiagnostics.exportData().turns[0].reason',context),'speaker_retry');
  }
  // Automatic speaker switches must retain the recording trace, including unknown guests.
  vm.runInContext("profiles=[{id:'trace-a',name:'A'},{id:'trace-b',name:'B'}];activeProfile='trace-a';",context);
  for(const speakerResult of [{matched:true,id:'trace-b',name:'B'},{matched:false,reason:'unknown'}]){
    diagnosticSpeaker=speakerResult;
    await vm.runInContext('startRecording()',context);await vm.runInContext('stopRecording()',context);await new Promise(r=>setImmediate(r));
    assert.equal(new Set(tracedRequests.slice(-3).map(r=>r.trace)).size,1,'automatic identity switch preserves one trace');
    await audioInstances.at(-1).onended();
    assert.equal(vm.runInContext('conversationDiagnostics.exportData().turns[0].steps[0].stage',context),'capture');
    assert.equal(vm.runInContext('conversationDiagnostics.exportData().turns[0].outcome',context),'done');
  }
  assert.equal(ids.speakerRetry.hidden,true,'a new speaker result clears the retry prompt');
  ids.speakerAuto.checked=false;
  diagnosticSpeaker=null;
  // Browser playback failure is not reported as successful completion.
  context.Audio=class{async play(){throw Error('private upstream text');}pause(){}};
  await vm.runInContext("speak('不应保存在诊断里的文字')",context);
  traceData=JSON.parse(vm.runInContext('JSON.stringify(conversationDiagnostics.exportData())',context));
  assert.equal(traceData.turns[0].outcome,'error');assert.equal(traceData.turns[0].reason,'playback');
  assert.ok(!JSON.stringify(traceData).includes('private upstream'));
  // A denied microphone has a capture-stage error, with recovery possible.
  context.TestMicrophone=class{async start(){const error=Error('denied');error.name='NotAllowedError';throw error;}async stop(){}};
  vm.runInContext('Microphone=TestMicrophone',context);await vm.runInContext('startRecording()',context);
  assert.equal(vm.runInContext('conversationDiagnostics.exportData().turns[0].reason',context),'mic');
  assert.equal(vm.runInContext('busy||recording',context),false);
  // Late diagnostic responses cannot revive a cancelled turn or a cleared list.
  let releaseTrace;
  context.fetch=async()=>new Promise(resolve=>{releaseTrace=resolve;});
  vm.runInContext("globalThis.oldTrace=conversationDiagnostics.start('manual');conversationDiagnostics.stage(oldTrace,'recognizing');",context);
  const poll=vm.runInContext('conversationDiagnostics.refresh(oldTrace)',context);
  vm.runInContext("conversationDiagnostics.finish(oldTrace,'cancelled');globalThis.newTrace=conversationDiagnostics.start('text');conversationDiagnostics.stage(newTrace,'request');",context);
  releaseTrace({ok:true,json:async()=>({available:true,steps:[{stage:'asr',status:'done',elapsed_ms:25}]})});await poll;
  assert.equal(vm.runInContext('conversationDiagnostics.current()===newTrace',context),true);
  assert.equal(vm.runInContext('conversationDiagnostics.exportData().turns[1].outcome',context),'cancelled');
  const clearPoll=vm.runInContext('conversationDiagnostics.refresh(newTrace)',context);
  ids.conversationTraceClear.onclick();assert.equal(vm.runInContext('conversationDiagnostics.current()===newTrace',context),true,'clear retains the active turn');
  vm.runInContext("conversationDiagnostics.finish(newTrace)",context);ids.conversationTraceClear.onclick();
  releaseTrace({ok:true,json:async()=>({available:true,steps:[]})});await clearPoll;
  assert.equal(vm.runInContext('conversationDiagnostics.exportData().turns.length',context),0);
  vm.runInContext("for(let i=0;i<30;i++){const id=conversationDiagnostics.start('text');conversationDiagnostics.finish(id);}",context);
  assert.equal(vm.runInContext('conversationDiagnostics.exportData().turns.length',context),20);

  assert.match(vm.runInContext("retrievalDiagnosticText({strategy:'lexical',hit_count:0,elapsed_ms:12,fallback:true})",context),/回退文字检索/);
  assert.equal(vm.runInContext("retrievalDiagnosticText({strategy:'secret',hit_count:1})",context),'');
  assert.match(vm.runInContext("retrievalDiagnosticText({strategy:'hybrid',hit_count:1,elapsed_ms:30})",context),/找到 1 条/);

  // Speech settings: explicit persistence, preview cancellation, late responses and shared-mode guards.
  vm.runInContext("busy=false;recording=false;speaking=false;enrolling=false;wakeStarting=false;wakePhase='';wakeEnabled=false;sharedVoiceState.session=null;",context);
  const speechData={available:true,voices:[{name:'Tingting',locale:'zh_CN'},{name:'Eddy',locale:'zh_CN'}],current:{voice:'Tingting',rate:195},revision:'one'};
  context.speechTestData=speechData;vm.runInContext('displaySpeechSettings(speechTestData)',context);
  let saves=0,previews=0,plays=0,resolvePreview;
  const speechFetch=context.fetch;
  context.fetch=async(path,options)=>{
    if(path==='/api/speech/settings/save'){saves++;const body=JSON.parse(options.body);return{ok:true,json:async()=>({...speechData,current:{voice:body.voice,rate:body.rate},revision:'two'})};}
    if(path==='/api/speech/preview'){previews++;return new Promise(resolve=>{resolvePreview=resolve;});}
    return speechFetch(path,options);
  };
  context.Audio=class{async play(){plays++;}pause(){} set volume(v){throw new Error('Preview must never change gain');}};
  context.URL={createObjectURL:()=> 'blob:sample',revokeObjectURL(){}};
  ids.speechRate.value='160';ids.speechRate.oninput();ids.speechVoice.value='Eddy';ids.speechVoice.onchange();assert.equal(saves,0);assert.equal(previews,0);
  let preview=vm.runInContext('previewSpeech()',context);await new Promise(r=>setImmediate(r));assert.equal(previews,1);
  vm.runInContext('cancelTurn()',context);resolvePreview({ok:true,blob:async()=>({})});await preview;assert.equal(plays,0,'cancelled late audio must not play');
  preview=vm.runInContext('previewSpeech()',context);await new Promise(r=>setImmediate(r));resolvePreview({ok:true,blob:async()=>({})});await preview;assert.equal(plays,1);assert.equal(saves,0);
  await vm.runInContext('saveSpeechSettings()',context);assert.equal(saves,0,'cannot save during preview');
  vm.runInContext('stopSpeechPreview(false)',context);await vm.runInContext('saveSpeechSettings()',context);assert.equal(saves,1);assert.match(ids.speechCurrent.textContent,/160/);
  vm.runInContext("sharedVoiceState.session='protected';",context);await vm.runInContext('previewSpeech()',context);assert.equal(previews,2,'shared confirmation blocks preview');
  vm.runInContext('sharedVoiceState.session=null;',context);context.fetch=speechFetch;
  // Persistent diagnostics are own-account metadata, cleared across spaces and late replies.
  const oldFetchAudit=context.fetch;
  vm.runInContext("sharedState.token='audit-login';sharedState.account={id:'a',name:'甲'};sharedState.space={id:'audit-space',name:'测试',role:'owner'};sharedState.asking=false;",context);
  const auditRows=[{id:'1',operation:'abcdef012345',started:1794280000,action:'confirm',status:'saved',reason:'ok',elapsed_ms:5,steps:[{stage:'commit',status:'done',elapsed_ms:5}],usage:{model_calls:0,reported_calls:0}},
    {id:'2',operation:'abcdef012345',started:1794279999,action:'ask',status:'failed',reason:'model_or_tool',elapsed_ms:2000,steps:[{stage:'model_wait',status:'error',elapsed_ms:2000}],usage:{model_calls:1,reported_calls:0},tools:[{name:'search_memory',status:'failed',elapsed_ms:4}]}];
  context.fetch=async()=>({ok:true,json:async()=>({available:true,records:auditRows,retention_days:14})});
  await vm.runInContext('sharedAuditLoad()',context);
  assert.equal(ids.sharedAuditList.children.length,2);assert.match(ids.sharedAuditInfo.textContent,/异常 1 次/);
  assert.match(ids.sharedAuditList.children[1].children.map(x=>x.textContent).join(' '),/Token 用量未知/);
  ids.sharedAuditList.children[0].open=true;vm.runInContext('sharedAuditRender()',context);assert.equal(ids.sharedAuditList.children[0].open,true,'refresh preserves expanded record');
  ids.sharedAuditFailures.checked=true;ids.sharedAuditFailures.onchange();assert.equal(ids.sharedAuditList.children.length,1);
  assert.match(vm.runInContext("auditUsage({model_calls:2,reported_calls:1,input_tokens:10,output_tokens:4,complete:false})",context),/部分.*1\/2/);
  let resolveAudit;context.fetch=()=>new Promise(resolve=>resolveAudit=resolve);
  const pendingAudit=vm.runInContext('sharedAuditLoad()',context);vm.runInContext('sharedClearSpace()',context);
  resolveAudit({ok:true,json:async()=>({available:true,records:auditRows})});await pendingAudit;
  assert.equal(ids.sharedAuditList.children.length,0);assert.equal(vm.runInContext('sharedAuditState.records.length',context),0);
  assert.equal(ids.sharedAuditExport.disabled,true);assert.equal(ids.sharedAuditFailures.checked,false);
  context.fetch=oldFetchAudit;

  // Feedback is consented, session-scoped, never restored from browser storage.
  vm.runInContext("sharedState.token='feedback-login';sharedState.space={id:'feedback-space',name:'反馈测试空间',role:'owner',version:1};sharedState.busy=false;sharedState.asking=false;sharedFeedbackClear();sharedFeedbackOffer({feedback_receipt:'answer-receipt'});sharedFeedbackControls();",context);
  assert.equal(ids.answerFeedbackForm.hidden,false);
  assert.equal(ids.answerFeedbackSave.disabled,true);
  ids.answerFeedbackConsent.checked=true;
  ids.answerFeedbackNote.value='反馈专用说明';
  vm.runInContext('sharedFeedbackControls()',context);
  const feedbackCalls=[];
  context.fetch=async(path,options)=>{feedbackCalls.push({path,body:JSON.parse(options.body)});return{ok:true,json:async()=>({id:'feedback-1',saved:true})};};
  await ids.answerFeedbackSave.onclick();
  assert.equal(feedbackCalls[0].path,'/api/shared/feedback/submit');
  assert.equal(feedbackCalls[0].body.consent,true);
  assert.equal(feedbackCalls[0].body.receipt,'answer-receipt');
  assert.equal(feedbackCalls[0].body.answer,undefined,'the browser cannot supply the answer snapshot');
  assert.equal(ids.answerFeedbackSave.disabled,true);
  assert.match(ids.answerFeedbackStatus.textContent,/已保存在本机/);
  const feedbackRow={id:'feedback-1',category:'wrong',state:'open',version:1,created:1794280000,content_available:true,sources_changed:false,note:'反馈专用说明',resolution:'',snapshot:{question:'反馈专用问题',answer:'反馈专用回答',topic:'测试活动',include_outdated:false,sources:[],answer_source_ids:[],source_usage:'model_reported'}};
  context.fetch=async()=>({ok:true,json:async()=>feedbackRow});
  await vm.runInContext("sharedFeedbackDetail('feedback-1')",context);
  assert.ok(ids.feedbackDetail.children.some(child=>child.textContent.includes('反馈专用问题')));
  const retryFeedback=ids.feedbackDetail.children.find(child=>child.textContent==='把原问题带回问答');
  context.fetch=async()=>{throw Error('bringing back a question must not submit it');};
  retryFeedback.onclick();
  assert.equal(ids.sharedQuestion.value,'测试活动，反馈专用问题');
  assert.equal(ids.viewShared.hidden,false);
  assert.equal(ids.sharedPageQuestion.hidden,false);
  assert.ok(!JSON.stringify(saved).includes('反馈专用'));
  context.fetch=async()=>({ok:true,json:async()=>({...feedbackRow,content_available:false})});
  await vm.runInContext("sharedFeedbackDetail('feedback-1')",context);
  assert.ok(!ids.feedbackDetail.children.some(child=>child.textContent.includes('反馈专用')),'a redacted detail never renders its snapshot or note');
  let releaseFeedback;
  context.fetch=()=>new Promise(resolve=>{releaseFeedback=resolve;});
  const lateFeedback=vm.runInContext("sharedFeedbackDetail('feedback-1')",context);
  vm.runInContext('sharedLock()',context);
  releaseFeedback({ok:true,json:async()=>feedbackRow});await lateFeedback;
  assert.equal(ids.feedbackDetail.children.length,0,'logout discards late feedback details');
  assert.equal(ids.answerFeedbackForm.hidden,true);

  // Display navigation preserves typed content and pending review; it never records or submits.
  context.fetch=async()=>{throw new Error('tab navigation must not submit data');};
  const navigationMicCalls=micCalls;
  ids.sharedQuestion.value='尚未发送的问题';ids.sharedMemoryText.value='尚未保存的记忆';ids.sharedAgentConsent.checked=true;
  vm.runInContext("sharedState.token='navigation-test';sharedState.space={id:'s',name:'测试空间',role:'owner'};sharedAgentDraft={kind:'preview',data:{token:'unsaved'},space:'s',auth:'navigation-test'};showWorkspacePage('shared');showSharedPage('library');",context);
  assert.equal(ids.viewShared.hidden,false);assert.equal(ids.viewChat.hidden,true);assert.equal(ids.sharedPageLibrary.hidden,false);assert.equal(ids.sharedPageQuestion.hidden,true);
  ids.navPersonal.onclick();ids.navShared.onclick();ids.navSharedQuestion.onclick();
  assert.equal(ids.sharedQuestion.value,'尚未发送的问题');assert.equal(ids.sharedMemoryText.value,'尚未保存的记忆');assert.equal(ids.sharedAgentConsent.checked,true);
  assert.equal(vm.runInContext('sharedAgentDraft.data.token',context),'unsaved');
  assert.equal(ids.workspaceReviewDraft.hidden,false);
  ids.navDiagnostics.onclick();assert.equal(ids.viewDiagnostics.hidden,false);assert.equal(ids.conversationDiagnosticsPanel.open,false,'voice details stay collapsed below the overview');
  ids.navChat.onclick();assert.equal(ids.conversationDiagnosticsPanel.open,false);
  let prevented=false;ids.navChat.onkeydown({key:'ArrowRight',preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(ids.viewPersonal.hidden,false);assert.equal(ids.navPersonal['aria-selected'],'true');assert.equal(ids.navChat.tabIndex,-1);
  ids.navPersonal.onkeydown({key:'End',preventDefault(){}});assert.equal(ids.viewDiagnostics.hidden,false);
  vm.runInContext("showSharedEditor()",context);assert.equal(ids.sharedEditorDisclosure.open,true);assert.equal(ids.sharedPageLibrary.hidden,false);
  ids.workspaceReviewDraft.onclick();assert.equal(ids.sharedPageQuestion.hidden,false);
  assert.equal(micCalls,navigationMicCalls,'navigation must never start a microphone');
  const html=fs.readFileSync('static/index.html','utf8'),allIds=[...html.matchAll(/id="([^\"]+)"/g)].map(m=>m[1]);
  assert.equal(new Set(allIds).size,allIds.length,'all legacy and new controls have unique IDs');
  for(const page of ['viewChat','viewPersonal','viewShared','viewSpeech','viewDiagnostics','sharedPageQuestion','sharedPageLibrary','sharedPageImport','sharedPageMembers'])assert.ok(ids[page]);
  // Each selected profile restores only its own persisted text conversation.
  context.fetch=async()=>({ok:true,json:async()=>({memories:[]})});
  saved['xiaoban-history-alice']=JSON.stringify([{role:'user',content:'甲的历史问题'},{role:'assistant',content:'甲的历史回答'}]);
  saved['xiaoban-history-bob']=JSON.stringify([{role:'user',content:'乙的历史问题'}]);
  saved['xiaoban-history']=JSON.stringify([{role:'user',content:'旧访客问题'}]);
  vm.runInContext("switchProfile('');switchProfile('alice');",context);
  assert.equal(vm.runInContext('messages[0].content',context),'甲的历史问题');
  vm.runInContext("switchProfile('bob')",context);
  assert.equal(vm.runInContext('JSON.stringify(messages)',context),saved['xiaoban-history-bob']);
  vm.runInContext("switchProfile('alice')",context);
  assert.equal(vm.runInContext('messages.length',context),2);
  vm.runInContext("applySpeaker({matched:false,reason:'unknown'})",context);
  assert.equal(vm.runInContext('messages.length',context),0,'an unknown speaker must not restore any saved history');
  saved['xiaoban-history-alice']='not json';
  vm.runInContext("switchProfile('alice')",context);
  assert.equal(vm.runInContext('messages.length',context),0);
  saved['xiaoban-history-alice']=JSON.stringify([null,{role:'system',content:'bad'},{role:'user',content:'有效历史',image:'private'}]);
  assert.equal(vm.runInContext("JSON.stringify(readChatHistory('alice'))",context),JSON.stringify([{role:'user',content:'有效历史'}]));
  vm.runInContext("showWorkspacePage('chat');showMemoryReview({target:{id:'alice',name:'甲'},token:'visible-review',content:'新内容',original:null,conflicts:[],stale:false,can_append:true})",context);
  assert.equal(ids.viewPersonal.hidden,false,'personal confirmation must be visible from the chat page');
  assert.equal(ids.memoryReviewPanel.hidden,false);
  vm.runInContext("sharedState.space={id:'count-space',name:'测试空间',role:'owner'};sharedState.memories=[{id:'x',content:'筛选测试',status:'valid',visibility:'all'}]",context);
  ids.sharedSearch.value='';ids.sharedStatusFilter.value='pending';
  vm.runInContext('sharedRenderMemories()',context);
  assert.match(ids.sharedCount.textContent,/匹配 0 条/);
  assert.match(ids.sharedMemoryList.children[0].textContent,/筛选条件/);
  // Restoring archives requires an explicit, current-session preview and both acknowledgments.
  vm.runInContext("sharedState.token='backup-auth';sharedState.account={id:'owner',name:'管理员'};sharedState.space={id:'source',name:'原空间',role:'owner'};sharedState.asking=false;sharedState.busy=false;sharedBackupClear();sharedControls();showSharedPage('backup')",context);
  assert.equal(ids.sharedPageBackup.hidden,false);
  assert.equal(ids.sharedBackupExport.disabled,true);
  ids.sharedBackupExportConsent.checked=true;ids.sharedBackupExportConsent.onchange();
  assert.equal(ids.sharedBackupExport.disabled,false);
  vm.runInContext("sharedState.space.role='reader';sharedControls()",context);
  assert.equal(ids.sharedBackupExport.disabled,true,'readers cannot export full private space');
  vm.runInContext("sharedState.space.role='owner'",context);
  const archive={space:{name:'原空间'},accounts:[{id:'source-owner',name:'原管理员'}],sources:[{name:'原文.txt',text:'虚构原文依据'}],history:[{changed:'2026-11-12',actor:'source-owner',before:null,after:{content:'恢复的记忆',status:'pending'}}]};
  const backupCounts={memories:1,history:1,sources:1,evidence:1,replacements:0,members:1};
  ids.sharedBackupFile.files=[{size:200,text:async()=>JSON.stringify(archive)}];ids.sharedBackupFile.onchange();
  context.fetch=async(path,options)=>{
    calls.push({path,data:JSON.parse(options.body)});
    if(path.endsWith('/inspect'))return{ok:true,json:async()=>({name:'原空间',accounts:[{id:'source-owner',name:'原管理员',role:'owner',owner:true,target_name:'管理员'}],counts:backupCounts})};
    if(path.endsWith('/preview'))return{ok:true,json:async()=>({token:'backup-preview',name:'恢复空间',kind:'team',counts:backupCounts,mapping:[{source:'原管理员',target:'管理员',role:'owner'}],memories:[{content:'恢复的记忆',status:'pending',event_date:'',author:'管理员',edit_policy:'author',visibility:'selected',viewers:['管理员']}]})};
    if(path.endsWith('/cancel'))return{ok:true,json:async()=>({cancelled:true})};
    throw new Error('Unexpected backup request '+path);
  };
  await ids.sharedBackupInspect.onclick();
  assert.match(ids.sharedBackupStatus.textContent,/读取完成/);
  assert.equal(vm.runInContext('backupState.accounts[0].input.disabled',context),true,'source owner maps to current operator');
  await ids.sharedBackupPreview.onclick();
  assert.equal(ids.sharedBackupReview.hidden,false);
  assert.equal(ids.sharedBackupConfirm.disabled,true);
  ids.sharedBackupConfirmData.checked=true;ids.sharedBackupConfirmData.onchange();
  assert.equal(ids.sharedBackupConfirm.disabled,true);
  ids.sharedBackupConfirmPermissions.checked=true;ids.sharedBackupConfirmPermissions.onchange();
  assert.equal(ids.sharedBackupConfirm.disabled,false);
  ids.sharedBackupName.value='新名字';ids.sharedBackupName.oninput();
  assert.equal(ids.sharedBackupConfirm.disabled,true,'renaming invalidates reviewed scope');
  assert.equal(ids.sharedBackupReview.hidden,true);
  assert.equal(vm.runInContext('backupState.plan',context),null);
  const originalBackupCalls=calls.length;await ids.sharedBackupPreview.onclick();ids.sharedBackupCancel.onclick();
  assert.ok(calls.slice(originalBackupCalls).every(c=>!c.path.endsWith('/confirm')),'cancel performs no restore');
  let releaseBackup;
  context.fetch=()=>new Promise(resolve=>{releaseBackup=resolve;});
  ids.sharedBackupFile.files=[{size:200,text:async()=>JSON.stringify(archive)}];
  const inspectPending=ids.sharedBackupInspect.onclick();await new Promise(r=>setImmediate(r));
  vm.runInContext('sharedLock()',context);
  releaseBackup({ok:true,json:async()=>({name:'过时备份',accounts:[],counts:backupCounts})});await inspectPending;
  assert.equal(vm.runInContext('backupState.document',context),null,'late inspection after logout must not restore private content');
  assert.equal(ids.sharedBackupReview.hidden,true);
  assert.equal(ids.sharedBackupMapping.children.length,0);
  // Recovery shows a frozen before/after preview and never writes until both acknowledgments.
  vm.runInContext("sharedState.token='recovery-auth';sharedState.account={id:'owner',name:'管理员'};sharedState.space={id:'recovery-space',name:'恢复测试',role:'owner',version:1};sharedState.busy=false;sharedState.asking=false;sharedRecoveryClear();sharedRecoveryStart({operation:'history_restore',id:'m1',version:3,history_id:1},'恢复历史版本')",context);
  assert.equal(ids.sharedPageBackup.hidden,false);
  assert.equal(ids.sharedRecoveryEditor.hidden,false);
  assert.equal(calls.filter(c=>c.path==='/api/shared/recovery/confirm').length,0);
  const beforeRecovery={id:'m1',content:'当前内容',status:'valid',event_date:'',source_note:'',status_reason:'',author_name:'管理员',visibility:'selected',edit_policy:'author',audience:['管理员']};
  const afterRecovery={...beforeRecovery,content:'历史内容',status:'pending'};
  let recoveryConfirmCalls=0;
  context.fetch=async(path,options)=>{
    const data=options?.body?JSON.parse(options.body):{};calls.push({path,data});
    if(path.endsWith('/recovery/preview'))return{ok:true,json:async()=>({token:'recovery-preview-123456789',space_id:'recovery-space',space_name:'恢复测试',actor_name:'管理员',reason:data.reason,operation:'history_restore',before:[beforeRecovery],after:[afterRecovery],conflicts:[{content:'可能冲突的记录'}],removed_viewers:[],history_count:0,source:null})};
    if(path.endsWith('/recovery/cancel'))return{ok:true,json:async()=>({cancelled:true})};
    if(path.endsWith('/recovery/confirm')){recoveryConfirmCalls++;return{ok:true,json:async()=>({applied:true,operation:'history_restore'})};}
    if(path.endsWith('/spaces/list'))return{ok:true,json:async()=>({account:{id:'owner',name:'管理员'},spaces:[{id:'recovery-space',name:'恢复测试',role:'owner',version:2}]})};
    if(path.endsWith('/memories/list'))return{ok:true,json:async()=>({space:{id:'recovery-space',name:'恢复测试',kind:'team',role:'owner',version:2},memories:[]})};
    if(path.endsWith('/members/list'))return{ok:true,json:async()=>({members:[]})};
    throw new Error('Unexpected recovery call '+path);
  };
  ids.sharedRecoveryReason.value='纠正误改';await ids.sharedRecoveryPreview.onclick();
  assert.equal(ids.sharedRecoveryReview.hidden,false);
  assert.equal(ids.sharedRecoveryConfirm.disabled,true);
  ids.sharedRecoveryConsent.checked=true;ids.sharedRecoveryScope.checked=true;ids.sharedRecoveryScope.onchange();
  assert.equal(ids.sharedRecoveryConfirm.disabled,true,'conflicting materials need separate acknowledgment');
  await ids.sharedRecoveryConfirm.onclick();assert.equal(recoveryConfirmCalls,0);
  ids.sharedRecoveryConflictConsent.checked=true;ids.sharedRecoveryConflictConsent.onchange();
  assert.equal(ids.sharedRecoveryConfirm.disabled,false);
  await ids.sharedRecoveryConfirm.onclick();assert.equal(recoveryConfirmCalls,1);
  assert.equal(vm.runInContext('sharedState.busy',context),false,'successful recovery refresh must release the operation lock');
  assert.equal(ids.sharedPageLibrary.hidden,false,'success returns to the current space library');
  assert.equal(ids.sharedRecoveryReview.hidden,true);
  vm.runInContext("sharedRecoveryStart({operation:'history_restore',id:'m1',version:3,history_id:1},'另一个恢复')",context);
  ids.sharedRecoveryReason.value='第二次纠正';await ids.sharedRecoveryPreview.onclick();
  ids.sharedRecoveryReason.value='更改说明后重新确认';ids.sharedRecoveryReason.oninput();
  assert.equal(vm.runInContext('recoveryState.plan',context),null);
  assert.equal(ids.sharedRecoveryConfirm.disabled,true,'editing the reason discards the frozen preview');
  ids.sharedRecoveryCancel.onclick();assert.equal(recoveryConfirmCalls,1,'cancel creates no extra confirmation');
  vm.runInContext("sharedRecoveryStart({operation:'history_restore',id:'m1',version:3,history_id:1},'异步预览')",context);
  let resolveRecovery;
  context.fetch=()=>new Promise(resolve=>{resolveRecovery=resolve;});
  ids.sharedRecoveryReason.value='退出期间的请求';const recoveryPending=ids.sharedRecoveryPreview.onclick();
  vm.runInContext('sharedLock()',context);
  resolveRecovery({ok:true,json:async()=>({token:'late-preview',before:[beforeRecovery],after:[afterRecovery]})});await recoveryPending;
  assert.equal(vm.runInContext('recoveryState.plan',context),null);
  assert.equal(ids.sharedRecoveryReview.hidden,true,'logout must hide late private recovery results');
  assert.equal(ids.sharedRecoveryReviewBody.children.length,0);
  // Personal semantic results are ephemeral, profile scoped and never auto-played.
  context.fetch=async(path,options)=>{
    const data=options?.body?JSON.parse(options.body):{};calls.push({path,data});
    if(path==='/api/memory/ask')return{ok:true,json:async()=>({profile:{id:'alice',name:'甲'},answer:'你喜欢燕麦拿铁。',sources:[{id:'personal-one',reference:1,content:'我喜欢燕麦拿铁。',source:'manual',created:null,updated:null},{id:'personal-two',reference:2,content:'周末去慢跑。',source:'manual'}],answer_source_ids:['personal-one'],source_usage:'model_reported',retrieval:{strategy:'hybrid',hit_count:2,elapsed_ms:12,fallback:false}})};
    if(path==='/api/memory/list')return{ok:true,json:async()=>({memories:[]})};
    if(path==='/api/turn/cancel')return{ok:true,json:async()=>({cancelled:true})};
    if(path==='/api/diagnostics/trace')return{ok:true,json:async()=>({steps:[],available:true})};
    throw new Error('Unexpected personal query '+path);
  };
  vm.runInContext("busy=false;recording=false;enrolling=false;wakePhase='';memoryBusy=false;profiles=[{id:'alice',name:'甲'},{id:'bob',name:'乙'}];switchProfile('alice');",context);
  await new Promise(r=>setImmediate(r));
  ids.personalMemoryQuestion.value='我喜欢什么饮品？';const queriesBefore=calls.length;
  await ids.personalMemoryAsk.onclick();
  assert.equal(ids.personalMemorySources.children.length,1,'only cited memories appear under actual references');
  assert.equal(ids.personalMemoryCandidates.children.length,1,'uncited candidate is separate');
  assert.match(ids.personalMemoryResultInfo.textContent,/语义与文字混合/);
  assert.ok(calls.slice(queriesBefore).every(c=>c.path!=='/api/speak'),'personal text query must not synthesize speech');
  assert.ok(!JSON.stringify(saved).includes('personal-one'),'source evidence is never saved in browser history');
  vm.runInContext("switchProfile('bob')",context);await new Promise(r=>setImmediate(r));
  assert.equal(ids.personalMemoryAnswer.textContent,'');assert.equal(ids.personalMemorySources.children.length,0);
  assert.equal(ids.personalMemoryQuestion.value,'');
  let latePersonal;
  context.fetch=async(path)=>{
    if(path==='/api/memory/ask')return new Promise(resolve=>{latePersonal=resolve;});
    return{ok:true,json:async()=>path==='/api/memory/list'?{memories:[]}:{steps:[],available:true}};
  };
  ids.personalMemoryQuestion.value='乙的饮品';const personalPending=ids.personalMemoryAsk.onclick();await new Promise(r=>setImmediate(r));
  vm.runInContext("switchProfile('alice')",context);
  latePersonal({ok:true,json:async()=>({profile:{id:'bob',name:'乙'},answer:'不能显示给甲的迟到内容',sources:[]})});await personalPending;
  assert.equal(ids.personalMemoryAnswer.textContent,'','profile switch discards late private answers');
  assert.equal(vm.runInContext('busy',context),false);
  const ordinaryRow={item:new Element()};context.personalOrdinaryRow=ordinaryRow;
  vm.runInContext("renderPersonalMemoryContext(personalOrdinaryRow,{profile:{name:'甲'},sources:[],retrieval:{strategy:'lexical',hit_count:0,elapsed_ms:1,fallback:true}})",context);
  assert.equal(ordinaryRow.item.children.length,1);
  assert.match(ordinaryRow.item.children[0].children[0].textContent,/未找到相关长期记忆/);
  // Batch views preserve the same permission/session boundary and require two confirmations.
  vm.runInContext("sharedState.token='batch-token';sharedState.account={id:'owner',name:'测试管理员'};sharedState.space={id:'batch-space',name:'批次测试',kind:'team',role:'owner',version:1};sharedState.members=[{id:'owner',name:'测试管理员',role:'owner'}];sharedState.busy=false;sharedState.asking=false;sharedBatchesClear();sharedControls();",context);
  const batchRecordFixture={id:'m-batch',version:1,content:'虚构活动十点集合',status:'pending',event_date:'2026-11-21',source_name:'通知.txt',source_note:'活动资料',author_name:'测试管理员',audience:['测试管理员'],visibility:'all',edit_policy:'author',state:'active',eligible:true,has_evidence:true,can_read_full:true};
  const batchFixture={id:'batch-1',name:'<script>只是批次名称</script>',actor_name:'测试管理员',created:'2026-11-13 00:00:00',can_undo:true,eligible_count:1,visible_count:1,active_count:1,trash_count:0};
  const batchRequests=[];let delayedBatchSource;
  context.fetch=async(path,options)=>{
    const data=JSON.parse(options.body);batchRequests.push({path,data});
    if(path==='/api/shared/batches/list')return{ok:true,json:async()=>({batches:[batchFixture],untracked_count:2})};
    if(path==='/api/shared/batches/detail')return{ok:true,json:async()=>({batch:batchFixture,items:[batchRecordFixture],events:[]})};
    if(path==='/api/shared/batches/preview')return{ok:true,json:async()=>({token:'batch-preview',space_id:'batch-space',space_name:'批次测试',actor_name:'测试管理员',batch:batchFixture,reason:data.reason,withdraw:[batchRecordFixture],retained:[]})};
    if(path==='/api/shared/batches/source')return new Promise(resolve=>{delayedBatchSource=resolve;});
    if(path==='/api/shared/batches/cancel'||path==='/api/shared/auth/logout')return{ok:true,json:async()=>({cancelled:true})};
    throw new Error('Unexpected batch request '+path);
  };
  await ids.sharedBatchLoad.onclick();assert.match(ids.sharedBatchStatus.textContent,/2 条历史/);
  assert.match(ids.sharedBatchList.children[0].children[0].textContent,/<script>/,'batch labels are inert text');
  await vm.runInContext("batchOpen('batch-1')",context);
  assert.equal(ids.sharedBatchItems.children.length,1);
  ids.sharedBatchReason.value='错导资料';ids.sharedBatchReason.oninput();await ids.sharedBatchPreview.onclick();
  assert.equal(ids.sharedBatchConfirm.disabled,true);
  ids.sharedBatchConsent.checked=true;ids.sharedBatchConsent.onchange();assert.equal(ids.sharedBatchConfirm.disabled,true);
  await ids.sharedBatchConfirm.onclick();assert.equal(batchRequests.filter(r=>r.path.endsWith('/confirm')).length,0);
  ids.sharedBatchScope.checked=true;ids.sharedBatchScope.onchange();assert.equal(ids.sharedBatchConfirm.disabled,false);
  ids.sharedBatchReason.value='修正撤销原因';ids.sharedBatchReason.oninput();assert.equal(ids.sharedBatchConfirm.disabled,true);
  assert.equal(vm.runInContext('batchState.plan',context),null,'changing reason invalidates the frozen preview');
  context.batchSourceFixture=batchRecordFixture;
  const pendingBatchSource=vm.runInContext('batchSource(batchSourceFixture,true)',context);await new Promise(r=>setImmediate(r));
  vm.runInContext("sharedLock('退出批次测试');",context);
  delayedBatchSource({ok:true,json:async()=>({available:true,source_name:'迟到原文',captured_content:'私密资料',quotes:[],source_text:'不得在退出后出现的资料'})});
  await pendingBatchSource;
  assert.equal(ids.sharedBatchSource.hidden,true);assert.equal(ids.sharedBatchSourceText.textContent,'');
  assert.equal(ids.sharedBatchItems.children.length,0);assert.equal(vm.runInContext('sharedState.busy',context),false);
  assert.ok(!JSON.stringify(saved).includes('私密资料'),'batch material is never persisted in localStorage');
  // Unified diagnostics distinguish searched candidates from validated citations.
  const overviewText=node=>[node.textContent,...(node.children||[]).map(overviewText)].join('\n');
  vm.runInContext("sharedState.token='overview-token';sharedState.account={id:'o',name:'诊断用户'};sharedState.space={id:'overview-space',name:'诊断测试',kind:'team',role:'owner',version:1};sharedState.busy=false;sharedState.asking=false;diagnosticOverview.clearShared();diagnosticOverview.clearLocal();",context);
  const overviewA={id:'oa',version:1,available:true,content:'已引用的北门安排',status:'pending',event_date:'2026-11-21',source_note:'通知A',has_evidence:false};
  const overviewB={id:'ob',version:1,available:true,content:'没有被引用的接待资料',status:'valid',event_date:'',source_note:'通知B',has_evidence:false};
  const overviewRow={id:'overview-round',started:1,action:'read',status:'done',reason:'ok',elapsed_ms:2400,search_performed:true,citation_checked:true,citation_status:'model_reported',materials:{searched:[overviewA,overviewB],read:[overviewA],cited:[overviewA]},models:[{model:'test-qwen',backend:'ollama',route:'local_service'}],usage:{model_calls:2,reported_calls:0},steps:[]};
  context.fetch=async(path)=>({ok:true,json:async()=>path==='/api/shared/diagnostics/overview'?{available:true,records:[overviewRow]}:{}});
  ids.overviewScope.value='all';await ids.overviewRefresh.onclick();
  let overviewRendered=overviewText(ids.overviewDetail);
  assert.match(overviewRendered,/2 条（不等于引用）/);assert.match(overviewRendered,/1 条/);assert.match(overviewRendered,/test-qwen/);assert.match(overviewRendered,/2.40 秒/);
  const citedOverview=ids.overviewDetail.children.find(c=>c.children?.[0]?.textContent==='实际引用的记忆');
  assert.match(overviewText(citedOverview),/已引用的北门安排/);assert.ok(!overviewText(citedOverview).includes('没有被引用的接待资料'));
  vm.runInContext("diagnosticOverview.startLocal('local-round','chat','普通对话问题');diagnosticOverview.sourcesLocal('local-round',{profile:{id:'a'},sources:[{id:'only-candidate',content:'普通聊天候选',status:'recorded'}]},true);diagnosticOverview.finishLocal('local-round');diagnosticOverview.traceLocal('local-round',{available:true,steps:[],models:[],usage:{model_calls:0,reported_calls:0}})",context);
  ids.overviewScope.value='local';ids.overviewScope.onchange();overviewRendered=overviewText(ids.overviewDetail);
  assert.match(overviewRendered,/普通聊天未核对引用/);assert.match(overviewRendered,/普通聊天候选/);assert.match(overviewRendered,/本轮未调用模型/);
  const localCited=ids.overviewDetail.children.find(c=>c.children?.[0]?.textContent==='实际引用的记忆');
  assert.ok(!overviewText(localCited).includes('普通聊天候选'));
  vm.runInContext("diagnosticOverview.finishLocal('local-round','cancelled')",context);
  assert.match(overviewText(ids.overviewDetail),/已完成/,'stopping playback after a text answer does not cancel completed text diagnostics');
  vm.runInContext("diagnosticOverview.startLocal('failed-round','personal','失败测试');diagnosticOverview.finishLocal('failed-round','failed');diagnosticOverview.traceLocal('failed-round',{available:true,steps:[],models:[{model:'failed-model',route:'remote_service'}],usage:{model_calls:1},failure:{code:'model_http',http_status:429,stage:'model_wait'}})",context);
  overviewRendered=overviewText(ids.overviewDetail);assert.match(overviewRendered,/HTTP 429/);assert.match(overviewRendered,/failed-model/);
  vm.runInContext("diagnosticOverview.startLocal('rule-round','chat','今天几号');diagnosticOverview.sourcesLocal('rule-round',{profile:null,sources:[],answer_source_ids:[],source_usage:'program_selected'});diagnosticOverview.finishLocal('rule-round');diagnosticOverview.traceLocal('rule-round',{available:true,steps:[{stage:'routing',status:'done',elapsed_ms:1}],models:[],usage:{model_calls:0}})",context);
  assert.match(overviewText(ids.overviewDetail),/本轮未执行记忆检索/);assert.match(overviewText(ids.overviewDetail),/0 条/);
  ids.overviewErrors.checked=true;ids.overviewErrors.onchange();assert.equal(ids.overviewList.children.length,1);ids.overviewErrors.checked=false;
  vm.runInContext("diagnosticOverview.clearLocal()",context);ids.overviewScope.value='all';
  let lateOverview;
  context.fetch=async(path)=>path==='/api/shared/diagnostics/overview'?new Promise(resolve=>{lateOverview=resolve;}):({ok:true,json:async()=>({})});
  const pendingOverview=ids.overviewRefresh.onclick();await new Promise(r=>setImmediate(r));
  vm.runInContext("sharedLock('退出诊断测试')",context);
  lateOverview({ok:true,json:async()=>({available:true,records:[{...overviewRow,materials:{searched:[{...overviewA,content:'退出后不得出现的私密资料'}],read:[],cited:[]}}]})});await pendingOverview;
  assert.equal(ids.overviewList.children.length,0);assert.ok(!overviewText(ids.overviewDetail).includes('退出后不得出现'));
  assert.ok(!JSON.stringify(saved).includes('普通聊天候选'),'overview bodies are never persisted in browser storage');
  console.log('Frontend checks passed: permissions, camera, profile isolation, cancellation, PCM and disabled speech.');
})().catch(e=>{console.error(e);process.exitCode=1;});
