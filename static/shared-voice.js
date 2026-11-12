// Shared speech stays in memory and never enters personal conversation history.
const sharedVoiceState={session:null,token:null,profile:null,epoch:0,starting:false,pending:false,cancelPromise:Promise.resolve()};
function sharedVoiceActive(){return !!sharedVoiceState.session;}
function sharedVoiceControls(){
  const active=sharedVoiceActive();
  $('sharedVoiceStart').disabled=sharedVoiceState.starting||active||!sharedState.token||!sharedState.space||busy||recording||enrolling||sharedState.busy||sharedState.asking||!asrReady||!wakeReady||!speakerReady||!ttsReady||!activeProfile;
  $('sharedVoicePrivate').disabled=active||sharedVoiceState.starting;
  $('sharedVoiceStop').disabled=!active&&!sharedVoiceState.starting;
  $('sharedVoiceBanner').hidden=!active&&!sharedVoiceState.starting;
  $('workspaceSharedVoice').hidden=active||sharedVoiceState.starting;
  $('autoplay').disabled=active||!ttsReady;
  if(active){$('speakerAuto').disabled=true;$('capture').disabled=true;}
}
async function sharedVoiceRequest(path,data={},signal=null,identity=sharedVoiceState.session,token=sharedVoiceState.token,traceId=null){
  const response=await fetch('/api/shared/voice/'+path,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token,...conversationDiagnostics.headers(traceId)},body:JSON.stringify({session:identity,...data}),signal});
  const result=await response.json();
  if(!response.ok){const error=new Error(result.error||'共同记忆语音操作失败。');error.status=response.status;throw error;}
  return result;
}
function sharedVoiceCancelPending(){
  if(!sharedVoiceActive())return;
  sharedVoiceState.epoch++;sharedVoiceState.pending=false;
  const session=sharedVoiceState.session;
  sharedVoiceState.cancelPromise=sharedVoiceRequest('cancel').catch(error=>{
    if(sharedVoiceState.session===session)sharedVoiceStop('共同记忆连接失效，请重新开启。');
  });
}
function sharedVoiceStop(message='已退出共同记忆语音。'){
  const session=sharedVoiceState.session,token=sharedVoiceState.token;
  sharedVoiceState.session=null;sharedVoiceState.token=null;sharedVoiceState.profile=null;
  sharedVoiceState.pending=false;sharedVoiceState.starting=false;sharedVoiceState.epoch++;
  if(session){
    wakeEnabled=false;pauseWake();cancelTurn({preserveSharedVoice:true});
    recording=false;clearInterval(tick);clearTimeout(timer);
    const mic=manual;manual=null;frames=[];mic?.stop().catch(()=>{});
    $('mic').querySelector('span').textContent='点击说话';$('micHint').textContent='点击开始，再次点击结束 · 最长 30 秒';
    sharedVoiceRequest('stop',{},null,session,token).catch(()=>{});render();controls();
  }
  $('sharedVoiceStatus').textContent=message;sharedVoiceControls();
}
async function sharedVoiceSpeak(text,review=null,followUp=false,traceId=null){
  traceId=traceId&&conversationDiagnostics.isLive(traceId)?traceId:conversationDiagnostics.start('shared');
  const epoch=sharedVoiceState.epoch,version=turnVersion,session=sharedVoiceState.session;
  const current=()=>sharedVoiceState.session===session&&sharedVoiceState.epoch===epoch&&turnVersion===version;
  await speak(text,{traceId,
    onComplete:async()=>{
      if(!current())return;
      try{
        if(review)await sharedVoiceRequest('played',{review_id:review});
        if(!current())return;
        if(review||followUp){conversationDiagnostics.stage(traceId,'confirmation');sharedVoiceState.pending=true;await pauseWake();await resumeWake('command');state('请完整说“我确认保存这条共同记忆”或“我确认修改这条共同记忆”，也可以取消','listening');}
        else{conversationDiagnostics.finish(traceId);await returnToWake();}
      }catch(error){if(current()){conversationDiagnostics.finish(traceId,'error','shared');sharedVoiceCancelPending();notice(error.message);await returnToWake();}}
    },
    onFailure:()=>{if(current()){sharedVoiceCancelPending();notice('播报未完成，未开启口头确认。请重新提出保存或修改。');}}
  });
}
async function sharedVoiceReceive(result,source,traceId=null){
  if(!sharedVoiceActive())return;
  const incomingSession=sharedVoiceState.session;
  await sharedVoiceState.cancelPromise;
  if(!sharedVoiceActive()||sharedVoiceState.session!==incomingSession)return;
  if(!result.text){conversationDiagnostics.finish(traceId,'empty','empty');sharedVoiceCancelPending();notice('没有听清，待确认操作已取消，请重新提出。');await returnToWake();return;}
  cancelTurn({preserveSharedVoice:true,preserveTrace:true});
  traceId=traceId&&conversationDiagnostics.isLive(traceId)?traceId:conversationDiagnostics.start(source);conversationDiagnostics.stage(traceId,'shared');
  const epoch=sharedVoiceState.epoch,version=turnVersion,session=sharedVoiceState.session;
  const current=()=>sharedVoiceState.session===session&&sharedVoiceState.epoch===epoch&&turnVersion===version;
  const id=newTurnId(),controller=new AbortController();turnId=id;chatAbort=controller;busy=true;
  controls();sharedControls();await pauseWake();if(!current())return;
  addMessage('user',result.text);const row=addMessage('assistant','正在处理共同记忆…');activeRow=row;
  state('正在处理共同记忆…','thinking');
  let reply=null;
  try{
    await monitorInterrupt();if(!current())return;
    reply=await sharedVoiceRequest('utterance',{utterance_id:result.utterance_id,turn_id:id},controller.signal,session,sharedVoiceState.token,traceId);
    if(!current())return;
    row.bubble.textContent=reply.reply;activeRow=null;
    sharedVoiceState.pending=!!reply.needs_confirmation||!!reply.follow_up;
    $('sharedVoiceStatus').textContent=reply.needs_confirmation?'待确认：请听完归属和可见范围，再明确说确认或取消。':reply.saved?'已完成共同记忆写入。':'共同记忆语音已开启。';
    if(reply.saved)sharedLoadCurrent().catch(()=>{});
    scroll();
  }catch(error){
    if(current()&&error.name!=='AbortError'){
      conversationDiagnostics.finish(traceId,'error','shared');
      sharedVoiceState.pending=false;
      if(error.status===401||error.status===403){
        sharedLock('共同记忆语音已锁定，请重新登录并开启。');
        notice(error.message);
        await speak('未能确认当前账号或说话人，已锁定共同记忆语音。请在后台重新登录并开启。');
        return;
      }
      row.bubble.textContent=error.status?'本次操作未完成：'+error.message:'未收到执行结果，请先在后台核对记忆是否已保存，再重试。';
      reply={reply:row.bubble.textContent};notice(error.message);
    }
  }finally{
    conversationDiagnostics.refresh(traceId);
    if(current()){busy=false;chatAbort=null;activeRow=null;controls();sharedControls();}
  }
  if(!current())return;
  if(reply?.stopped){sharedVoiceStop();await speak(reply.reply);return;}
  if(reply)await sharedVoiceSpeak(reply.reply,reply.review_id,reply.follow_up,traceId);
  else await returnToWake();
}
$('sharedVoiceStart').onclick=async()=>{
  if($('sharedVoiceStart').disabled)return;
  const epoch=sharedState.epoch,token=sharedState.token;
  sharedVoiceState.starting=true;sharedVoiceControls();
  try{
    const result=await sharedApi('/voice/start',{space_id:sharedState.space.id,profile_id:activeProfile,private_listening:$('sharedVoicePrivate').checked});
    if(epoch!==sharedState.epoch||token!==sharedState.token||!sharedVoiceState.starting)return;
    cancelTurn();await pauseWake();
    if(epoch!==sharedState.epoch||token!==sharedState.token||!sharedVoiceState.starting)return;
    sharedClearAnswer();
    sharedVoiceState.session=result.session;sharedVoiceState.token=token;sharedVoiceState.profile=activeProfile;sharedVoiceState.epoch++;
    $('speakerAuto').checked=true;wakeEnabled=true;
    $('messages').replaceChildren();$('suggestions').hidden=true;addMessage('assistant',result.reply);
    $('sharedVoiceStatus').textContent='共同记忆语音：'+sharedState.space.name+' · '+($('sharedVoicePrivate').checked?'私密收听':'公开播放')+' · 30 分钟后需重新开启';
    showWorkspacePage('chat');
    $('sharedVoiceBanner').scrollIntoView?.({behavior:'smooth',block:'center'});
    await sharedVoiceSpeak(result.reply);
  }catch(error){notice(error.message);$('sharedVoiceStatus').textContent=error.message;}
  finally{sharedVoiceState.starting=false;controls();sharedControls();sharedVoiceControls();}
};
$('sharedVoiceStop').onclick=async()=>{sharedVoiceStop();sharedControls();await returnToWake();};
sharedVoiceControls();
