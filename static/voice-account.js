// Password login grants access; server voice receipts only select the bound identity.
const voiceMemoryUI=(()=>{
  const state={token:null,epoch:0,request:0,busy:false,data:null,plan:null};
  const message=text=>$('voiceMemoryStatus').textContent=text||'';
  function clearPlan(){state.plan=null;$('voiceMemoryReview').hidden=true;$('voiceMemoryConsent').checked=false;$('voiceMemorySummary').textContent='';}
  function locked(){return state.busy||busy||recording||enrolling||sharedState.busy||sharedState.asking;}
  function paint(){
    const logged=!!sharedState.token,lock=locked();
    $('voiceMemoryPreview').disabled=!logged||lock||!$('voiceMemoryProfile').value;
    $('voiceMemoryProfile').disabled=!logged||lock;$('voiceMemoryRefresh').disabled=!logged||lock;
    $('voiceMemoryConfirm').disabled=!state.plan||lock||!$('voiceMemoryConsent').checked;
    $('voiceMemoryCancel').disabled=lock;
    $('voiceMemoryStart').disabled=!state.data?.authorized||lock;
    $('voiceMemoryRevoke').disabled=!state.data?.authorized||lock;
    $('voiceMemoryUnbind').disabled=!state.data?.binding||lock;
    $('voiceMemorySetup').textContent=state.data?.authorized?'声纹查询已授权 · 设置':'声纹查询设置';
  }
  async function call(action,data={}){
    const token=sharedState.token,epoch=state.epoch;
    const response=await fetch('/api/voice-memory/'+action,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify(data)});
    const result=await response.json();
    if(epoch!==state.epoch||token!==sharedState.token)return null;
    if(!response.ok){
      if(response.status===401)sharedLock('本人登录已失效，请重新登录后授权声纹查询。');
      throw new Error(result.error||'声纹授权操作未完成。');
    }
    return result;
  }
  function apply(data){
    state.data=data;
    const previous=$('voiceMemoryProfile').value;$('voiceMemoryProfile').replaceChildren();
    const blank=document.createElement('option');blank.value='';blank.textContent='请选择已登记声纹的本人档案';$('voiceMemoryProfile').append(blank);
    for(const p of data.profiles||[]){const option=document.createElement('option');option.value=p.id;option.textContent=p.name+' · 已录声纹';$('voiceMemoryProfile').append(option);}
    const ids=new Set((data.profiles||[]).map(p=>p.id));
    $('voiceMemoryProfile').value=data.binding?.profile_id||(ids.has(previous)?previous:'');
    const teams=(data.spaces||[]).map(s=>s.name);
    message(data.message+(data.binding?' 当前绑定：'+data.binding.profile_name+' → '+data.account.name+'。':'')+
      (teams.length?' 当前获准团队：'+teams.join('、')+'。':' 当前没有团队权限，可以先使用个人记忆。'));
    paint();
  }
  async function refresh(){
    if(!sharedState.token||state.busy)return;
    const serial=++state.request;
    try{const result=await call('status');if(result&&serial===state.request)apply(result);}
    catch(error){if(serial===state.request)message(error.message);}
  }
  function sync(){
    if(state.token!==sharedState.token){
      state.token=sharedState.token;state.epoch++;state.request++;state.busy=false;state.data=null;clearPlan();
      $('voiceMemoryProfile').replaceChildren();message('');
      $('workspaceSpeakerResult').hidden=true;$('workspaceSpeakerResult').textContent='';
      if(state.token)void refresh();
    }
    paint();
  }
  async function work(fn){
    if(locked()||!sharedState.token)return;
    const epoch=state.epoch;state.request++;state.busy=true;paint();
    try{await fn();}
    catch(error){if(epoch===state.epoch){clearPlan();message(error.message);}}
    finally{if(epoch===state.epoch){state.busy=false;paint();}}
  }
  $('voiceMemoryPreview').onclick=()=>work(async()=>{
    clearPlan();const result=await call('preview',{profile_id:$('voiceMemoryProfile').value});
    if(!result)return;state.plan=result.preview_token;
    $('voiceMemorySummary').textContent='声纹档案：'+result.profile_name+' → 当前记忆账号：'+result.account_name+'。'+
      '访问范围：本人个人记忆'+(result.spaces.length?'，以及'+result.spaces.join('、'):'（当前没有团队权限）')+'。'+result.effect;
    $('voiceMemoryReview').hidden=false;message('请核对是否为本人账号和本人声纹，再确认授权。');
  });
  $('voiceMemoryConfirm').onclick=()=>work(async()=>{
    if(!state.plan||!$('voiceMemoryConsent').checked)return;
    const result=await call('confirm',{preview_token:state.plan,confirmed:true,consent:true});
    if(result){clearPlan();apply(result);}
  });
  $('voiceMemoryConsent').onchange=paint;
  $('voiceMemoryProfile').onchange=()=>{clearPlan();paint();};
  $('voiceMemoryCancel').onclick=()=>{clearPlan();paint();};
  $('voiceMemoryRefresh').onclick=refresh;
  $('voiceMemoryRevoke').onclick=()=>work(async()=>{
    const result=await call('revoke');
    if(result){clearPlan();apply(result);cancelTurn();render();controls();}
  });
  $('voiceMemoryUnbind').onclick=()=>work(async()=>{
    if(!window.confirm('解除本人声纹绑定，并停止这个账号全部会话的声纹记忆授权？不会删除原有声纹或记忆。'))return;
    const result=await call('unbind');
    if(result){clearPlan();apply(result);cancelTurn();render();controls();}
  });
  $('voiceMemoryStart').onclick=async()=>{
    if(!state.data?.authorized||locked())return;
    await companionMemorySetMode('auto');showWorkspacePage('chat');$('speakerAuto').checked=true;
    notice('声纹记忆查询已就绪。点击“说话”，或由你开启语音唤醒；匹配本人后自动查询个人和获准的团队记忆。');
    controls();
  };
  $('voiceMemorySetup').onclick=()=>{
    workspaceOpenAccount('chat');
    if(sharedState.token)$('voiceMemoryPanel').scrollIntoView({block:'start'});
    void refresh();
  };
  function accept(identity){
    if(!identity?.matched||identity.account_name!==sharedState.account?.name)return;
    $('workspaceSpeakerResult').textContent='本轮声音匹配：'+identity.profile_name+' · 已核对本人账号，按当前权限查询个人与团队记忆。';
    $('workspaceSpeakerResult').hidden=false;
    $('speakerStatus').textContent='本轮声音匹配：'+identity.profile_name+'；账号授权已核对。';
  }
  function denied(text){
    $('workspaceSpeakerResult').textContent=text;$('workspaceSpeakerResult').hidden=false;
    $('speakerStatus').textContent='本轮未读取记忆。';message(text);void refresh();
  }
  return {sync,refresh,accept,denied};
})();
voiceMemoryUI.sync();
