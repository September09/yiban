// Logic-level checks with DOM/audio stubs; this does not launch or control a browser.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
class Element{
  constructor(){this.children=[];this.dataset={};this.textContent='';this.className='';this.hidden=false;this.checked=false;this.disabled=false;this.value='';this.classList={toggle(){},remove(){}};}
  focus(){}click(){this.clicked=true;}remove(){this.removed=true;}
  append(...children){this.children.push(...children);}replaceChildren(){this.children=[];}
  setAttribute(k,v){this[k]=v;}querySelector(){return new Element();}
  querySelectorAll(selector){const all=this.children.flatMap(c=>[c,...(c.querySelectorAll?.('input')||[])]).filter(c=>c.type==='checkbox');return selector.includes(':checked')?all.filter(c=>c.checked):all;}
  animate(frames,options){this.animation={frames,options};return{cancel(){}};}
}
const ids=Object.fromEntries([...fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const calls=[],saved={};let micCalls=0;
const context=vm.createContext({
  document:{getElementById:id=>{assert.ok(ids[id],id);return ids[id];},createElement:()=>new Element(),createTextNode:text=>({textContent:text}),querySelectorAll:()=>[],body:{className:''}},
  localStorage:{getItem:()=>null,setItem:(k,v)=>saved[k]=v,removeItem:k=>delete saved[k]},
  window:{matchMedia:()=>({matches:false}),addEventListener(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>{micCalls++;throw new Error('No device in logic test');}}},
  fetch:async(path,options)=>{
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
  console,URL, btoa:s=>Buffer.from(s,'binary').toString('base64')
});
(async()=>{
  vm.runInContext(fs.readFileSync('static/conversation-diagnostics.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/personal.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/memory-import.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/memory-review.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-voice.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/shared-evidence.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/material-import.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/speech-settings.js','utf8'),context);
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

  // Shared voice consumes ASR receipts, keeps personal history intact, and arms only after playback.
  const audioInstances=[];
  context.Audio=class{constructor(){audioInstances.push(this);}async play(){}pause(){}};
  context.URL={createObjectURL:()=> 'blob:voice-test',revokeObjectURL(){}};
  vm.runInContext("pauseWake=async()=>{wakePhase='';};resumeWake=async mode=>{wakePhase=mode||'wake';};monitorInterrupt=async()=>{};sharedLoadCurrent=async()=>{};ready=true;asrReady=true;wakeReady=true;speakerReady=true;ttsReady=true;busy=false;recording=false;enrolling=false;wakeEnabled=true;sharedState.token='voice-token';sharedState.account={id:'a',name:'测试账号'};sharedState.space={id:'s',name:'测试家庭',kind:'family',role:'owner',owner:'a',version:1};sharedVoiceState.session='voice-session';sharedVoiceState.token='voice-token';sharedVoiceState.profile='alice';messages=[{role:'user',content:'原个人聊天'}];controls()",context);
  const originalPersonalHistory=vm.runInContext('JSON.stringify(messages)',context);
  let voiceWrites=0,playedCalls=0,cancelCalls=0;
  const voiceFetch=async(path,options)=>{
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
  console.log('Frontend checks passed: permissions, camera, profile isolation, cancellation, PCM and disabled speech.');
})().catch(e=>{console.error(e);process.exitCode=1;});
