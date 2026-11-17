// Shared answers stay in this page. Personal history and profile IDs never enter these requests.
const companionMemory={mode:'auto',binding:null,conversation:null,token:null,asking:false};
function companionMemoryV2(){return document.getElementById('chatMemoryVersion')?.value==='2';}
function companionMemoryManual(){return companionMemory.mode==='shared';}
function companionMemoryActive(){
  if(!sharedState.token||(typeof sharedVoiceActive==='function'&&sharedVoiceActive()))return false;
  if(companionMemoryManual())return !!sharedState.space;
  if(companionMemoryV2()&&['auto','personal'].includes(companionMemory.mode))return true;
  return companionMemory.mode==='auto'&&sharedState.spaces.length>0;
}
function companionMemoryPending(){return companionMemory.asking;}
// Read-only speech uses the authenticated account, never a guessed voice identity.
function companionMemoryVoiceActive(){return companionMemoryActive()&&companionMemoryV2();}
function companionMemoryKeepVoiceTopic(){return companionMemoryVoiceActive()&&!companionMemory.asking;}
async function companionMemoryRead(text,current,trace){
  if(!current()||!ttsReady)return;
  const version=turnVersion,speech=openReplySpeech(version,trace);
  await speech.end(text);
  if(current()&&version===turnVersion){await returnToWake();controls();}
}

function companionMemoryResetTopic(){
  const identity=companionMemory.conversation,token=companionMemory.token;
  companionMemory.conversation=null;
  const automatic=companionMemory.mode==='auto';
  if(identity&&token)fetch(companionMemoryV2()?'/api/memory/v2/reset':automatic?'/api/companion/reset':'/api/shared/ask/reset',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({conversation_id:identity,profile_id:activeProfile||null})}).catch(()=>{});
}
function companionMemoryCancel(preserveTopic=false){
  companionMemory.asking=false;
  if(!preserveTopic)companionMemoryResetTopic();
}
function companionMemoryRender(){
  $('messages').replaceChildren();$('suggestions').hidden=true;
  const space=sharedState.token&&sharedState.space;
  if(!sharedState.token){addMessage('assistant','可以直接和我聊天。登录空间账号后，还可以查询你有权查看的记忆。');return;}
  if(companionMemoryV2()&&companionMemory.mode==='personal'){addMessage('assistant','正在查询登录账号自己的新版个人记忆；团队资料不参与本轮回答。');return;}
  if(companionMemoryV2()&&companionMemory.mode==='auto'&&!sharedState.spaces.length){addMessage('assistant','当前查询你的账号个人记忆。加入或创建团队空间后，也会自动查询其中有权查看的记忆。');return;}
  if(companionMemory.mode==='auto'){addMessage('assistant',companionMemoryV2()?'已启用新版记忆：自动查询登录账号自己的个人记忆和有权查看的团队记忆。旧资料不会自动转换。直接输入问题即可，统计会说明依据和口径。':'已开启自动记忆查询：会同时查当前个人档案和已登录账号有权查看的团队记忆。直接输入问题即可，统计活动时会说明依据和口径。');return;}
  addMessage('assistant',space?'已选择「'+space.name+'」。直接输入问题，我会查询你有权查看的共同记忆，并展示回答依据。':'请选择共同记忆空间；尚未登录时，先点击“登录 / 管理空间”。');
}
function companionMemoryControls(){
  const active=companionMemoryActive(),voice=typeof sharedVoiceActive==='function'&&sharedVoiceActive();
  const locked=busy||recording||enrolling||wakeStarting||wakePhase==='command'||sharedState.busy||sharedState.asking||voice;
  $('chatMemoryVersion').disabled=locked;
  $('chatMemoryMode').value=companionMemory.mode;$('chatMemoryMode').disabled=locked;
  $('chatMemoryManage').textContent=sharedState.token?'管理账号与空间':'登录记忆账号';
  const automatic=companionMemory.mode==='auto';
  const showSpaces=companionMemory.mode==='shared'&&!!sharedState.token&&sharedState.spaces.length>0;
  $('chatMemorySpace').hidden=!showSpaces;$('chatMemorySpaceLabel').hidden=!showSpaces;$('chatMemoryManage').hidden=companionMemory.mode==='personal'&&!companionMemoryV2();
  $('chatMemorySpace').disabled=locked||!sharedState.token;$('chatMemoryManage').disabled=busy||recording||enrolling;
  if(voice){$('chatMemoryHint').textContent='共同记忆语音已开启；文字输入不能代替口头确认。';return;}
  if(!active){
    $('chatMemoryHint').textContent=!sharedState.token?'可以直接聊天，空间登录可选；登录后可使用有权限的记忆。':!sharedState.spaces.length?'当前账号暂无团队空间，可以直接普通聊天；个人记忆可在范围中选择。':'普通对话只查询当前个人档案。';
    if(companionMemory.mode==='chat')$('chatMemoryHint').textContent='普通聊天 · 可使用当前本机档案，账号下的个人与团队记忆不参与本轮。';
    $('send').disabled=locked||!ready;$('autoplay').disabled=!ttsReady;
    $('input').placeholder='输入想聊的内容…';$('input').maxLength=6000;return;
  }
  $('chatMemoryManage').textContent=sharedState.token?'管理账号与空间':'登录记忆账号';
  const space=sharedState.token&&sharedState.space;
  $('chatMemoryHint').textContent=space?'空间账号：'+sharedState.account.name+' · '+space.name+' · 仅文字查询':'先登录空间账号，再选择空间。个人档案不会授予团队访问权限。';
  if(automatic)$('chatMemoryHint').textContent='自动查询 · '+(activeProfile?'当前个人档案 + ':'')+'空间账号：'+(sharedState.account?.name||'未登录')+' · '+sharedState.spaces.length+'个可访问空间 · 文字查询';
  if(companionMemoryV2())$('chatMemoryHint').textContent='新版记忆 · 登录账号：'+sharedState.account?.name+' · '+(automatic?'自动查询自己的个人记忆和有权查看的团队记忆':companionMemory.mode==='personal'?'只查询自己的新版个人记忆':'查询指定团队')+'。支持语音只读查询，保存或修改仍需单独确认。';
  if(companionMemoryV2()&&automatic&&!sharedState.spaces.length)$('chatMemoryHint').textContent='记忆账号：'+sharedState.account.name+' · 只查询你自己的个人记忆（暂无团队空间）。';
  $('workspaceSharedVoice').hidden=true;$('autoplay').disabled=companionMemoryV2()?!ttsReady:true;
  if(!companionMemoryV2()){$('mic').disabled=true;$('wake').disabled=true;}
  $('capture').disabled=true;$('speakerRetry').disabled=true;$('speakerAuto').disabled=true;
  $('send').disabled=locked||!ready||!sharedState.token||(companionMemory.mode==='shared'&&!space);
  if(companionMemoryV2()&&!sharedState.token)$('chatMemoryHint').textContent='请先登录空间账号后查询新版记忆；个人档案选择不能代替登录。';
  $('input').disabled=recording||enrolling;
  $('input').placeholder=automatic?'直接提问，自动查询个人与团队记忆…':'输入共同记忆问题…';$('input').maxLength=automatic?1200:2000;
}
function companionMemorySync(){
  const space=sharedState.space,token=sharedState.token;
  const automatic=(companionMemory.mode==='auto'||(companionMemoryV2()&&companionMemory.mode==='personal'))&&!(typeof sharedVoiceActive==='function'&&sharedVoiceActive());
  const binding=automatic?[companionMemoryV2()?'v2':'v1',token||'',activeProfile,sharedState.epoch,...sharedState.spaces.map(x=>x.id+':'+x.version).sort()].join('|'):(token&&space?(companionMemoryV2()?'v2|':'v1|')+token+'|'+space.id+'|'+space.version:null);
  if(companionMemory.binding!==binding){
    const refresh=companionMemoryActive()||automatic||!!companionMemory.token;
    if(refresh)cancelTurn();else companionMemoryResetTopic();
    companionMemory.binding=binding;companionMemory.token=token;
    if(refresh){render();notice();controls();}
  }
  const select=$('chatMemorySpace');select.replaceChildren();
  const blank=document.createElement('option');blank.value='';blank.textContent=token?'请选择空间':'请先登录空间账号';select.append(blank);
  for(const item of sharedState.spaces){const option=document.createElement('option');option.value=item.id;option.textContent=item.name;select.append(option);}
  select.value=space?.id||'';companionMemoryControls();
}
async function companionMemorySetMode(mode){
  if(!['auto','personal','shared','chat'].includes(mode)||busy||recording||enrolling||wakeStarting||wakePhase==='command'||sharedState.busy||sharedState.asking||(typeof sharedVoiceActive==='function'&&sharedVoiceActive())){companionMemoryControls();return;}
  cancelTurn();wakeEnabled=false;await pauseWake();
  companionMemory.mode=mode;companionMemory.binding=null;
  $('input').value='';$('input').placeholder='输入想聊的内容…';$('input').maxLength=6000;
  companionMemorySync();render();controls();sharedControls();idle();notice();
}
function companionMemorySources(row,result,current,spaceId){
  const sources=Array.isArray(result.sources)?result.sources:[],ids=result.answer_source_ids;
  if(!sources.length)return;
  const known=new Set(sources.map(x=>x.id));
  const confirmed=['model_reported','linked_record','program_selected'].includes(result.source_usage)&&Array.isArray(ids)&&new Set(ids).size===ids.length&&ids.every(id=>known.has(id));
  const cited=new Set(confirmed?ids:[]),details=document.createElement('details'),summary=document.createElement('summary'),list=document.createElement('ul');
  details.className='chat-memory-sources';summary.textContent=confirmed?'回答依据 · 实际引用 '+cited.size+' 条':'候选资料 · 引用关系未确认';
  details.append(summary,list);
  for(const item of sources){
    const card=document.createElement('li'),label=document.createElement('strong'),body=document.createElement('p'),meta=document.createElement('p');
    label.textContent=(cited.has(item.id)?'实际引用':'其他候选')+' · '+(item.scope_name||'当前空间')+' · '+(sharedStatusNames[item.status]||'状态未记录');
    body.textContent=item.content;meta.className='memory-meta';meta.textContent='事件日期：'+(item.event_date||'未填写')+'\n来源：'+(item.source_note||'未填写');
    card.append(label,body,meta);
    if(item.has_evidence){
      const button=document.createElement('button'),quotes=document.createElement('p');button.className='text-button';button.textContent='查看原文摘录';
      button.onclick=async()=>{
        if(!current())return;button.disabled=true;
        try{
          const evidence=item.memory_version===2?await companionMemoryV2Evidence(item):await sharedApi('/memories/evidence',{space_id:item.space_id||spaceId,id:item.record_id||item.id,expected_version:item.version});
          if(!current())return;
          quotes.textContent=evidence.available?(evidence.content_changed?'当前正文有修改，请重新核对摘录。\n':'')+(evidence.quotes||[]).map(q=>q.location+'\n'+q.text).join('\n\n'):evidence.message;
        }catch(error){if(current())quotes.textContent=error.message;}
        finally{if(current())button.disabled=false;}
      };
      card.append(button,quotes);
    }
    list.append(card);
  }
  row.item.append(details);
}
async function companionMemorySend(text,{choiceId=null,label=null,spaceChoice=null,source='text',traceId=null}={}){
  const query=text.trim(),space=sharedState.space,token=sharedState.token;
  if(!companionMemoryActive()||!query||busy||recording||enrolling||sharedState.busy||sharedState.asking)return;
  const automatic=companionMemory.mode==='auto';
  if(!token||(!automatic&&!(companionMemoryV2()&&companionMemory.mode==='personal')&&!space)){notice('请先登录空间账号并选择空间。');return;}
  const maximum=automatic||companionMemoryV2()?1200:2000;
  if(query.length>maximum){notice('请将记忆问题缩短到'+maximum+'字以内。');return;}
  cancelTurn({preserveCompanionTopic:true,preserveTrace:!!(traceId&&conversationDiagnostics.isLive(traceId))});
  const version=turnVersion,epoch=sharedState.epoch,binding=companionMemory.binding,id=newTurnId(),controller=new AbortController();
  const current=()=>companionMemoryActive()&&version===turnVersion&&epoch===sharedState.epoch&&token===sharedState.token&&binding===companionMemory.binding;
  const trace=traceId&&conversationDiagnostics.isLive(traceId)?traceId:conversationDiagnostics.start(source==='text'?'shared':source);
  conversationDiagnostics.stage(trace,'shared');
  turnId=id;chatAbort=controller;busy=true;companionMemory.asking=true;controls();sharedControls();
  $('input').value='';notice();addMessage('user',label||query);
  const row=addMessage('assistant',automatic?'正在自动查询个人与团队记忆…':'正在查询'+(companionMemoryV2()&&companionMemory.mode==='personal'?'个人记忆':'「'+space.name+'」')+'…');activeRow=row;state('正在查询共同记忆…','thinking');
  const started=performance.now();let voiceText=null;
  try{
    const result=companionMemoryV2()?await companionMemoryAutoApi({query,memory_version:2,conversation_id:companionMemory.conversation,choice_id:choiceId,...(companionMemory.mode==='personal'?{scope_kind:'personal'}:!automatic?{scope_kind:'team',space_id:space.id}:{}),space_choice:spaceChoice,turn_id:id},controller.signal):automatic?await companionMemoryAutoApi({query,profile_id:activeProfile||null,conversation_id:companionMemory.conversation,space_choice:spaceChoice,turn_id:id},controller.signal):await sharedApi('/agent/ask',{space_id:space.id,query,conversation_mode:true,conversation_id:companionMemory.conversation,choice_id:choiceId,turn_id:id,include_outdated:false},controller.signal);
    if(!current())return;
    companionMemory.conversation=result.conversation?.id||null;companionMemory.token=token;
    row.bubble.textContent=result.answer;
    if(!automatic&&typeof diagnosticOverview!=='undefined')diagnosticOverview.noteShared(result,query);
    // Evidence in an earlier displayed answer remains readable in the same
    // authenticated scope; a new text turn must not disable its source button.
    const readable=()=>companionMemoryActive()&&epoch===sharedState.epoch&&token===sharedState.token&&binding===companionMemory.binding;
    companionMemorySources(row,result,readable,space?.id);
    const choices=result.space_choices||result.conversation?.choices||[];
    if(choices.length){
      const group=document.createElement('div');group.className='chat-memory-choices';
      for(const choice of choices){const button=document.createElement('button');button.className='secondary-button';button.textContent=choice.label;button.onclick=()=>{if(current())companionMemorySend(query,{choiceId:result.space_choices?null:choice.id,spaceChoice:result.space_choices?choice.id:null,label:choice.label});};group.append(button);}
      row.item.append(group);
    }
    $('connection').textContent=(automatic?'自动记忆查询':'共同记忆 · '+(space?.name||'个人'))+' · 用时 '+((performance.now()-started)/1000).toFixed(1)+' 秒';
    if(companionMemoryV2()&&typeof result.answer==='string'&&!choices.length){
      const replay=document.createElement('button');replay.className='replay';replay.textContent='▷ 再听一遍';
      replay.onclick=async()=>{
        if(!readable()||!ttsReady)return;
        cancelTurn({preserveCompanionTopic:true});turnId=newTurnId();
        const replayVersion=turnVersion,trace=conversationDiagnostics.start('replay');
        await companionMemoryRead(result.answer,()=>readable()&&replayVersion===turnVersion,trace);
      };row.item.append(replay);
      if(ttsReady&&$('autoplay').checked)voiceText=result.answer;
    }
    activeRow=null;scroll();if(!voiceText)conversationDiagnostics.finish(trace);
  }catch(error){
    if(current()){
      companionMemoryResetTopic();
      row.bubble.textContent=error.name==='AbortError'?'已停止查询。':'本次共同记忆查询未完成：'+error.message;
      conversationDiagnostics.finish(trace,'error','shared');
    }
  }finally{
    if(current()){busy=false;companionMemory.asking=false;chatAbort=null;activeRow=null;controls();sharedControls();idle();}
  }
  if(voiceText&&current())await companionMemoryRead(voiceText,current,trace);
  else if(current())await returnToWake();
}
async function companionMemoryAutoApi(data,signal){
  const token=sharedState.token,epoch=sharedState.epoch;
  const response=await fetch('/api/companion/ask',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify(data),signal});
  const result=await response.json();
  if(!response.ok){
    if(token===sharedState.token&&epoch===sharedState.epoch&&response.status===401)sharedLock('团队登录已失效，请重新登录。');
    if(token===sharedState.token&&epoch===sharedState.epoch&&[403,409].includes(response.status)){
      // Clear all cross-scope material immediately, then reload current memberships.
      cancelTurn();render();sharedRun(sharedRefresh);
    }
    const error=new Error(result.error||'自动查询失败，请重试。');error.status=response.status;throw error;
  }
  return result;
}
async function companionMemoryV2Evidence(item){
  const response=await fetch('/api/memory/v2/evidence',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+sharedState.token},body:JSON.stringify({scope_kind:item.scope_kind,space_id:item.space_id,id:item.record_id||item.id,expected_version:item.version})});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'摘录读取失败。');return result;
}
$('chatMemoryVersion').onchange=()=>{if(busy||recording||enrolling)return;companionMemory.binding=null;cancelTurn();companionMemorySync();render();controls();};
$('chatMemoryMode').onchange=()=>companionMemorySetMode($('chatMemoryMode').value);
$('chatMemoryManage').onclick=()=>workspaceOpenAccount('chat');
$('chatMemorySpace').onchange=async()=>{
  if(busy||recording||enrolling||sharedState.busy||sharedState.asking)return;
  const id=$('chatMemorySpace').value;
  sharedClearSpace();sharedState.space=sharedState.spaces.find(item=>item.id===id)||null;
  sharedRenderSpaces();await sharedRun(sharedLoadCurrent);companionMemorySync();
};
companionMemorySync();
