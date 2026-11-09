// Explicit memory edits and opt-in voiceprint enrollment. Audio stays in RAM.
let profiles=[], memoryEdit=null, personalVersion=0;
let voiceMatchedProfile='';
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
let enrollmentMic=null, enrollmentFrames=[], enrollmentSamples=[], enrollmentTimer=null, enrollmentVersion=0, enrollmentSaving=false;
const enrollmentPrompts=[
  '请读：今天我想和机器人聊聊天，也希望它能慢慢记住我的习惯。',
  '请读：周末我喜欢做一些自己感兴趣的事情，生活里还有很多值得发现的细节。',
  '请读：这是我的第三段声音，我会用自然的语速说话，让它认识我的声音。'
];

function personalControls(){
  const locked=busy||recording||enrolling||enrollmentSaving||wakePhase==='command';
  $('createProfile').disabled=locked;$('profileName').disabled=locked;
  for(const id of ['saveMemory','deleteProfile','deleteVoice','saveSalutation','profileSalutation'])$(id).disabled=!activeProfile||locked;
  $('memoryText').disabled=!activeProfile||locked;
  $('recordVoice').disabled=!activeProfile||!speakerReady||enrollmentSaving||(!enrollmentMic&&(locked||enrollmentSamples.length>=3||!$('voiceConsent').checked));
  $('saveVoice').disabled=!activeProfile||enrollmentSamples.length!==3||enrollmentSaving||!!enrollmentMic||!$('voiceConsent').checked;
  $('voiceConsent').disabled=enrollmentSaving||!!enrollmentMic;
  $('cancelEnroll').disabled=enrollmentSaving;
}

async function refreshProfiles(){
  const result=await(await api('/api/profiles/list',{})).json();profiles=result.profiles;
  $('profileSelect').replaceChildren();
  const guest=document.createElement('option');guest.value='';guest.textContent='访客 · 不读取个人记忆';$('profileSelect').append(guest);
  for(const profile of profiles){const option=document.createElement('option');option.value=profile.id;option.textContent=profile.name+(profile.enrolled?' · 已录声纹':'');$('profileSelect').append(option);}
  if(activeProfile&&!profiles.some(p=>p.id===activeProfile))switchProfile('');
  $('profileSelect').value=activeProfile;fillSalutation();controls();
}

function switchProfile(identity){
  voiceMatchedProfile='';
  if(identity===activeProfile)return;
  cancelTurn();activeProfile=identity;messages=[];render();
  memoryEdit=null;$('memoryText').value='';$('saveMemory').textContent='保存记忆';
  $('profileSelect').value=identity;
  fillSalutation();
  enrollmentSamples=[];enrollmentFrames=[];$('voiceConsent').checked=false;updateEnrollment();
  refreshMemories();controls();
}

function applySpeaker(result){
  const identity=result.matched&&profiles.some(p=>p.id===result.id)?result.id:'';
  switchProfile(identity);
  voiceMatchedProfile=identity;
  $('speakerStatus').textContent=identity?'本轮声音匹配：'+result.name+'（可在上方更正）':'本轮按访客处理：'+(result.reason||'未确认说话人');
  if(!identity&&result.diagnostics?.speech_seconds!==undefined){
    $('speakerStatus').textContent+=`（有效语音 ${result.diagnostics.speech_seconds} 秒）`;
  }
}

async function refreshMemories(){
  const version=++personalVersion,identity=activeProfile;
  $('memoryList').replaceChildren();if(!identity)return;
  try{
    const data=await(await api('/api/memory/list',{profile_id:identity})).json();
    if(version!==personalVersion||identity!==activeProfile)return;
    for(const memory of data.memories){
      const row=document.createElement('li'),content=document.createElement('span');content.textContent=memory.content;
      const edit=document.createElement('button');edit.className='text-button';edit.textContent='编辑';edit.onclick=()=>{if(busy||enrolling)return;memoryEdit=memory.id;$('memoryText').value=memory.content;$('saveMemory').textContent='更新记忆';};
      const remove=document.createElement('button');remove.className='text-button';remove.textContent='删除';remove.onclick=async()=>{
        if(busy||enrolling||identity!==activeProfile||!window.confirm('删除这条长期记忆？'))return;
        try{await api('/api/memory/delete',{profile_id:identity,id:memory.id});refreshMemories();}catch(error){notice(error.message);}
      };
      row.append(content,edit,remove);$('memoryList').append(row);
    }
    if(!data.memories.length){const row=document.createElement('li');row.textContent='还没有保存的记忆。';$('memoryList').append(row);}
  }catch(error){if(version===personalVersion)notice(error.message);}
}

function updateEnrollment(){
  $('recordVoice').textContent=enrollmentMic?'结束这一段':`录制第 ${Math.min(3,enrollmentSamples.length+1)} 段`;
  $('enrollPrompt').textContent=enrollmentSamples.length===3?'三段录音已准备好，点击「保存声纹」。':`${enrollmentSamples.length}/3 已录制。${enrollmentPrompts[enrollmentSamples.length]}`;
  personalControls();
}

async function cancelEnrollment(){
  if(enrollmentSaving)return;
  enrollmentVersion++;clearTimeout(enrollmentTimer);
  const mic=enrollmentMic;enrollmentMic=null;enrollmentFrames=[];enrollmentSamples=[];enrolling=false;
  if(mic)await mic.stop();$('voiceConsent').checked=false;updateEnrollment();controls();
}

async function recordEnrollment(){
  if(enrollmentMic){await finishEnrollment();return;}
  if(!activeProfile||!$('voiceConsent').checked||enrollmentSamples.length>=3)return;
  cancelTurn();enrolling=true;controls();await pauseWake();
  const version=++enrollmentVersion,mic=new Microphone();enrollmentMic=mic;enrollmentFrames=[];let count=0;
  try{
    await mic.start(chunk=>{if(version!==enrollmentVersion)return;enrollmentFrames.push(chunk);count+=chunk.length;if(count>=160000)finishEnrollment();});
    if(version!==enrollmentVersion){await mic.stop();return;}
    enrollmentTimer=setTimeout(finishEnrollment,12000);updateEnrollment();state('正在录入声纹，请自然说话…','listening');
  }catch(error){await cancelEnrollment();notice(micError(error));}
}

async function finishEnrollment(){
  const mic=enrollmentMic;if(!mic)return;
  enrollmentMic=null;clearTimeout(enrollmentTimer);const chunks=enrollmentFrames;enrollmentFrames=[];
  const version=enrollmentVersion;await mic.stop();if(version!==enrollmentVersion)return;
  enrolling=false;
  if(chunks.reduce((n,c)=>n+c.length,0)<16000*4){notice('这一段太短，请至少录制 4 秒。');}
  else{
    const bytes=new Uint8Array(encodeWav(chunks,16000));let binary='';
    for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    enrollmentSamples.push(btoa(binary));notice('本段已暂存在内存中，尚未保存声纹。');
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
$('saveMemory').onclick=async()=>{try{await api('/api/memory/save',{profile_id:activeProfile,content:$('memoryText').value,id:memoryEdit});memoryEdit=null;$('memoryText').value='';$('saveMemory').textContent='保存记忆';await refreshMemories();notice('记忆已保存。');}catch(error){notice(error.message);}};
$('cancelMemoryEdit').onclick=()=>{memoryEdit=null;$('memoryText').value='';$('saveMemory').textContent='保存记忆';};
$('deleteProfile').onclick=async()=>{
  if(!activeProfile||!window.confirm('删除这个用户的全部长期记忆和声纹？'))return;
  try{const identity=activeProfile;await api('/api/profiles/delete',{profile_id:identity});localStorage.removeItem('xiaoban-history-'+identity);switchProfile('');await refreshProfiles();}catch(error){notice(error.message);}
};
$('recordVoice').onclick=recordEnrollment;$('cancelEnroll').onclick=()=>cancelEnrollment();$('voiceConsent').onchange=personalControls;
$('saveVoice').onclick=async()=>{
  if(enrollmentSamples.length!==3||!$('voiceConsent').checked)return;
  enrollmentSaving=true;enrolling=true;controls();
  try{await api('/api/speaker/enroll',{profile_id:activeProfile,consent:true,recordings:enrollmentSamples});enrollmentSamples=[];$('speakerStatus').textContent='声纹已保存，可开启自动识别。';await refreshProfiles();}
  catch(error){notice(error.message);}
  finally{enrollmentSaving=false;enrolling=false;updateEnrollment();controls();}
};
$('deleteVoice').onclick=async()=>{if(!activeProfile||!window.confirm('删除当前用户的声纹特征？'))return;try{await api('/api/speaker/delete',{profile_id:activeProfile});await cancelEnrollment();await refreshProfiles();$('speakerStatus').textContent='声纹已删除。';}catch(error){notice(error.message);}};
$('speakerAuto').onchange=async()=>{await pauseWake();if($('speakerAuto').checked)switchProfile('');$('speakerStatus').textContent=$('speakerAuto').checked?'下次说话时匹配；不确定时按访客处理。':'已关闭声纹识别，可手动选择用户。';resumeWake();};
refreshProfiles().catch(error=>notice(error.message));updateEnrollment();
