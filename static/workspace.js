// Display-only navigation. Switching pages never submits, confirms or resets a draft.
const workspacePages={chat:['navChat','viewChat'],personal:['navPersonal','viewPersonal'],shared:['navShared','viewShared'],speech:['navSpeech','viewSpeech'],models:['navModels','viewModels'],diagnostics:['navDiagnostics','viewDiagnostics']};
const sharedPages={question:['navSharedQuestion','sharedPageQuestion'],library:['navSharedLibrary','sharedPageLibrary'],import:['navSharedImport','sharedPageImport'],backup:['navSharedBackup','sharedPageBackup'],members:['navSharedMembers','sharedPageMembers']};
let workspacePage='chat',sharedPage='question',workspaceReturn='chat';
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
  workspaceActivity();
}
function showSharedPage(key){if(!sharedPages[key])return;sharedPage=key;setWorkspaceTabs(sharedPages,key);workspaceActivity();}
function showSharedEditor(){showWorkspacePage('shared');showSharedPage('library');$('sharedEditorDisclosure').open=true;}
function workspaceActivity(){
  const active=[];
  const profile=typeof profiles!=='undefined'&&typeof activeProfile!=='undefined'?profiles.find(p=>p.id===activeProfile):null;
  $('workspaceProfile').textContent='当前身份：'+(profile?.name||'访客')+' · 管理档案';
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
$('workspaceProfile').onclick=()=>showWorkspacePage('personal');
$('workspaceSharedVoice').onclick=()=>{
  showWorkspacePage('shared');
  showSharedPage('members');
  $('sharedVoiceSetup').open=true;
  if (!$('sharedSignedIn').hidden) $('sharedVoiceSetup').scrollIntoView?.({block:'nearest'});
};
$('workspaceReviewDraft').onclick=()=>{showWorkspacePage('shared');showSharedPage('question');$('sharedAgentReview').scrollIntoView?.({block:'nearest'});};
showWorkspacePage('chat');showSharedPage('question');
