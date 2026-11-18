// Logic-level checks with DOM/audio stubs; this does not launch or control a browser.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
class Element{
  constructor(){this.children=[];this.dataset={};this.style={};this.textContent='';this.className='';this.hidden=false;this.checked=false;this.disabled=false;this.value='';this.classList={toggle(){},remove(){}};}
  get value(){return this._value||'';}set value(v){this._value=String(v??'');}
  scrollIntoView(){}focus(){}click(){this.clicked=true;}remove(){this.removed=true;}
  append(...children){this.children.push(...children);}replaceChildren(){this.children=[];}
  setAttribute(k,v){this[k]=v;}querySelector(){return new Element();}
  querySelectorAll(selector){const all=this.children.flatMap(c=>[c,...(c.querySelectorAll?.('input')||[])]).filter(c=>c.type==='checkbox');return selector.includes(':checked')?all.filter(c=>c.checked):all;}
  animate(frames,options){this.animation={frames,options};return{cancel(){}};}
}
const ids=Object.fromEntries([...fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const calls=[],saved={};let micCalls=0,heldReply=null,sharedFailure=false,v2Saved=false,voiceFailure=false;
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
    if(path==='/api/shared/spaces/list')return{ok:true,json:async()=>({account:{id:'owner',name:'测试账号'},spaces:context.mockSpaces||[]})};
    if(path==='/api/shared/memories/list')return{ok:true,json:async()=>({space:(context.mockSpaces||[]).find(s=>s.id===JSON.parse(options.body).space_id),memories:[]})};
    if(path==='/api/shared/members/list')return{ok:true,json:async()=>({members:[]})};
    if(path==='/api/voice-memory/ask'&&voiceFailure)return{ok:false,status:403,json:async()=>({error:'未确认本人声音，没有读取记忆。',voice_memory:true})};
    if(path==='/api/shared/agent/ask'||path==='/api/memories/ask'||path==='/api/voice-memory/ask'){if(heldReply)await heldReply;return sharedFailure?{ok:false,status:401,json:async()=>({error:'会话已失效'})}:{ok:true,json:async()=>sharedResult};}
    if(path==='/api/memories/evidence'&&JSON.parse(options.body).id==='mem')return{ok:true,json:async()=>({available:true,quotes:[{location:'原文第1行',text:'虚构原文摘录'}]})};
    if(path==='/api/memories/reset'||path==='/api/shared/ask/reset'||path==='/api/shared/auth/logout')return{ok:true,json:async()=>({})};
    if(path==='/api/status')return{json:async()=>({chat:true,asr:true,wake:true,vision:true})};
    if(path==='/api/turn/cancel')return{ok:true,json:async()=>({cancelled:true})};
    if(path==='/api/profiles/list')return{ok:true,json:async()=>({profiles:[]})};
    if(path==='/api/memory/list')return{ok:true,json:async()=>({memories:[]})};
    if(path==='/api/chat'){
      let consumed=false;return{ok:true,body:{getReader:()=>({read:async()=>consumed?{done:true}:(consumed=true,{done:false,value:new TextEncoder().encode(JSON.stringify({message:{content:'已收到'},done:true})+'\n')})})}};
    }
    if(path.startsWith('/api/memories/')){
      if(path.endsWith('/draft')){if(heldReply)await heldReply;context.lastDraft=JSON.parse(options.body);return{ok:true,json:async()=>({document:context.mockDraftDocument})};}
      if(path.endsWith('/validate')){if(heldReply)await heldReply;return{ok:true,json:async()=>({document:context.mockDocument})};}
      if(path==='/api/memories/preview')return{ok:true,json:async()=>({token:'review-token',scope_kind:'personal',scope_name:'个人 · 测试账号',items:context.mockDocument.records,warnings:[],options:{visibility:'selected'}})};
      if(path.endsWith('/confirm')){if(context.lastUpdate){const plan=context.lastUpdate;context.mockRows=context.mockRows.map(r=>r.id===plan.before.id?{...r,...plan.after,version:r.version+1}:r);context.lastUpdate=null;}v2Saved=true;return{ok:true,json:async()=>({saved:true,imported:1})};}
      if(path.endsWith('/update/preview')){const req=JSON.parse(options.body),before=context.mockRows.find(r=>r.id===req.id);context.lastUpdate={token:'update-token',scope_name:'测试团队',before,after:{...before,...req.changes},lifecycle:before.lifecycle,warnings:[]};return{ok:true,json:async()=>context.lastUpdate};}
      if(path.endsWith('/list'))return{ok:true,json:async()=>({scope_name:'个人 · 测试账号',records:context.mockRows||(v2Saved?[{...context.mockDocument.records[0],id:'record-new',version:1,lifecycle:'active',content:'虚构摘要',can_edit:true}]:[])})};
      if(path.endsWith('/reset'))return{ok:true,json:async()=>({reset:true})};
      if(path.endsWith('/discard'))return{ok:true,json:async()=>({discarded:true})};
      if(path.endsWith('/evidence')){if(context.evidenceWait)await context.evidenceWait;return context.evidenceFail?{ok:false,status:409,json:async()=>({error:'记忆已变化'})}:{ok:true,json:async()=>({available:true,quotes:[{source_name:'虚构文章',source_kind:'manual',text:'原文片段 <script>不会执行</script>'}]})};}
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
  vm.runInContext(fs.readFileSync('static/md-import.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/structured-memory.js','utf8'),context);


  await new Promise(r=>setImmediate(r));
  const run=s=>vm.runInContext(s,context);
  assert.equal(ids.v2Controls.disabled,true);
  assert.equal(ids.chatMemoryVersion,undefined,'不再提供新旧记忆切换');
  assert.equal(ids.send.disabled,false,'访客无需空间登录即可普通聊天');
  assert.equal(run('companionMemoryActive()'),false,'访客不进入需要授权的记忆查询');
  ids.autoplay.checked=false;
  await run("send('自我介绍一下')");
  assert.equal(calls.filter(c=>c.path==='/api/chat').length,1,'访客发送走普通对话');
  assert.equal(calls.filter(c=>c.path==='/api/memories/ask').length,0,'访客不会发起团队查询');
  assert.equal(calls.filter(c=>c.path==='/api/chat').at(-1).data.profile_id,'');
  run("sharedState.token='empty-token';sharedState.account={id:'empty-owner',name:'无空间账号'};sharedState.spaces=[];sharedState.space=null;sharedControls();");
  await run("send('没有空间也能聊天吗')");
  assert.equal(calls.filter(c=>c.path==='/api/chat').length,1,'已登录但无团队空间应查询账号自己的记忆');
  const emptyAsk=calls.filter(c=>c.path==='/api/memories/ask').at(-1);
  assert.equal(emptyAsk.data.memory_version,undefined);assert.equal(emptyAsk.data.space_id,undefined);
  assert.equal(emptyAsk.data.profile_id,undefined);assert.match(ids.chatMemoryHint.textContent,/自己的个人记忆/);
  assert.equal(run('companionMemoryActive()'),true);
  await run("companionMemorySetMode('chat')");
  assert.equal(run('companionMemoryActive()'),false);assert.match(ids.chatMemoryHint.textContent,/不参与本轮/);
  const accountRequests=calls.filter(c=>c.path==='/api/memories/ask').length;
  await run("send('现在随便聊聊')");
  assert.equal(calls.filter(c=>c.path==='/api/chat').length,2);
  assert.equal(calls.filter(c=>c.path==='/api/memories/ask').length,accountRequests,'显式普通聊天不读取账号记忆');

  await run("companionMemorySetMode('shared')");
  assert.equal(ids.send.disabled,false,'指定空间模式在无空间时也不锁死普通对话');
  await run("companionMemorySetMode('auto')");
  run("sharedState.token='test-token';sharedState.account={id:'owner',name:'测试账号'};sharedState.spaces=[{id:'team-a',name:'测试团队',version:1,role:'owner'}];sharedState.space=null;sharedControls();");
  context.mockSpaces=[{id:'team-a',name:'测试团队',version:1,role:'owner',kind:'team'},{id:'team-b',name:'另一个团队',version:1,role:'reader',kind:'team'}];
  await run('sharedRefresh()');
  assert.equal(run('sharedState.space.id'),'team-a','多个有权限空间默认选择第一个');
  run('sharedState.space=sharedState.spaces[1]');await run('sharedRefresh()');
  assert.equal(run('sharedState.space.id'),'team-b','已选择的有权限空间不会被刷新覆盖');
  assert.equal(ids.v2Controls.disabled,false);assert.equal(ids.send.disabled,false);
  await run("send('新版测试问题')");
  let ask=calls.filter(c=>c.path==='/api/memories/ask').at(-1);
  assert.equal(ask.data.memory_version,undefined);
  const firstEvidenceRow=ids.messages.children.at(-1);
  await run("send('那是谁制作的？')");
  const priorEvidenceCard=firstEvidenceRow.children[2].children[1].children[0];await priorEvidenceCard.children[3].onclick();assert.ok(priorEvidenceCard.children[4].textContent,'追问后上一轮仍可查看原文');
  assert.equal(calls.filter(c=>c.path==='/api/memories/ask').at(-1).data.conversation_id,'topic-test');
  ids.clear.onclick();assert.ok(calls.some(c=>c.path==='/api/memories/reset'),'新版新对话清除新版话题');assert.equal(ask.data.profile_id,undefined);assert.equal(ask.data.messages,undefined);
  await run("companionMemorySetMode('personal')");
  await run("send('自己的新版记忆')");
  ask=calls.filter(c=>c.path==='/api/memories/ask').at(-1);assert.equal(ask.data.scope_kind,'personal');assert.equal(ask.data.space_id,undefined);
  assert.equal(ids.chatMemorySpace.hidden,true);
  context.mockDraftDocument={schema:'yiban.memory-candidates.v2',batch_name:'手动文字记忆',entities:[],events:[],sources:[{key:'manual-source',kind:'manual',text:'我喜欢茉莉茶。'}],records:[{key:'manual-note',type:'quote',title:'饮茶偏好',summary:'我喜欢茉莉茶。',detail:'',participants:[],emotions:[],tags:[],evidence:[{text:'我喜欢茉莉茶。'}],verification:'pending',sensitivity:'personal',fact:null}]};
  const writesBefore=calls.filter(c=>/preview|confirm/.test(c.path)).length;
  ids.v2DraftTitle.value='饮茶偏好';ids.v2DraftText.value='我喜欢茉莉茶。';
  await ids.v2Draft.onclick();
  assert.equal(context.lastDraft.scope_kind,'personal');assert.equal(context.lastDraft.title,'饮茶偏好');
  assert.equal(context.lastDraft.content,'我喜欢茉莉茶。');
  assert.equal(calls.filter(c=>/preview|confirm/.test(c.path)).length,writesBefore,'生成文字候选不会自动预览或保存');
  assert.equal(ids.v2Candidates.children.length,1);assert.match(ids.v2DraftStatus.textContent,/尚未保存/);
  ids.v2DraftText.value='已改成另一条';ids.v2DraftText.oninput();
  assert.equal(ids.v2Candidates.children.length,0);assert.equal(ids.v2Preview.disabled,true,'编辑文字使旧候选失效');
  context.mockDocument={schema:'yiban.memory-candidates.v2',batch_name:'虚构',entities:[],events:[],records:[{key:'r',type:'reflection',title:'虚构条目',summary:'虚构摘要',detail:'',participants:[],emotions:[],tags:[],evidence:[{text:'依据'}],verification:'pending',sensitivity:'normal',fact:null}]};
  ids.v2Packet.value=JSON.stringify(context.mockDocument);await ids.v2Validate.onclick();
  assert.equal(ids.v2Candidates.children.length,1);assert.equal(v2Saved,false);
  await ids.v2Preview.onclick();assert.equal(v2Saved,false,'预览不能写入');assert.equal(ids.v2Confirm.disabled,true);
  ids.v2Consent.checked=true;ids.v2Consent.onchange();assert.equal(ids.v2Confirm.disabled,false);
  await ids.v2Confirm.onclick();assert.equal(v2Saved,true);assert.equal(ids.v2Records.children.length,1);
  // The review queue is derived only from the authorized listing and never writes on selection.
  const settle=()=>new Promise(r=>setImmediate(r));
  const qty={...context.mockDocument.records[0],id:'qty',key:'qty',title:'捐赠数量',version:3,lifecycle:'active',can_edit:true,entities:{group:'虚构班级'},verification_reason:'需核对：数量口径',fact:{predicate:'quantity',subject_key:'group',value:460,unit:'本',metric:'筛选合计'}};
  const score={...qty,id:'score',key:'score',title:'比分未记载',version:2,can_edit:false,fact:{predicate:'score',subject_key:'group',object_key:'other',value:{own:null,opponent:null,result:'loss'}}};
  context.mockRows=[qty,score,{...qty,id:'done',key:'done',title:'已确认数量',verification:'confirmed'}, {...qty,id:'ordinary',key:'ordinary',title:'普通回顾',fact:null,verification_reason:'按要求待核实'}];
  await ids.v2FocusRefresh.onclick();await settle();
  assert.match(ids.v2FocusCount.textContent,/待核验 3 条/);assert.equal(ids.v2FocusList.children.length,3);
  assert.equal(ids.v2EditTitle.value,'捐赠数量');assert.equal(ids.v2FocusConfirm.disabled,false);
  assert.ok(JSON.stringify(ids.v2FocusEvidence.children).includes('<script>不会执行</script>'),'原文按纯文字呈现');
  assert.ok(JSON.stringify(ids.v2FocusEvidence.children).includes('手动提供的资料'));
  assert.ok(!JSON.stringify(ids.v2FocusEvidence.children).includes('脱敏整理版'),'手动提供不代表已脱敏');
  let evidenceCall=calls.filter(c=>c.path.endsWith('/evidence')).at(-1);assert.equal(evidenceCall.data.id,'qty');assert.equal(evidenceCall.data.expected_version,3);
  const confirmCount=calls.filter(c=>c.path.endsWith('/confirm')).length;
  assert.equal(ids.v2Edit.hidden,true,'默认只显示问答，不显示编辑表单');assert.match(ids.v2FocusAnswer.textContent,/460本/,'候选答案必须显示结构化数量');assert.match(ids.v2FocusTitle.textContent,/筛选合计是多少/);
  ids.v2EditArchived.checked=true;await ids.v2FocusConfirm.onclick();await settle();assert.equal(calls.filter(c=>c.path.endsWith('/confirm')).length,confirmCount+1,'人工确认一次即可完成保存');
  let update=calls.filter(c=>c.path.endsWith('/update/preview')).at(-1);assert.equal(update.data.expected_version,3);assert.equal(update.data.changes.verification,'confirmed');assert.equal(update.data.changes.lifecycle,undefined,'快捷核实不传生命周期');assert.equal(update.data.changes.fact,undefined,'快捷核实不改数量');assert.equal(context.mockRows[0].lifecycle,'active');assert.equal(context.mockRows[0].verification,'confirmed');assert.equal(ids.v2Confirm.disabled,true);
  assert.equal(ids.v2EditTitle.value,'比分未记载','确认后自动下一条');assert.equal(ids.v2FocusConfirm.disabled,true,'只读记忆不能审核保存');assert.equal(ids.v2EditPreview.disabled,true);
  ids.v2FocusStatus.value='confirmed';ids.v2FocusSearch.value='捐赠';ids.v2FocusCategory.value='quantity';ids.v2FocusCategory.onchange();await settle();assert.equal(ids.v2FocusList.children.length,1);assert.equal(ids.v2EditTitle.value,'捐赠数量');
  ids.v2FocusModify.onclick();assert.equal(ids.v2Edit.hidden,false);assert.equal(ids.v2FocusConfirm.disabled,true,'编辑时不可静默确认旧答案');
  const beforeInvalid=calls.filter(c=>c.path.endsWith('/update/preview')).length;
  const numberInput=ids.v2FactFields.children.flatMap(e=>e.children||[]).find(e=>e.type==='number');numberInput.value='';await ids.v2EditPreview.onclick();assert.equal(calls.filter(c=>c.path.endsWith('/update/preview')).length,beforeInvalid);assert.match(ids.v2Status.textContent,/空白当成0/);
  numberInput.value='461';await ids.v2EditPreview.onclick();assert.equal(calls.filter(c=>c.path.endsWith('/update/preview')).at(-1).data.changes.fact.value,461);
  ids.v2FocusSearch.value='已确认';ids.v2FocusCategory.value='all';ids.v2FocusStatus.value='confirmed';ids.v2FocusStatus.onchange();await settle();assert.equal(ids.v2FocusList.children.length,1);assert.equal(ids.v2EditTitle.value,'已确认数量');
  ids.v2FocusSearch.value='普通';ids.v2FocusStatus.value='open';ids.v2FocusStatus.onchange();await settle();const wrongCount=calls.filter(c=>c.path.endsWith('/confirm')).length;await ids.v2FocusWrong.onclick();await settle();assert.equal(calls.filter(c=>c.path.endsWith('/confirm')).length,wrongCount+1);assert.equal(context.mockRows.find(r=>r.id==='ordinary').verification,'disputed');
  ids.v2FocusSearch.value='';context.evidenceFail=true;ids.v2FocusStatus.value='open';await ids.v2FocusRefresh.onclick();await settle();
  // Refresh clears evidence only after a save; choose a new version to force a fresh guarded read.
  context.mockRows[0]={...qty,version:4};await ids.v2FocusRefresh.onclick();await settle();assert.equal(ids.v2FocusConfirm.disabled,true);assert.ok(JSON.stringify(ids.v2FocusEvidence.children).includes('记忆已变化'));
  context.evidenceFail=false;let releaseEvidence;context.evidenceWait=new Promise(r=>releaseEvidence=r);context.mockRows[0]={...qty,version:5};await ids.v2FocusRefresh.onclick();run('sharedLock()');releaseEvidence();await settle();assert.equal(ids.v2FocusList.children.length,0);assert.equal(ids.v2FocusEvidence.children.length,0,'退出后迟到的原文不会回到界面');assert.equal(ids.v2EditTitle.value,'');assert.equal(ids.v2FocusTitle.textContent,'');assert.equal(ids.v2FactFields.children.length,0);context.evidenceWait=null;
  run("sharedState.token='test-token';sharedState.account={id:'owner',name:'测试账号'};sharedState.spaces=[{id:'team-a',name:'测试团队',version:1,role:'owner'}];sharedControls();");
  let release;heldReply=new Promise(r=>release=r);
  const late=ids.v2Validate.onclick();await new Promise(r=>setImmediate(r));run('sharedLock()');release();await late;
  assert.equal(ids.v2Candidates.children.length,0,'退出后丢弃迟到的候选');assert.equal(ids.v2Records.children.length,0);assert.equal(ids.v2Packet.value,'');assert.equal(ids.v2DraftTitle.value,'');assert.equal(ids.v2DraftText.value,'');
  assert.equal(micCalls,0);assert.equal(calls.filter(c=>/\/api\/(speak|listen|transcribe)/.test(c.path)).length,0);
  // Read-only v2 voice uses account permissions and keeps a completed topic
  // when playback is interrupted; it never submits a write or changes identity.
  heldReply=null;
  const originalVoiceFetch=context.fetch,players=[];
  context.Audio=class{
    constructor(){this.ended=false;this.paused=true;players.push(this);}
    async play(){this.paused=false;}
    pause(){this.paused=true;Promise.resolve().then(()=>this.onpause?.());}
  };
  context.fetch=async(path,options)=>{
    if(path==='/api/speak'){calls.push({path,data:JSON.parse(options.body),headers:options.headers});return{ok:true,blob:async()=>new Blob(['voice'])};}
    return originalVoiceFetch(path,options);
  };
  run("sharedState.token='voice-token';sharedState.account={id:'owner',name:'测试账号'};sharedState.spaces=[{id:'voice-team',name:'测试团队',version:1,role:'owner'}];sharedState.space=sharedState.spaces[0];companionMemory.mode='auto';ready=true;asrReady=true;wakeReady=true;ttsReady=true;sharedControls();$('autoplay').checked=true;$('speakerAuto').checked=true;");
  assert.equal(ids.mic.disabled,false);assert.equal(ids.autoplay.disabled,false);
  assert.equal(run('identifySpeechSpeaker()'),true,'语音记忆必须识别声纹，授权仍由服务器检查');
  const beforeNoProof=calls.length;
  await run("send('不带凭证的语音',{source:'manual'})");
  assert.equal(calls.slice(beforeNoProof).filter(c=>c.path.includes('/ask')).length,0,'缺少声纹凭证不降级成文字记忆查询');
  voiceFailure=true;
  await run("send('未知声音',{source:'manual',voiceReceipt:'unknown-speaker-receipt'})");
  assert.equal(run('sharedState.token'),'voice-token','声纹未匹配不把本人账号换成其他账号');
  assert.equal(ids.messages.children.length,1,'失败后清除之前的记忆与原文卡片');
  assert.match(ids.messages.children[0].children[1].textContent,/没有读取记忆/);
  assert.equal(players.length,0,'未知声音不朗读此前的私人回答');
  voiceFailure=false;
  const historyBeforeVoice=JSON.stringify(saved),beforeVoice=calls.length;
  const trace=run("conversationDiagnostics.start('manual')");
  context.voiceTrace=trace;
  let voiceDone=false;
  const speaking=run("send('这是谁制作的？',{source:'manual',traceId:voiceTrace,voiceReceipt:'server-voice-receipt'})").then(()=>{voiceDone=true;});
  for(let i=0;i<60&&!players[0]?.onended;i++)await new Promise(r=>setImmediate(r));
  assert.ok(players[0]?.onended,'新版回答应真实进入播放器');
  assert.equal(run('companionMemory.conversation'),'topic-test');
  const queryCall=calls.slice(beforeVoice).find(c=>c.path==='/api/voice-memory/ask');
  assert.equal(queryCall.headers.Authorization,'Bearer voice-token');assert.equal(queryCall.data.memory_version,undefined);
  assert.equal(queryCall.data.profile_id,undefined);assert.equal(queryCall.data.query,undefined);assert.equal(queryCall.data.receipt_id,'server-voice-receipt');
  await run('stopInteraction()');
  for(let i=0;i<60&&!voiceDone;i++)await new Promise(r=>setImmediate(r));
  assert.ok(voiceDone,'暂停播放后旧语音任务必须结束');await speaking;
  assert.equal(run('companionMemory.conversation'),'topic-test','已回答的话题在停止朗读后保留');
  ids.autoplay.checked=false;await run("send('他送给谁了？')");
  assert.equal(calls.filter(c=>c.path==='/api/memories/ask').at(-1).data.conversation_id,'topic-test');
  assert.equal(JSON.stringify(saved),historyBeforeVoice,'团队语音不进入个人聊天记录');
  assert.equal(micCalls,0,'自动测试不打开真实麦克风');
  assert.ok(!calls.slice(beforeVoice).some(c=>/preview|confirm|\/voice\//.test(c.path)),'只读语音不能走写入或旧版语音接口');
  run("sharedState.token='demo-token';sharedState.account={id:'demo',name:'其他账号'};sharedState.spaces=[{id:'demo-team',name:'虚构测试空间',role:'reader',version:1}];sharedState.space=null;busy=false;recording=false;enrolling=false;speaking=false;sharedState.busy=false;sharedState.asking=false;sharedControls()");
  assert.equal(ids.workspaceProfile.textContent,'其他账号，欢迎回来','登录后的欢迎语使用账号，不展示声纹档案');
  assert.ok(!ids.workspaceProfile.textContent.includes('档案'));
  ids.workspaceProfile.onclick();assert.equal(run('workspacePage'),'shared','点击欢迎语管理账号与空间');
  run("showWorkspacePage('chat')");
  console.log('Structured memory frontend passed: authenticated personal scope, v2 chat routing, preview before write, logout isolation, focus review with exact evidence/version guards, permission checks, one-click status-only review, automatic next, deferred correction, read-only v2 speech, interruption continuity and no real microphone.');
})().catch(error=>{console.error(error);process.exitCode=1;});
