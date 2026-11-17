// Explicit memory edits and opt-in voiceprint enrollment. Audio stays in RAM.
const personalStatusNames={pending:'待核实',valid:'有效（本人标记）',outdated:'过时'};
function personalStateText(row){return '状态：'+(personalStatusNames[row.status]||'未记录核实状态')+'；事件日期：'+(row.event_date||'未填写')+(row.status_reason?'；说明：'+row.status_reason:'');}
let profiles=[], memoryEdit=null, memoryEditRevision=null, personalVersion=0;
let voiceMatchedProfile='';
let memoryItems=[], memoryLoaded=false, memoryBusy=false;
const greetedProfiles=new Set();
function voiceGreeting(source){
  if(!['manual','wake'].includes(source)||!activeProfile||voiceMatchedProfile!==activeProfile||greetedProfiles.has(activeProfile))return null;
  const profile=profiles.find(p=>p.id===activeProfile);
  return profile?{profile:profile.id,text:`${profile.salutation||profile.name}，你好。`}:null;
}
function markVoiceGreeting(greeting){greetedProfiles.add(greeting.profile);}
function resetVoiceGreetings(){greetedProfiles.clear();}
function fillSalutation(){
  $('profileSalutation').value=profiles.find(p=>p.id===activeProfile)?.salutation||'';
}
const speakerDiagnosticSteps=[];
function speakerDiagnostic(title, details={}){
  const lines=Object.entries(details).map(([key,value])=>`${key}：${value}`);
  speakerDiagnosticSteps.unshift(`[${new Date().toLocaleTimeString()}] ${title}\n${lines.join('\n')}`);
  speakerDiagnosticSteps.length=Math.min(speakerDiagnosticSteps.length,10);
  $('speakerDiagnostics').textContent=speakerDiagnosticSteps.join('\n\n');
}
function transcriptionDiagnostic(result,source){
  const d=result.diagnostics||result.speaker?.diagnostics||{};
  const labels={matched:'声纹匹配成功',insufficient_speech:'有效语音不足',low_score:'相似度未达到阈值',ambiguous:'两个候选太接近',
    no_compatible_voiceprint:'没有兼容的声纹',model_unavailable:'声纹模型不可用',invalid_format:'音频格式不符合要求',
    invalid_audio:'音频或声纹特征无效',empty_audio:'没有收到音频',clipped_audio:'声音失真',too_long:'录音太长',recognition_disabled:'未开启声纹识别',no_speech_timeout:'等待超时，没有检测到讲话'};
  const details={'输入方式':source==='wake'?'语音唤醒 / 插话':'点击录音','结果':labels[d.code]||result.speaker?.reason||'未返回诊断信息',
    '文字识别':result.text?'已识别到文字':'未识别到文字'};
  if(d.trace_id)details['日志编号']=d.trace_id;
  for(const [key,label] of [['audio_seconds','录音总时长（秒）'],['speech_seconds','有效语音（秒）'],['min_speech_seconds','最低有效语音（秒）'],
    ['sample_rate','采样率'],['channels','声道数'],['rms_dbfs','平均电平（dBFS）'],['peak','峰值（0～1）'],
    ['registered_count','已登记声纹数'],['compatible_count','参与比对的声纹数'],['top_score','最高相似度'],['threshold','匹配阈值'],
    ['runner_up_score','第二名相似度'],['score_gap','两名差值'],['margin_threshold','差值要求'],['elapsed_ms','声纹处理耗时（毫秒）']]){
    if(d[key]!==undefined&&d[key]!==null)details[label]=d[key];
  }
  if(d.code==='insufficient_speech')details['建议']='连续自然说 5～8 秒；若录音很长但有效语音很短，检查麦克风、距离和环境噪声。';
  if(d.code==='low_score')details['建议']='确认本人讲话，并使用登记时的麦克风与距离；先不要降低匹配阈值。';
  speakerDiagnostic('语音处理结果',details);
}
let enrollmentStarting=false;
function enrollmentNotice(text){$('enrollmentStatus').textContent=text;notice(text);}
let enrollmentMic=null, enrollmentFrames=[], enrollmentSamples=[], enrollmentTimer=null, enrollmentVersion=0, enrollmentSaving=false;
const enrollmentPrompts=[
  '请读：今天我想和机器人聊聊天，也希望它能慢慢记住我的习惯。',
  '请读：周末我喜欢做一些自己感兴趣的事情，生活里还有很多值得发现的细节。',
  '请读：这是我的第三段声音，我会用自然的语速说话，让它认识我的声音。'
];

function personalControls(){
  const locked=busy||recording||enrolling||enrollmentSaving||wakePhase==='command';
  $('createProfile').disabled=locked;$('profileName').disabled=locked;
  for(const id of ['deleteProfile','deleteVoice','saveSalutation','profileSalutation'])$(id).disabled=!activeProfile||locked||memoryBusy;
  // 保存类按钮始终可点：条件不满足时给出具体原因，而不是让用户点了没有任何反应。
  $('saveMemory').disabled=memoryBusy;
  $('profileSelect').disabled=locked||memoryBusy;
  $('cancelMemoryEdit').disabled=locked||memoryBusy;
  $('memorySearch').disabled=!activeProfile||!memoryLoaded;
  $('exportMemories').disabled=!activeProfile||!memoryLoaded||locked||memoryBusy;
  $('memoryText').disabled=!activeProfile||locked||memoryBusy;
  $('recordVoice').disabled=!speakerReady||enrollmentSaving||enrollmentStarting;
  $('saveVoice').disabled=enrollmentSaving||enrollmentStarting||!!enrollmentMic;
  $('voiceConsent').disabled=enrollmentSaving||enrollmentStarting||!!enrollmentMic;
  $('cancelEnroll').disabled=enrollmentSaving;
  if(typeof personalMemoryControls==='function')personalMemoryControls();
  if(typeof memoryImportControls==='function')memoryImportControls();
  if(typeof memoryReviewControls==='function')memoryReviewControls();
  if(typeof personalStateControls==='function')personalStateControls();
}

async function refreshProfiles(){
  const result=await(await api('/api/profiles/list',{})).json();profiles=result.profiles;
  $('profileSelect').replaceChildren();
  const guest=document.createElement('option');guest.value='';guest.textContent='访客 · 不读取个人记忆';$('profileSelect').append(guest);
  for(const profile of profiles){const option=document.createElement('option');option.value=profile.id;option.textContent=profile.name+(profile.enrolled?' · 已录声纹':'');$('profileSelect').append(option);}
  if(activeProfile&&!profiles.some(p=>p.id===activeProfile))switchProfile('');
  $('profileSelect').value=activeProfile;fillSalutation();controls();
}

function switchProfile(identity,{preserveTrace=false}={}){
  voiceMatchedProfile='';
  $('workspaceSpeakerResult').textContent='';
  $('workspaceSpeakerResult').hidden=true;
  $('speakerRetry').hidden=true;
  if(identity===activeProfile)return;
  if(typeof diagnosticOverview!=='undefined')diagnosticOverview.clearLocal();
  if(typeof sharedPersonalChanged==='function')sharedPersonalChanged();
  cancelTurn({preserveTrace});if(typeof personalMemoryCancel==='function')personalMemoryCancel(true);activeProfile=identity;
  messages=identity?readChatHistory(identity):[];
  render();
  resetMemoryEditor();$('memorySearch').value='';$('memoryStatusFilter').value='all';$('personalIncludeOutdated').checked=false;
  if(typeof resetMemoryImport==='function')resetMemoryImport();
  if(typeof resetMemoryReview==='function'){resetMemoryReview();closeMemoryHistory();}
  $('profileSelect').value=identity;
  fillSalutation();
  enrollmentSamples=[];enrollmentFrames=[];$('voiceConsent').checked=false;updateEnrollment();
  refreshMemories();controls();
}

function applySpeaker(result){
  const identity=result.matched&&profiles.some(p=>p.id===result.id)?result.id:'';
  switchProfile(identity,{preserveTrace:true});
  voiceMatchedProfile=identity;
  if(!identity){
    const reason=result.diagnostics?.code==='low_score'?'声纹相似度未达标':(result.reason||'未确认说话人');
    $('workspaceSpeakerResult').textContent='本轮按访客处理：'+reason+'。';
    $('workspaceSpeakerResult').hidden=false;
  }else{
    // 匹配成功也要说出来，否则用户无法确认这一轮到底用了谁的档案。
    $('workspaceSpeakerResult').textContent=`本轮声音匹配：${result.name}，已按该档案读取个人记忆。若不是你本人，可在上方手动切换。`;
    $('workspaceSpeakerResult').hidden=false;
  }
  $('speakerStatus').textContent=identity?'本轮声音匹配：'+result.name+'（可在上方更正）':'本轮按访客处理：'+(result.reason||'未确认说话人');
  if(!identity&&result.diagnostics?.speech_seconds!==undefined){
    $('speakerStatus').textContent+=`（有效语音 ${result.diagnostics.speech_seconds} 秒）`;
  }
}

function speakerRetryNeeded(result,traceId){
  const code=result.speaker?.diagnostics?.code||result.diagnostics?.code;
  if(!$('speakerAuto').checked||!result.text||result.speaker?.matched!==false||
    !['low_score','insufficient_speech','ambiguous'].includes(code)||sharedVoiceActive())return false;
  const suggestions={
    insufficient_speech:'请自然说一句完整问题，持续约 5～8 秒。',
    ambiguous:'两个人的声音分数太接近，请由一人单独说话。',
    low_score:'请使用登记时的麦克风和距离。反复失败时，可重新录入本人声纹。',
  };
  const message='这轮还没认准说话人。请点“重新识别声音”再说一遍完整问题。'+suggestions[code]+
    '也可在个人记忆中关闭声纹自动识别，按访客对话。';
  $('workspaceSpeakerResult').textContent=message;
  $('workspaceSpeakerResult').hidden=false;
  $('speakerRetry').hidden=false;
  notice(message);
  conversationDiagnostics.finish(traceId,'done','speaker_retry');
  return true;
}
$('speakerRetry').onclick=()=>{
  if(!$('speakerRetry').disabled)startRecording();
};

function resetMemoryEditor(){
  memoryEdit=null;memoryEditRevision=null;$('memoryText').value='';$('memoryState').value='pending';$('memoryStateReason').value='';$('memoryEventDate').value='';$('saveMemory').textContent='保存记忆';
}
function memoryLocked(){return busy||recording||enrolling||enrollmentSaving||memoryBusy||wakePhase==='command';}
function memoryTime(value){
  if(!value)return '未知（历史记录）';
  const date=new Date(value.replace(' ','T')+'Z');
  return Number.isNaN(date.getTime())?'未知':date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
}
function renderMemories(){
  $('memoryList').replaceChildren();
  if(!activeProfile){$('memoryCount').textContent='请先选择用户';return;}
  if(!memoryLoaded){$('memoryCount').textContent='记忆尚未加载';return;}
  const identity=activeProfile,query=$('memorySearch').value.trim().toLocaleLowerCase();
  const filter=$('memoryStatusFilter').value||'all';
  const items=memoryItems.filter(item=>item.content.toLocaleLowerCase().includes(query)&&(filter==='all'||item.status===filter));
  $('memoryCount').textContent=query||filter!=='all'?`找到 ${items.length} / ${memoryItems.length} 条`:`已保存 ${memoryItems.length} / 200 条`;
  const sources={manual:'手动保存',conversation:'对话中的记住指令',legacy:'历史记录（来源未知）'};
  for(const memory of items){
    const row=document.createElement('li'),content=document.createElement('span'),meta=document.createElement('small');
    content.textContent=memory.content;meta.className='memory-meta';
    meta.textContent=personalStateText(memory)+'\n'+`来源：${sources[memory.source]||sources.legacy}\n首次保存：${memoryTime(memory.created)}\n最近更新：${memoryTime(memory.updated)}（北京时间）`;
    const edit=document.createElement('button');edit.className='text-button';edit.textContent='编辑';edit.onclick=()=>{
      if(memoryLocked()||identity!==activeProfile)return;
      if(typeof resetMemoryReview==='function')resetMemoryReview();
      memoryEdit=memory.id;memoryEditRevision=memory.revision;$('memoryText').value=memory.content;$('memoryState').value=memory.status||'pending';$('memoryStateReason').value=memory.status_reason||'';$('memoryEventDate').value=memory.event_date||'';$('saveMemory').textContent='更新记忆';$('memoryText').focus();
    };
    const remove=document.createElement('button');remove.className='text-button';remove.textContent='删除';remove.onclick=async()=>{
      if(memoryLocked()||identity!==activeProfile||!window.confirm('删除这条长期记忆？'))return;
      memoryBusy=true;personalControls();
      try{
        const result=await(await api('/api/memory/delete',{profile_id:identity,id:memory.id,expected_revision:memory.revision})).json();
        if(identity!==activeProfile)return;
        if(memoryEdit===memory.id)resetMemoryEditor();
        if(typeof closeMemoryHistory==='function'){closeMemoryHistory();resetMemoryReview();}
        await refreshMemories();notice(result.deleted?'记忆已删除。':'这条记忆已不存在，列表已更新。');
      }catch(error){if(identity===activeProfile)notice(error.message);}
      finally{memoryBusy=false;personalControls();}
    };
    const history=document.createElement('button');history.className='text-button';history.textContent='修改历史';
    history.onclick=()=>{if(!memoryLocked()&&identity===activeProfile)showMemoryHistory(memory.id);};
    row.append(content,meta,edit,remove,history);$('memoryList').append(row);
  }
  if(!items.length){const row=document.createElement('li');row.textContent=memoryItems.length?'没有匹配的记忆，试试其他关键词。':'还没有保存的记忆。可以在上方添加，或在聊天中说“记住：……”。';$('memoryList').append(row);}
}
async function refreshMemories(){
  if(typeof diagnosticOverview!=='undefined')diagnosticOverview.clearLocal();
  const version=++personalVersion,identity=activeProfile;
  if(typeof personalConflictClear==='function')personalConflictClear();
  if(typeof personalMemoryCancel==='function')personalMemoryCancel();
  memoryItems=[];memoryLoaded=false;renderMemories();personalControls();if(!identity)return;
  $('memoryCount').textContent='正在加载记忆…';
  try{
    const data=await(await api('/api/memory/list',{profile_id:identity})).json();
    if(version!==personalVersion||identity!==activeProfile)return;
    memoryItems=data.memories;memoryLoaded=true;
    if(typeof personalConflictRender==='function')personalConflictRender(data.conflicts);
    if(memoryEdit&&!memoryItems.some(item=>item.id===memoryEdit))resetMemoryEditor();
    renderMemories();personalControls();
  }catch(error){if(version===personalVersion){$('memoryCount').textContent='加载失败，请重新选择用户重试';notice(error.message);}}
}
async function exportMemories(){
  if(!activeProfile||!memoryLoaded||memoryLocked())return;
  const identity=activeProfile;memoryBusy=true;personalControls();
  let url=null,link=null;
  try{
    const data=await(await api('/api/memory/export',{profile_id:identity})).json();
    if(identity!==activeProfile)return;
    const blob=new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json;charset=utf-8'});
    url=URL.createObjectURL(blob);link=document.createElement('a');link.href=url;
    link.download=`yiban-memories-${identity}-${new Date().toISOString().replace(/[:.]/g,'-')}.json`;
    document.body.append(link);link.click();
    notice(`已发起下载：${data.memories.length} 条文字记忆，请在浏览器下载中查看。`);
  }catch(error){if(identity===activeProfile)notice(error.message);}
  finally{if(link)link.remove();if(url)setTimeout(()=>URL.revokeObjectURL(url),1000);memoryBusy=false;personalControls();}
}
$('memorySearch').oninput=renderMemories;
$('exportMemories').onclick=exportMemories;

function updateEnrollment(){
  $('recordVoice').textContent=enrollmentStarting?'正在打开麦克风…':enrollmentMic?'结束这一段':`录制第 ${Math.min(3,enrollmentSamples.length+1)} 段`;
  $('enrollPrompt').textContent=enrollmentSamples.length===3?'三段录音已准备好，点击「保存声纹」。':`${enrollmentSamples.length}/3 已录制。${enrollmentPrompts[enrollmentSamples.length]}`;
  personalControls();
}

async function cancelEnrollment(){
  if(enrollmentSaving)return;
  enrollmentVersion++;clearTimeout(enrollmentTimer);
  const mic=enrollmentMic;enrollmentMic=null;enrollmentFrames=[];enrollmentSamples=[];enrolling=false;enrollmentStarting=false;
  if(mic)await mic.stop();$('voiceConsent').checked=false;updateEnrollment();controls();
}

async function recordEnrollment(){
  if(enrollmentStarting)return;
  if(enrollmentMic){await finishEnrollment();return;}
  if(!activeProfile){enrollmentNotice('请先在上方“当前用户”选择本人档案，再录入声纹；访客不能保存声纹。');return;}
  if(!$('voiceConsent').checked){enrollmentNotice('请先勾选“我本人同意在这台电脑保存声纹特征”，再开始录音。');return;}
  if(enrollmentSamples.length>=3){enrollmentNotice('三段已经录满，点击“保存声纹”即可。');return;}
  cancelTurn();enrolling=true;enrollmentStarting=true;
  const version=++enrollmentVersion;
  updateEnrollment();controls();enrollmentNotice('正在打开麦克风，请留意浏览器的权限提示；允许后开始朗读。');
  try{
    await pauseWake();if(version!==enrollmentVersion)return;
    const mic=new Microphone();enrollmentMic=mic;enrollmentFrames=[];let count=0;
    await mic.start(chunk=>{if(version!==enrollmentVersion)return;enrollmentFrames.push(chunk);count+=chunk.length;if(count>=160000)finishEnrollment();},
      async()=>{if(version!==enrollmentVersion)return;await cancelEnrollment();enrollmentNotice('麦克风已断开，本段未保存。请检查设备后重试。');});
    if(version!==enrollmentVersion){await mic.stop();return;}
    enrollmentStarting=false;
    enrollmentTimer=setTimeout(finishEnrollment,12000);updateEnrollment();
    enrollmentNotice('正在录制，请自然朗读上方句子约6–10秒；再次点击“结束这一段”即可。');
    state('正在录入声纹，请自然说话…','listening');
  }catch(error){if(version!==enrollmentVersion)return;await cancelEnrollment();enrollmentNotice('未开始录音：'+micError(error));}
}

async function finishEnrollment(){
  const mic=enrollmentMic;if(!mic)return;
  enrollmentMic=null;clearTimeout(enrollmentTimer);const chunks=enrollmentFrames;enrollmentFrames=[];
  const version=enrollmentVersion;await mic.stop();if(version!==enrollmentVersion)return;
  enrolling=false;
  if(chunks.reduce((n,c)=>n+c.length,0)<16000*4){enrollmentNotice('这一段太短，请至少录制 4 秒。');}
  else{
    const bytes=new Uint8Array(encodeWav(chunks,16000));let binary='';
    for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    enrollmentSamples.push(btoa(binary));enrollmentNotice('本段已暂存在内存中，尚未保存声纹。');
  }
  updateEnrollment();controls();idle();
}

$('profileSelect').onchange=async()=>{await pauseWake();await cancelEnrollment();switchProfile($('profileSelect').value);$('speakerStatus').textContent='已手动选择用户；开启声纹识别后，每次说话会重新匹配。';resumeWake();};
$('createProfile').onclick=async()=>{try{const profile=await(await api('/api/profiles/create',{name:$('profileName').value})).json();await pauseWake();await refreshProfiles();switchProfile(profile.id);$('profileName').value='';resumeWake();}catch(error){notice(error.message);}};
$('saveSalutation').onclick=async()=>{
  if(!activeProfile)return;
  try{await api('/api/profiles/salutation',{profile_id:activeProfile,salutation:$('profileSalutation').value});await refreshProfiles();notice('称呼已保存。声纹匹配后的首次回答会使用它；点击“新对话”可重新测试。');}
  catch(error){notice(error.message);}
};
$('saveMemory').onclick=async()=>{
  if(!activeProfile){notice('请先在上方选择或创建用户档案，再保存记忆。');return;}
  if(memoryLocked()){notice(enrolling?'正在录入声纹，请等录入结束后再保存记忆。':'正在对话或收音，请等这一轮结束后再保存记忆。');return;}
  if(!$('memoryText').value.trim()){notice('请先写下要记住的内容，再点「保存记忆」。');return;}
  const identity=activeProfile,edited=!!memoryEdit,state=$('memoryState').value;memoryBusy=true;personalControls();
  try{
    const result=await(await api('/api/memory/save',{profile_id:identity,content:$('memoryText').value,id:memoryEdit,expected_revision:memoryEditRevision,status:$('memoryState').value,status_reason:$('memoryStateReason').value,event_date:$('memoryEventDate').value})).json();
    if(identity!==activeProfile)return;
    if(result.review){showMemoryReview(result.review);return;}
    if(typeof resetMemoryReview==='function')resetMemoryReview();
    resetMemoryEditor();await refreshMemories();
    const label=personalStatusNames[state]||'待核实';
    notice(edited?`记忆已更新（状态：${label}），可在下方列表查看。`:`记忆已保存（状态：${label}），可在下方列表查看。`);
  }catch(error){if(identity===activeProfile)notice(error.message);}
  finally{memoryBusy=false;personalControls();}
};
$('cancelMemoryEdit').onclick=()=>{if(!memoryLocked()){resetMemoryEditor();if(typeof resetMemoryReview==='function')resetMemoryReview();}};
$('deleteProfile').onclick=async()=>{
  if(!activeProfile||!window.confirm('删除这个用户的全部长期记忆和声纹？'))return;
  try{const identity=activeProfile;await api('/api/profiles/delete',{profile_id:identity});localStorage.removeItem('xiaoban-history-'+identity);switchProfile('');await refreshProfiles();}catch(error){notice(error.message);}
};
$('recordVoice').onclick=recordEnrollment;$('cancelEnroll').onclick=async()=>{await cancelEnrollment();enrollmentNotice('已取消录入，未保存的录音已清空。');};$('voiceConsent').onchange=personalControls;
$('saveVoice').onclick=async()=>{
  if(enrollmentSaving||enrollmentMic)return;
  if(!activeProfile){enrollmentNotice('请先选择用户档案，再保存声纹。');return;}
  if(!$('voiceConsent').checked){enrollmentNotice('请先勾选「我本人同意在这台电脑保存声纹特征」，再保存声纹。');return;}
  if(enrollmentSamples.length!==3){enrollmentNotice(`还需要录制 ${3-enrollmentSamples.length} 段语音，才能保存声纹。`);return;}
  const identity=activeProfile,name=profiles.find(p=>p.id===identity)?.name||'当前用户';
  enrollmentSaving=true;enrolling=true;controls();
  try{
    await api('/api/speaker/enroll',{profile_id:identity,consent:true,recordings:enrollmentSamples});
    enrollmentSamples=[];
    $('speakerStatus').textContent=`声纹已保存：${name}。勾选「用声纹自动识别本轮说话的人」后，用麦克风说话即可验证。`;
    await refreshProfiles();
    enrollmentNotice(`声纹已保存（${name}）：三段录音已合并为特征，原始录音未落盘。勾选「用声纹自动识别」后说一句话验证。`);
  }
  catch(error){enrollmentNotice('声纹未保存：'+error.message);}
  finally{enrollmentSaving=false;enrolling=false;updateEnrollment();controls();}
};
$('deleteVoice').onclick=async()=>{if(!activeProfile||!window.confirm('删除当前用户的声纹特征？'))return;try{await api('/api/speaker/delete',{profile_id:activeProfile});await cancelEnrollment();await refreshProfiles();$('speakerStatus').textContent='声纹已删除。';}catch(error){notice(error.message);}};
$('speakerAuto').onchange=async()=>{
  await pauseWake();
  const unsaved=enrollmentSamples.length;
  if($('speakerAuto').checked)switchProfile('');
  $('speakerStatus').textContent=$('speakerAuto').checked?'下次说话时匹配；不确定时按访客处理。':'已关闭声纹识别，可手动选择用户。';
  $('speakerRetry').hidden=true;controls();resumeWake();
  if($('speakerAuto').checked){
    const enrolled=profiles.some(profile=>profile.enrolled);
    notice(!enrolled?'已开启声纹自动识别，但本机还没有保存过声纹：请先在下方录入三段语音并保存声纹。':
      `已开启声纹自动识别：当前先按访客，用麦克风说话后自动匹配。${unsaved?`（已清空未保存的 ${unsaved} 段录音）`:''}文字输入不参与声纹识别。`);
  }else notice('已关闭声纹自动识别，可在上方手动选择用户。');
};
refreshProfiles().catch(error=>notice(error.message));updateEnrollment();
