// Display-only navigation. Switching pages never submits, confirms or resets a draft.
const workspacePages={chat:['navChat','viewChat'],personal:['navPersonal','viewPersonal'],shared:['navShared','viewShared'],memoryV2:['navMemoryV2','viewMemoryV2'],speech:['navSpeech','viewSpeech'],models:['navModels','viewModels'],diagnostics:['navDiagnostics','viewDiagnostics']};
const sharedPages={question:['navSharedQuestion','sharedPageQuestion'],library:['navSharedLibrary','sharedPageLibrary'],import:['navSharedImport','sharedPageImport'],backup:['navSharedBackup','sharedPageBackup'],members:['navSharedMembers','sharedPageMembers']};
let workspacePage='chat',sharedPage='question',workspaceReturn='chat',workspaceLoginTarget=null;
function setWorkspaceTabs(pages,selected){
  for(const [key,[button,panel]] of Object.entries(pages)){$(button).setAttribute('aria-selected',String(key===selected));$(button).tabIndex=key===selected?0:-1;$(panel).hidden=key!==selected;}
}
function showWorkspacePage(key){
  if(!workspacePages[key])return;if(workspacePage==='models'&&key!=='models'&&typeof modelSettingsUI!=='undefined')modelSettingsUI.leave();workspacePage=key;setWorkspaceTabs(workspacePages,key);
  $('speechSettingsPanel').open=key==='speech';
  if(key!=='diagnostics'){$('conversationDiagnosticsPanel').open=false;$('speakerDiagnosticsPanel').open=false;}
  if(key==='diagnostics'){if(typeof conversationDiagnostics!=='undefined')conversationDiagnostics.render();if(typeof diagnosticOverview!=='undefined')diagnosticOverview.load();}
  if(key==='speech'&&typeof audioDevices!=='undefined')audioDevices.refresh();
  if(key==='models'&&typeof modelSettingsUI!=='undefined')modelSettingsUI.load();
  // 回到对话页时重新核对登录/空间状态：令牌过期或空间被移除后，开关要立刻变灰并说明原因。
  if(key==='chat'&&typeof companionMemorySync==='function')companionMemorySync();
  if(key==='memoryV2'&&typeof structuredMemory!=='undefined'){structuredMemory.sync();}
  workspaceActivity();
}
function showSharedPage(key){
  if(key==='question'){
    showWorkspacePage('chat');
    if(typeof companionMemorySetMode==='function')companionMemorySetMode(sharedState.space?'shared':'auto');
    return;
  }
  if(key==='library'||key==='import'){
    workspaceOpenMemory();
    if(typeof structuredMemory!=='undefined'&&sharedState.space){
      $('v2Scope').value=sharedState.space.id;structuredMemory.refresh();
      if(key==='import')$('v2ImportDisclosure').open=true;
    }
    return;
  }
  if(!sharedPages[key])return;sharedPage=key;setWorkspaceTabs(sharedPages,key);workspaceActivity();
}
function showSharedEditor(){workspaceOpenMemory();}

function workspaceActivity(){
  const active=[];
  const profile=typeof profiles!=='undefined'&&typeof activeProfile!=='undefined'?profiles.find(p=>p.id===activeProfile):null;
  const account=typeof sharedState!=='undefined'&&sharedState.token?sharedState.account:null;
  $('workspaceProfile').textContent=account?account.name+'，欢迎回来':(profile?.name?profile.name+'，你好':'你好，欢迎来聊聊');
  $('workspaceProfile').title=account?'管理账号与空间':'设置称呼与声音识别';
  const preview=typeof speechPreviewActive==='function'&&speechPreviewActive();
  workspaceReturn=typeof enrolling!=='undefined'&&enrolling?'personal':preview?'speech':typeof sharedState!=='undefined'&&sharedState.asking?'shared':'chat';
  if(preview)active.push('正在试听声音');
  if(typeof recording!=='undefined'&&recording)active.push('正在录音');
  if(typeof enrolling!=='undefined'&&enrolling)active.push('正在录入声纹');
  if(typeof wakeEnabled!=='undefined'&&wakeEnabled)active.push('语音唤醒已开启');
  if(typeof cameraStream!=='undefined'&&cameraStream)active.push('摄像头已开启');
  if(typeof busy!=='undefined'&&busy)active.push('对话正在处理');
  if(typeof speaking!=='undefined'&&speaking)active.push('正在播放声音');
  if(typeof sharedVoiceActive==='function'&&sharedVoiceActive())active.push('共同记忆语音已开启');
  if(typeof sharedState!=='undefined'&&sharedState.asking)active.push('共同记忆正在回答');
  const draft=typeof sharedAgentDraft!=='undefined'&&!!sharedAgentDraft;
  if(draft)active.push('共同记忆有待确认内容');
  $('workspaceActivity').hidden=!active.length;$('workspaceActivityText').textContent=active.join(' · ');
  $('workspaceReviewDraft').hidden=!draft;
  if(typeof gettingStarted!=='undefined')gettingStarted.refresh();
  $('workspaceReturnChat').hidden=workspacePage===workspaceReturn;
  $('workspaceReturnChat').textContent={chat:'返回对话',personal:'返回声纹录入',speech:'返回声音试听',shared:'返回记忆问答'}[workspaceReturn];
}
function bindWorkspaceTabs(pages,select){
  const keys=Object.keys(pages);
  for(const [key,[id]] of Object.entries(pages)){
    $(id).onclick=()=>select(key);
    $(id).onkeydown=event=>{
      const i=keys.indexOf(key),next=event.key==='ArrowRight'?keys[(i+1)%keys.length]:event.key==='ArrowLeft'?keys[(i+keys.length-1)%keys.length]:event.key==='Home'?keys[0]:event.key==='End'?keys.at(-1):null;
      if(next){event.preventDefault();select(next);$(pages[next][0]).focus();}
    };
  }
}
bindWorkspaceTabs(workspacePages,showWorkspacePage);bindWorkspaceTabs(sharedPages,showSharedPage);
$('workspaceReturnChat').onclick=()=>{showWorkspacePage(workspaceReturn);if(workspaceReturn==='shared')showSharedPage('question');};
$('sharedOpenOverview').onclick=()=>showWorkspacePage('diagnostics');
$('workspaceProfile').onclick=()=>sharedState.token?workspaceOpenAccount('chat'):showWorkspacePage('personal');
$('workspaceSharedVoice').hidden=true;
$('workspaceSharedVoice').onclick=()=>showWorkspacePage('chat');
$('workspaceReviewDraft').onclick=()=>{showWorkspacePage('shared');showSharedPage('question');$('sharedAgentReview').scrollIntoView?.({block:'nearest'});};
showWorkspacePage('chat');showSharedPage('members');

// Navigation is explicit; none of these actions creates a space or saves data.
function workspaceOpenMemory(){
  if(!sharedState.token){workspaceLoginTarget='memoryV2';showWorkspacePage('shared');$('sharedAccountName').focus();return;}
  showWorkspacePage('memoryV2');
}
function workspaceOpenAccount(target='chat'){
  workspaceLoginTarget=sharedState.token?null:target;
  showWorkspacePage('shared');showSharedPage('members');
  if(!sharedState.token)$('sharedAccountName').focus();
}
async function workspaceAfterLogin(){
  const target=workspaceLoginTarget;workspaceLoginTarget=null;
  if(target==='memoryV2'){showWorkspacePage('memoryV2');await structuredMemory.refresh();}
  else if(target==='join'){showWorkspacePage('shared');showSharedPage('members');$('joinCode').focus();}
  else if(target==='memoryAdd'){showWorkspacePage('memoryV2');await structuredMemory.refresh();$('v2ImportDisclosure').open=true;}
  else if(target==='chat')showWorkspacePage('chat');
}
$('chatOpenMemory').onclick=workspaceOpenMemory;
$('sharedOpenMemory').onclick=workspaceOpenMemory;
$('personalOpenMemory').onclick=workspaceOpenMemory;
