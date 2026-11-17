// Space identities are password-authenticated and independent of voice/personal profiles.
// Tokens, shared drafts and answers stay in this page's memory, never localStorage.
const sharedState={token:null,account:null,spaces:[],space:null,members:[],memories:[],edit:null,plan:null,
  conversation:null,epoch:0,loadVersion:0,busy:false,asking:false,askVersion:0,askAbort:null,turn:null};
const sharedStatusNames={pending:'待核实',valid:'有效',outdated:'过时'};
const sharedRoles={owner:'管理员',editor:'编辑成员',reader:'只读成员'};
function sharedMessage(text){$('sharedStatus').textContent=text;}
// 保存失败时页面顶部的提示容易错过（编辑器在长表单底部），这里在按钮旁再报一次。
function sharedEditorNotice(text){
  const node=$('sharedEditorStatus');node.textContent=text||'';node.hidden=!text;
  if(text&&typeof node.scrollIntoView==='function')node.scrollIntoView({block:'nearest'});
}
function sharedResetReview(){sharedState.plan=null;$('sharedReviewPanel').hidden=true;$('sharedPublishConsent').checked=false;$('sharedConflictConsent').checked=false;$('sharedReviewSummary').textContent='';$('sharedReviewContent').replaceChildren();}
function sharedCloseHistory(){$('sharedHistoryPanel').hidden=true;$('sharedHistoryInfo').textContent='';$('sharedHistoryList').replaceChildren();}
function sharedClearAnswer(preserveTopic=false){if(typeof sharedFeedbackResetAnswer==='function')sharedFeedbackResetAnswer();if(typeof sharedResetAgent==='function')sharedResetAgent();if(!preserveTopic)sharedResetTopic();sharedStopAsk(false);$('sharedAgentPanel').hidden=true;$('sharedAgentStatus').textContent='';$('sharedAgentSteps').replaceChildren();$('sharedAnswer').textContent='';$('sharedRetrievalInfo').textContent='';sharedClearSourceCards();if(typeof sharedCloseEvidence==='function')sharedCloseEvidence();}
function sharedResetEditor(){
  sharedState.edit=null;sharedResetReview();$('sharedMemoryStatus').value='pending';$('sharedStatusReason').value='';$('sharedMemoryText').value='';$('sharedEventDate').value='';$('sharedSourceNote').value='';
  $('sharedVisibility').value='selected';$('sharedEditPolicy').value='author';$('sharedEditorTitle').textContent='发布共同记忆';sharedRenderAudience();sharedEditorLabels();sharedEditorNotice('');
}
// 保存分两步：先预览核对，勾选确认后才写入。按钮文案随「新建／编辑」切换，避免找不到保存入口。
function sharedEditorLabels(){
  const editing=!!sharedState.edit;
  $('sharedPreview').textContent=editing?'保存修改（先预览核对）':'保存新记忆（先预览核对）';
  $('sharedConfirm').textContent=editing?'确认保存修改':'确认发布共同记忆';
  $('sharedSaveHint').textContent=editing
    ?'修改不会自动保存：点「保存修改（先预览核对）」，勾选确认项后再点「确认保存修改」才会写入；点「取消编辑」放弃本次修改。'
    :'发布需要两步：先点「保存新记忆（先预览核对）」查看归属与可见范围，勾选确认后再点「确认发布共同记忆」才会写入。';
}
function sharedClearSpace(){
  if(typeof modelSettingsUI!=="undefined")modelSettingsUI.invalidate();
  if(typeof diagnosticOverview!=='undefined')diagnosticOverview.clearShared();
  if(typeof sharedBatchesClear==='function')sharedBatchesClear();
  if(typeof sharedRecoveryClear==='function')sharedRecoveryClear();
  if(typeof sharedBackupClear==='function')sharedBackupClear();
  if(typeof sharedFeedbackClear==='function')sharedFeedbackClear();
  if(typeof sharedReplacementClear==='function')sharedReplacementClear();
  if(typeof sharedCloseEvidence==='function')sharedCloseEvidence();
  $('sharedStatusFilter').value='all';$('sharedIncludeOutdated').checked=false;
  if(typeof materialReset==='function')materialReset();
  if(typeof sharedVoiceStop==='function')sharedVoiceStop();
  sharedState.epoch++;if(typeof sharedAuditClear==='function')sharedAuditClear();sharedState.loadVersion++;sharedState.busy=false;sharedState.space=null;sharedState.members=[];sharedState.memories=[];
  sharedClearAnswer();$('sharedQuestion').value='';sharedCloseHistory();sharedResetEditor();$('sharedSpaceSelect').value='';$('sharedSearch').value='';
  $('sharedMemberList').replaceChildren();$('sharedMemoryList').replaceChildren();$('sharedCount').textContent='请选择空间';$('sharedSpaceInfo').textContent='';sharedControls();
  if(typeof sharedMemberNotice==='function')sharedMemberNotice('');
  if($('sharedMemberHint'))$('sharedMemberHint').textContent='';
}
function sharedLock(message='已退出空间账号，页面中的共同记忆已清空。'){
  const oldToken=sharedState.token;sharedResetTopic();
  sharedState.token=null;sharedState.account=null;sharedState.spaces=[];sharedClearSpace();
  $('sharedSpaceSelect').replaceChildren();const option=document.createElement('option');option.value='';option.textContent='请选择空间';$('sharedSpaceSelect').append(option);
  $('sharedPassword').value='';$('sharedOldPassword').value='';$('sharedNewPassword').value='';$('sharedQuestion').value='';$('sharedIdentity').textContent='';
  sharedMessage(message);sharedControls();
  return oldToken;
}
function sharedPersonalChanged(){
  if(!sharedState.token)return;
  const token=sharedLock('个人档案发生切换，空间账号已锁定；使用共同记忆请重新登录。');
  fetch('/api/shared/auth/logout',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:'{}'}).catch(()=>{});
}
async function sharedApi(path,data={},signal=null){
  const token=sharedState.token,epoch=sharedState.epoch;
  const headers={'Content-Type':'application/json'};if(token)headers.Authorization='Bearer '+token;
  const response=await fetch('/api/shared'+path,{method:'POST',headers,body:JSON.stringify(data),signal});
  const result=await response.json();
  if(result.diagnostic&&token===sharedState.token&&epoch===sharedState.epoch&&typeof sharedAuditObserve==='function')sharedAuditObserve(result.diagnostic);
  if(!response.ok){
    if(response.status===401&&token===sharedState.token&&epoch===sharedState.epoch)sharedLock('登录失效或账号口令不正确，请重新登录。');
    if(response.status===403&&token===sharedState.token&&epoch===sharedState.epoch)sharedClearSpace();
    const error=new Error(result.error||'操作失败，请重试。');error.status=response.status;error.diagnostic=result.diagnostic;throw error;
  }
  return result;
}
async function sharedRun(work){
  if(sharedState.busy||sharedState.asking||(typeof companionMemoryPending==='function'&&companionMemoryPending())){
    const wait='上一步操作还在处理，请稍候再点。';sharedMessage(wait);
    if($('sharedEditorDisclosure').open)sharedEditorNotice(wait);
    return;
  }
  const epoch=sharedState.epoch;sharedState.busy=true;sharedControls();
  try{await work(epoch);}
  catch(error){
    sharedMessage(error.message);
    // 编辑器在长表单底部，顶部提示容易错过，所以按钮旁同步报一次。
    if($('sharedEditorDisclosure').open)sharedEditorNotice('保存未完成：'+error.message);
  }
  finally{if(epoch===sharedState.epoch)sharedState.busy=false;sharedControls();}
}
function sharedControls(){
  if(typeof workspaceActivity==='function')workspaceActivity();
  const logged=!!sharedState.token,space=sharedState.space,locked=sharedState.busy||sharedState.asking||(typeof companionMemoryPending==='function'&&companionMemoryPending())||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());
  const writer=!!space&&space.role!=='reader',editable=writer&&(!sharedState.edit||sharedState.edit.can_edit);
  const configurable=editable&&(!sharedState.edit||sharedState.edit.can_configure);
  $('sharedLoginPanel').hidden=logged;$('sharedSignedIn').hidden=!logged;
  for(const id of ['sharedAccountName','sharedPassword','sharedLogin','sharedRegister'])$(id).disabled=locked;
  for(const id of ['sharedRefresh','sharedChangePassword','sharedCreateSpace','sharedSpaceName','sharedSpaceKind','sharedOldPassword','sharedNewPassword'])$(id).disabled=!logged||locked;
  $('sharedSpaceSelect').disabled=!logged||locked;$('sharedLogout').disabled=!logged;
  $('sharedMemberAdmin').hidden=!space||space.role!=='owner';$('sharedDeleteSpace').hidden=!space||space.role!=='owner';
  for(const id of ['sharedMemberName','sharedMemberRole','sharedSetMember','sharedDeleteSpace'])$(id).disabled=!space||space.role!=='owner'||locked;
  for(const id of ['sharedMemoryText','sharedMemoryStatus','sharedStatusReason','sharedEventDate','sharedSourceNote','sharedPreview'])$(id).disabled=!editable||locked;
  for(const id of ['sharedVisibility','sharedEditPolicy'])$(id).disabled=!configurable||locked;
  if(typeof sharedFeedbackControls==='function')sharedFeedbackControls();
  if(typeof sharedBackupControls==='function')sharedBackupControls();
  if(typeof sharedRecoveryControls==='function')sharedRecoveryControls();
  if(typeof sharedBatchesControls==='function')sharedBatchesControls();
  sharedAudienceControls();
  $('sharedCancelEdit').disabled=locked;$('sharedCancelReview').disabled=locked;
  $('sharedSearch').disabled=!space;$('sharedStatusFilter').disabled=!space;$('sharedIncludeOutdated').disabled=!space||locked;
  $('sharedPublishConsent').disabled=locked||!sharedState.plan;
  $('sharedConflictConsent').disabled=locked||!sharedState.plan;
  $('sharedConfirm').disabled=locked||!sharedState.plan||!$('sharedPublishConsent').checked||
    (sharedState.plan.conflicts.length>0&&!$('sharedConflictConsent').checked);
  $('sharedNewTopic').disabled=!space||locked;$('sharedAgentMode').disabled=!space||locked;
  for(const button of $('sharedChoices').children)button.disabled=locked;
  $('sharedAsk').disabled=!space||locked;$('sharedQuestion').disabled=!space||locked;$('sharedStopAsk').disabled=!sharedState.asking;
  $('sharedOwnership').textContent=space?`归属：${space.kind==='family'?'家庭':'团队'}「${space.name}」；发布者：${sharedState.edit?.author_name||sharedState.account.name}。`:'请选择空间。';
  if(typeof sharedReplacementControls==='function')sharedReplacementControls();
  if(typeof sharedAgentControls==='function')sharedAgentControls();
  if(typeof sharedAuditControls==='function'){sharedAuditControls();sharedAuditSchedule();}
  if(typeof sharedVoiceControls==='function')sharedVoiceControls();
  if(typeof materialControls==='function')materialControls();
  if(typeof companionMemorySync==='function')companionMemorySync();
  if(typeof structuredMemory!=='undefined')structuredMemory.sync();
}
function sharedAudienceHintText(){
  if($('sharedVisibility').value==='all')return '全体成员可见，包括以后加入的成员。';
  const chosen=[...$('sharedAudience').querySelectorAll('input')].filter(box=>box.checked&&box.dataset.implicit!=='true').length;
  if(chosen)return '已选 '+chosen+' 位成员可见；作者和管理员始终可见。';
  // 「指定成员」却一个人都没选，等于只有自己和管理员能看到，必须说清楚，否则会以为已经共享。
  return sharedState.members.length>1
    ?'注意：当前选了 0 位成员，保存后只有你（作者）和空间管理员能看到，其他成员在记忆库和问答里都读不到。要让全班都能问，请改选「全体成员」。'
    :'注意：当前空间只有你一位成员，「指定成员」等于只有你自己能看到。要让以后加入的成员也能看到，请改选「全体成员」。';
}
function sharedRenderAudience(){
  const previous=new Set([...$('sharedAudience').querySelectorAll('input:checked')].map(x=>x.value));
  const selected=sharedState.edit?new Set(sharedState.edit.viewers):previous;
  $('sharedAudience').replaceChildren();$('sharedAudience').hidden=$('sharedVisibility').value!=='selected';
  const author=sharedState.edit?.author||sharedState.account?.id;
  for(const member of sharedState.members){
    const label=document.createElement('label'),box=document.createElement('input'),text=document.createElement('span');box.type='checkbox';box.value=member.id;
    const implicit=member.role==='owner'||member.id===author;box.checked=implicit||selected.has(member.id);box.disabled=implicit;
    box.dataset.implicit=implicit?'true':'false';text.textContent=member.name+(implicit?'（始终可见）':'');
    box.onchange=()=>{sharedResetReview();sharedRenderAudienceHint();sharedControls();};label.append(box,text);$('sharedAudience').append(label);
  }
  sharedRenderAudienceHint();
  sharedAudienceControls();
}
function sharedRenderAudienceHint(){
  const node=$('sharedAudienceHint');if(!node)return;
  const hint=sharedAudienceHintText();node.textContent=hint;
  node.classList.toggle('audience-warning',hint.startsWith('注意：'));
}
function sharedAudienceControls(){
  const space=sharedState.space,locked=sharedState.busy||sharedState.asking||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());
  const configurable=space&&space.role!=='reader'&&(!sharedState.edit||sharedState.edit.can_configure);
  for(const box of $('sharedAudience').querySelectorAll('input'))box.disabled=locked||!configurable||box.dataset.implicit==='true';
}
function sharedRenderSpaces(){
  $('sharedSpaceSelect').replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent='请选择空间';$('sharedSpaceSelect').append(empty);
  for(const space of sharedState.spaces){const option=document.createElement('option');option.value=space.id;option.textContent=`${space.kind==='family'?'家庭':'团队'} · ${space.name} · ${sharedRoles[space.role]}`;$('sharedSpaceSelect').append(option);}
  $('sharedSpaceSelect').value=sharedState.space?.id||'';
}
async function sharedRefresh(){
  const epoch=sharedState.epoch,result=await sharedApi('/spaces/list');if(epoch!==sharedState.epoch)return;
  sharedState.account=result.account;sharedState.spaces=result.spaces;$('sharedIdentity').textContent='当前空间账号：'+result.account.name;
  if(sharedState.space&&!result.spaces.some(s=>s.id===sharedState.space.id))sharedClearSpace();
  sharedRenderSpaces();
  // Default only among the authenticated account's authorized spaces.
  if(!sharedState.space&&sharedState.spaces.length){sharedState.space=sharedState.spaces[0];sharedRenderSpaces();}
  if(sharedState.space)await sharedLoadCurrent();else sharedControls();
}
async function sharedLoadCurrent(){
  if(!sharedState.space)return;
  const epoch=sharedState.epoch,id=sharedState.space.id,load=++sharedState.loadVersion;
  const [result,members]=await Promise.all([sharedApi('/memories/list',{space_id:id}),sharedApi('/members/list',{space_id:id})]);
  if(epoch!==sharedState.epoch||sharedState.space?.id!==id||load!==sharedState.loadVersion)return;
  if(sharedState.space.version!==result.space.version){if(typeof modelSettingsUI!=='undefined')modelSettingsUI.invalidate();if(typeof diagnosticOverview!=='undefined')diagnosticOverview.clearShared();if(typeof batchStale==='function')batchStale();else if(typeof sharedBatchesClear==='function')sharedBatchesClear();if(typeof sharedRecoveryClear==='function')sharedRecoveryClear();if(typeof sharedFeedbackClear==='function')sharedFeedbackClear();if(typeof sharedReplacementClear==='function')sharedReplacementClear();if(typeof sharedCloseEvidence==='function')sharedCloseEvidence();sharedResetReview();sharedCloseHistory();sharedClearAnswer();$('sharedQuestion').value='';}
  const materialChanged=sharedState.space.version!==result.space.version;
  sharedState.space=result.space;sharedState.members=members.members;sharedState.memories=result.memories;
  if(typeof materialPermissionsChanged==='function'&&materialChanged)materialPermissionsChanged();
  else if(typeof materialAudience==='function')materialAudience();
  if(sharedState.edit&&!result.memories.some(x=>x.id===sharedState.edit.id&&x.can_edit))sharedResetEditor();
  $('sharedSpaceInfo').textContent=`${result.space.kind==='family'?'家庭':'团队'}空间 · 我的角色：${sharedRoles[result.space.role]}。管理员始终可管理空间内全部记忆。`;
  if($('sharedStatus').textContent==='已登录空间账号。请选择空间或创建家庭／团队空间。')sharedMessage('当前空间已就绪。');
  sharedRenderMembers();sharedRenderMemories();if(!sharedState.edit)sharedRenderAudience();sharedControls();sharedAudienceControls();
  // 停在「资料导入」页时，登录、切换空间或内容变化后自动读取批次，避免用户看到空白列表以为资料丢了。
  if(typeof sharedBatchesAutoLoad==='function'&&typeof sharedPage!=='undefined'&&sharedPage==='import')sharedBatchesAutoLoad();
}
function sharedRenderMembers(){
  $('sharedMemberList').replaceChildren();const space=sharedState.space;
  const owner=space?.role==='owner';
  const hint=$('sharedMemberHint');
  if(hint)hint.textContent=!space?'':(owner
    ?`当前 ${sharedState.members.length} 位成员（含管理员，上限 100）。添加前对方要先有账号：可展开下面「对方还没有账号？在这里为他注册」。账号只在本机有效，成员需在这台 Mac 上登录。`
    :`当前 ${sharedState.members.length} 位成员。只有空间管理员可以添加或移除成员，你当前是${sharedRoles[space.role]||space.role}，可以查看名单。`);
  for(const member of sharedState.members){
    const row=document.createElement('li'),text=document.createElement('span');text.textContent=member.name+' · '+sharedRoles[member.role];row.append(text);
    if(space.role==='owner'&&member.role!=='owner'){
      const controls=document.createElement('div');controls.className='shared-member-controls';const role=document.createElement('select');role.setAttribute('aria-label','成员 '+member.name+' 的角色');
      for(const value of ['reader','editor']){const option=document.createElement('option');option.value=value;option.textContent=sharedRoles[value];role.append(option);}role.value=member.role;
      const save=document.createElement('button');save.className='text-button';save.textContent='保存角色';save.onclick=()=>sharedRun(async()=>{if(sharedState.space?.id!==space.id)return;await sharedApi('/members/set',{space_id:space.id,name:member.name,role:role.value});await sharedRefresh();sharedMemberNotice(`已把「${member.name}」改为${sharedRoles[role.value]}。`);sharedMessage('成员角色已更新。');});
      const remove=document.createElement('button');remove.className='text-button danger';remove.textContent='移除成员';remove.onclick=()=>sharedRun(async()=>{if(sharedState.space?.id!==space.id||!window.confirm('移除 '+member.name+' 后，该账号将不能再访问这个空间。确认移除？'))return;await sharedApi('/members/remove',{space_id:space.id,account_id:member.id});await sharedRefresh();sharedMemberNotice(`已移除「${member.name}」，他在这个空间的指定可见授权也已撤销。`);sharedMessage('成员已移除，指定可见授权也已撤销。');});
      controls.append(role,save,remove);row.append(controls);
    }
    $('sharedMemberList').append(row);
  }
  if(sharedState.members.length<2){const row=document.createElement('li');row.textContent=owner?'还没有其他成员。让对方先注册空间账号，再在上面的输入框里填写该账号名称添加。':'这个空间目前只有管理员一位成员。';$('sharedMemberList').append(row);}
}
// 添加成员的错误原先只写在页面顶部，容易以为“点了没反应”，这里在按钮旁再报一次。
function sharedMemberNotice(text,error){
  const node=$('sharedMemberStatus');if(!node)return;
  node.textContent=text||'';node.hidden=!text;
  if(node.classList&&node.classList.toggle)node.classList.toggle('material-notice-error',!!error&&!!text);
  if(text&&typeof node.scrollIntoView==='function')node.scrollIntoView({block:'nearest'});
}
// 把一条记忆装进编辑区（不写入），编辑按钮和「改为全体成员可见」共用。
function sharedLoadEditor(item){
  sharedResetReview();sharedState.edit=item;$('sharedEditorTitle').textContent='编辑共同记忆';
  $('sharedMemoryText').value=item.content;$('sharedMemoryStatus').value=item.status||'pending';$('sharedStatusReason').value=item.status_reason||'';
  $('sharedEventDate').value=item.event_date;$('sharedSourceNote').value=item.source_note;$('sharedVisibility').value=item.visibility;$('sharedEditPolicy').value=item.edit_policy;
  sharedRenderAudience();sharedControls();sharedAudienceControls();
  if(typeof showSharedEditor==='function')showSharedEditor();sharedEditorLabels();
}
function sharedRenderMemories(){
  $('sharedMemoryList').replaceChildren();const query=$('sharedSearch').value.trim().toLocaleLowerCase(),space=sharedState.space;
  if(!space){$('sharedCount').textContent='请选择空间';return;}
  const filter=$('sharedStatusFilter').value||'all';
  const items=sharedState.memories.filter(item=>item.content.toLocaleLowerCase().includes(query)&&(filter==='all'||(item.status||'pending')===filter));
  $('sharedCount').textContent=`我可见 ${sharedState.memories.length} 条${query||filter!=='all'?'，匹配 '+items.length+' 条':''}`;
  for(const item of items){
    const row=document.createElement('li'),content=document.createElement('span'),meta=document.createElement('small');content.textContent=item.content;meta.className='memory-meta';
    const viewers=Array.isArray(item.viewers)?item.viewers:[];
    const audience=item.visibility==='all'?'全体成员'
      :viewers.length?'指定成员（'+viewers.length+' 人）'
      :'指定成员：仅作者和管理员（未选择其他成员，其他人问不到）';
    meta.textContent=`归属：${space.name} · 作者：${item.author_name} · 最近修改：${item.editor_name}\n可见：${audience} · 修改：${item.edit_policy==='author'?'作者和管理员':'可见的编辑成员'}\n更新时间：${memoryTime(item.updated)}（北京时间）${item.event_date?'\n事件日期：'+item.event_date:''}${item.source_note?'\n来源说明：'+item.source_note:''}`;
    const badge=document.createElement('strong');badge.className='memory-state memory-state-'+(item.status||'pending');badge.textContent=sharedStatusNames[item.status||'pending'];
    row.append(badge,content,meta);
    if(item.status_reason){const reason=document.createElement('small');reason.className='memory-meta';reason.textContent='状态说明：'+item.status_reason;row.append(reason);}
    const evidence=document.createElement('button');evidence.className='text-button';evidence.textContent=item.has_evidence?'查看原文依据':'未保留原文依据';evidence.disabled=!item.has_evidence;
    evidence.onclick=()=>sharedOpenEvidence(item);row.append(evidence);
    if(typeof sharedReplacementButtons==='function')sharedReplacementButtons(row,item);
    if(item.can_edit){const edit=document.createElement('button');edit.className='text-button';edit.textContent='编辑共同记忆';edit.onclick=()=>{if(sharedState.busy||sharedState.asking||sharedState.space?.id!==space.id)return;sharedLoadEditor(item);sharedMessage('已载入到上方编辑区：修改后点「保存修改（先预览核对）」，勾选确认才会写入。');$('sharedMemoryText').focus();};row.append(edit);}
    // 「指定成员」却没选人：这条记忆实际只有作者和管理员能看到。给作者一个一键改法，避免以为已经共享给全班。
    if(item.can_configure&&item.visibility==='selected'&&!(item.viewers||[]).length){
      const share=document.createElement('button');share.className='text-button';share.textContent='改为全体成员可见';
      share.onclick=()=>{if(sharedState.busy||sharedState.asking||sharedState.space?.id!==space.id)return;sharedLoadEditor(item);$('sharedVisibility').value='all';sharedRenderAudience();sharedControls();sharedEditorNotice('已把可见范围改为「全体成员」，点「保存修改（先预览核对）」并确认后才会生效。');sharedMessage('已载入编辑区：可见范围已改为全体成员，仍需预览并确认才写入。');};
      row.append(share);
    }
    if(item.can_correct_replacement){const correct=document.createElement('button');correct.className='text-button';correct.textContent='纠正上次替代';correct.onclick=()=>sharedRecoveryStart({operation:'undo_replacement',id:item.id,version:item.version},'纠正当前安排的最近一次替代：'+item.content);row.append(correct);}
    if(item.can_delete){const remove=document.createElement('button');remove.className='text-button danger';remove.textContent='移入回收站';remove.onclick=()=>{if(sharedState.space?.id===space.id)sharedRecoveryStart({operation:'trash',id:item.id,version:item.version},'移入回收站：'+item.content);};row.append(remove);}

    if(item.can_history){const history=document.createElement('button');history.className='text-button';history.textContent='查看共同记忆历史';history.onclick=()=>sharedRun(async epoch=>{if(sharedState.space?.id!==space.id)return;const result=await sharedApi('/memories/history',{space_id:space.id,id:item.id});if(epoch!==sharedState.epoch)return;sharedCloseHistory();$('sharedHistoryPanel').hidden=false;$('sharedHistoryInfo').textContent=`共 ${result.total} 次记录，显示最近 ${result.entries.length} 次。历史仅作者和管理员可见。`;
      for(const entry of result.entries){const line=document.createElement('li');line.textContent=`${memoryTime(entry.changed)}（北京时间） · 操作账号：${entry.actor_name}\n${entry.before?'修改前：'+entry.before.content:'新建记忆'}\n记录内容：${entry.after.content}\n可见范围：${entry.after.visibility==='all'?'全体成员':'指定成员'} · 修改权限：${entry.after.edit_policy==='author'?'作者和管理员':'可见的编辑成员'}`;line.textContent+='\n状态：'+(entry.before?(sharedStatusNames[entry.before.status]||'当时未记录')+' → ':'')+(sharedStatusNames[entry.after.status]||'当时未记录')+(entry.after.status_reason?' · '+entry.after.status_reason:'');if(entry.after.recovery)line.textContent+='\n恢复操作：'+(recoveryLabels[entry.after.recovery.operation]||entry.after.recovery.operation)+'；说明：'+entry.after.recovery.reason;
      if(result.can_restore&&['content','event_date','source_note'].some(key=>(entry.after[key]||'')!==(item[key]||''))){const restore=document.createElement('button');restore.className='text-button';restore.textContent='准备恢复此历史版本';restore.onclick=()=>sharedRecoveryStart({operation:'history_restore',id:item.id,version:item.version,history_id:entry.id},'准备恢复 '+memoryTime(entry.changed)+'（北京时间）的历史内容：'+entry.after.content);line.append(restore);}
      $('sharedHistoryList').append(line);}});row.append(history);}
    $('sharedMemoryList').append(row);
  }
  if(!items.length){const row=document.createElement('li');row.textContent=query||filter!=='all'?'当前筛选条件下没有匹配的可见记忆。':'当前没有你可见的共同记忆。';$('sharedMemoryList').append(row);}
}
function sharedPayload(){
  return {space_id:sharedState.space.id,id:sharedState.edit?.id,version:sharedState.edit?.version,content:$('sharedMemoryText').value,status:$('sharedMemoryStatus').value,status_reason:$('sharedStatusReason').value,
    visibility:$('sharedVisibility').value,edit_policy:$('sharedEditPolicy').value,event_date:$('sharedEventDate').value,source_note:$('sharedSourceNote').value,
    viewers:[...$('sharedAudience').querySelectorAll('input:checked')].filter(x=>x.dataset.implicit!=='true').map(x=>x.value)};
}
$('sharedRegister').onclick=()=>sharedRun(async()=>{await sharedApi('/auth/register',{name:$('sharedAccountName').value,password:$('sharedPassword').value});$('sharedPassword').value='';sharedMessage('空间账号已注册，请输入口令登录。');});
$('sharedLogin').onclick=()=>sharedRun(async epoch=>{const result=await sharedApi('/auth/login',{name:$('sharedAccountName').value,password:$('sharedPassword').value});if(epoch!==sharedState.epoch)return;sharedState.token=result.token;sharedState.account=result.account;$('sharedPassword').value='';await sharedRefresh();sharedMessage(sharedState.space?'已登录，默认进入「'+sharedState.space.name+'」。可切换其他有权限的空间。':'已登录，可以使用账号个人记忆；也可以在“空间与成员”创建团队空间。');if(typeof workspaceAfterLogin==='function')await workspaceAfterLogin();});
$('sharedLogout').onclick=async()=>{const token=sharedLock();if(token)try{await fetch('/api/shared/auth/logout',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:'{}'});}catch{sharedMessage('页面已锁定；网络异常时请注意其他已登录会话可能仍有效。');}};
$('sharedChangePassword').onclick=()=>sharedRun(async()=>{await sharedApi('/auth/password',{old_password:$('sharedOldPassword').value,new_password:$('sharedNewPassword').value});sharedLock('口令已修改，所有空间登录会话已退出。请重新登录。');});
$('sharedRefresh').onclick=()=>sharedRun(sharedRefresh);
$('sharedSpaceSelect').onchange=()=>{if(sharedState.busy||sharedState.asking)return;const id=$('sharedSpaceSelect').value;sharedClearSpace();sharedState.space=sharedState.spaces.find(x=>x.id===id)||null;$('sharedSpaceSelect').value=id;sharedRun(sharedLoadCurrent);};
$('sharedCreateSpace').onclick=()=>sharedRun(async()=>{const result=await sharedApi('/spaces/create',{name:$('sharedSpaceName').value,kind:$('sharedSpaceKind').value});$('sharedSpaceName').value='';await sharedRefresh();sharedClearAnswer();sharedCloseHistory();$('sharedSearch').value='';sharedState.space=sharedState.spaces.find(x=>x.id===result.id)||null;sharedRenderSpaces();sharedResetEditor();await sharedLoadCurrent();sharedMessage('空间已创建，你是管理员。成员注册账号后，可在这里添加。');});
$('sharedDeleteSpace').onclick=()=>sharedRun(async()=>{const space=sharedState.space;if(!space||!window.confirm('删除「'+space.name+'」及其全部成员关系、共同记忆、修改历史、回收站及恢复记录？此操作无法通过回收站恢复。个人记忆不受影响。'))return;await sharedApi('/spaces/delete',{space_id:space.id,version:space.version,confirmed:true});sharedClearSpace();await sharedRefresh();sharedMessage('空间及共同记忆已删除。');});
$('sharedSetMember').onclick=()=>sharedRun(async()=>{
  const space=sharedState.space;if(!space)return;
  if(space.role!=='owner'){sharedMemberNotice('只有空间管理员可以添加成员。',true);return;}
  const name=$('sharedMemberName').value.trim(),role=$('sharedMemberRole').value;
  if(!name){sharedMemberNotice('请先填写成员的空间账号名称，要和对方注册时用的名称完全一致。',true);$('sharedMemberName').focus();return;}
  sharedMemberNotice('正在添加「'+name+'」…');
  try{
    await sharedApi('/members/set',{space_id:space.id,name,role});
  }catch(error){
    const missing=/不存在/.test(error.message||'');
    sharedMemberNotice(missing?`没有找到空间账号「${name}」。请先展开下面的「对方还没有账号？在这里为他注册」注册这个名称，再回来添加。`:(error.message||'添加成员失败，请重试。'),true);
    return;
  }
  $('sharedMemberName').value='';await sharedRefresh();
  sharedMemberNotice(`已把「${name}」设为${sharedRoles[role]}，名单现在 ${sharedState.members.length} 位成员。若要让他看到「指定成员」的记忆，还要在那条记忆的「谁可以看到」里勾选他。`);
  sharedMessage('成员角色已保存。');
});
// 登录状态下也能为对方注册账号：注册不需要当前登录，也不会切换当前会话。
$('sharedRegisterMember').onclick=()=>sharedRun(async()=>{
  const name=$('sharedNewMemberName').value.trim(),password=$('sharedNewMemberPassword').value;
  const node=$('sharedRegisterMemberStatus');
  const tell=(text,error)=>{node.textContent=text;if(node.classList&&node.classList.toggle)node.classList.toggle('material-notice-error',!!error);if(typeof node.scrollIntoView==='function')node.scrollIntoView({block:'nearest'});};
  if(!name){tell('请先填写新成员的空间账号名称。',true);$('sharedNewMemberName').focus();return;}
  if(password.length<10||password.length>128){tell('口令请使用 10–128 个字符，并让对方自己输入。',true);$('sharedNewMemberPassword').focus();return;}
  if(sharedState.space?.role!=='owner'){tell('只有空间管理员可以添加成员。',true);return;}
  try{
    await sharedApi('/auth/register',{name,password});
  }catch(error){
    tell(/已被使用/.test(error.message||'')?`账号名称「${name}」已被使用，请换一个名称，或直接在下面按该名称添加成员。`:(error.message||'注册失败，请重试。'),true);
    return;
  }
  $('sharedNewMemberName').value='';$('sharedNewMemberPassword').value='';
  $('sharedMemberName').value=name;$('sharedMemberRole').focus();
  tell(`已注册账号「${name}」，当前登录没有被切换。请确认角色后点「添加或更新成员角色」把 TA 加入本空间。`);
  sharedMessage(`已注册空间账号「${name}」，继续添加为成员即可。`);
});
// 发请求前先按服务端规则自检，避免点保存后只得到一条看不见的错误。
function sharedEditorProblem(){
  const content=$('sharedMemoryText').value.trim(),status=$('sharedMemoryStatus').value,reason=$('sharedStatusReason').value.trim();
  if(!content)return {message:'请先填写共同记忆内容，再保存。',focus:'sharedMemoryText'};
  const original=sharedState.edit;
  const changed=!!original&&(content!==original.content.trim()||$('sharedEventDate').value!==(original.event_date||''));
  const inherited=original&&status===original.status?(original.status_reason||'').trim():'';
  if(!changed&&status!=='pending'&&!reason&&!inherited)
    return {message:`状态选择「${sharedStatusNames[status]}」时需要填写状态说明（核实依据），否则无法保存。`,focus:'sharedStatusReason'};
  return null;
}
$('sharedPreview').onclick=()=>{
  const button=$('sharedPreview');if(button.disabled)return;
  const problem=sharedEditorProblem();
  if(problem){sharedMessage(problem.message);sharedEditorNotice(problem.message);$(problem.focus).focus();return;}
  button.textContent='正在核对…';sharedEditorNotice('');
  return sharedRun(async epoch=>{if(!sharedState.space)return;sharedResetReview();const plan=await sharedApi('/memories/preview',sharedPayload());if(epoch!==sharedState.epoch)return;sharedState.plan=plan;$('sharedReviewPanel').hidden=false;$('sharedConflictLabel').hidden=!plan.conflicts.length;
  const onlyAuthor=plan.after.visibility==='selected'&&!(plan.after.viewers||[]).length;
  $('sharedReviewSummary').textContent=`归属：${plan.kind==='family'?'家庭':'团队'}「${plan.space_name}」；原作者：${plan.author_name}；本次操作：${plan.actor_name}。可见：${plan.after.visibility==='all'?'全体成员（含以后加入的成员），当前为：':''}${plan.audience.join('、')}${onlyAuthor?'（未选择其他成员：保存后只有作者和管理员能看到，其他成员在记忆库和问答里都读不到）':''}。修改权限：${plan.after.edit_policy==='author'?'作者和管理员':'可见的编辑成员'}。`;
  for(const text of [...(plan.before?['修改前：'+plan.before.content]:[]),'准备保存：'+plan.after.content,'记忆状态：'+sharedStatusNames[plan.after.status]+'；说明：'+(plan.after.status_reason||'未填写'),...(plan.before?.has_evidence?['已保留的原文摘录随这条记忆的可见范围开放，完整原文仍仅作者和管理员可见。']:[]),...plan.conflicts.map(x=>'可能冲突：'+x.content)]){const line=document.createElement('p');line.textContent=text;$('sharedReviewContent').append(line);}
  const next=document.createElement('p');next.textContent='下一步：勾选下方确认项，再点「'+$('sharedConfirm').textContent+'」才会写入。';$('sharedReviewContent').append(next);
  // 核对区在长表单下方，滚动过去让勾选框和确认按钮直接可见。
  if(typeof $('sharedReviewPanel').scrollIntoView==='function')$('sharedReviewPanel').scrollIntoView({block:'nearest'});
  sharedMessage('尚未保存。请勾选确认项后再点「'+$('sharedConfirm').textContent+'」；预览 10 分钟内有效。');
  }).finally(()=>{button.textContent=sharedState.edit?'保存修改（先预览核对）':'保存新记忆（先预览核对）';});
};
$('sharedConfirm').onclick=()=>sharedRun(async()=>{const plan=sharedState.plan;if(!plan||!$('sharedPublishConsent').checked||(plan.conflicts.length&&!$('sharedConflictConsent').checked))return;const editing=!!sharedState.edit;await sharedApi('/memories/confirm',{space_id:sharedState.space.id,token:plan.token,confirmed:true,acknowledge_conflicts:$('sharedConflictConsent').checked});sharedResetEditor();await sharedRefresh();sharedMessage(editing?'共同记忆修改已保存。':'共同记忆已发布，已按可见范围和修改权限保存。');});
$('sharedCancelReview').onclick=()=>{if(sharedState.busy)return;sharedResetReview();sharedControls();sharedMessage('已取消，未写入；已填写的内容仍保留在编辑区。');};
$('sharedCancelEdit').onclick=()=>{if(sharedState.busy)return;sharedResetEditor();sharedControls();sharedAudienceControls();};
for(const id of ['sharedMemoryText','sharedMemoryStatus','sharedStatusReason','sharedEventDate','sharedSourceNote','sharedEditPolicy'])$(id).oninput=()=>{sharedResetReview();sharedControls();sharedAudienceControls();};
$('sharedVisibility').onchange=()=>{sharedResetReview();$('sharedAudience').hidden=$('sharedVisibility').value!=='selected';sharedRenderAudienceHint();sharedControls();sharedAudienceControls();};
$('sharedPublishConsent').onchange=sharedControls;$('sharedConflictConsent').onchange=sharedControls;
$('sharedSearch').oninput=sharedRenderMemories;$('sharedStatusFilter').onchange=sharedRenderMemories;$('sharedCloseHistory').onclick=sharedCloseHistory;
function sharedResetTopic(){
  const identity=sharedState.conversation,token=sharedState.token;
  sharedState.conversation=null;$('sharedTopic').textContent='';$('sharedChoices').replaceChildren();$('sharedChoices').hidden=true;
  if(identity&&token)fetch('/api/shared/ask/reset',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({conversation_id:identity})}).catch(()=>{});
}
$('sharedNewTopic').onclick=()=>{sharedClearAnswer();$('sharedQuestion').value='';$('sharedTopic').textContent='已开始新话题，请说出活动名称或日期。';};
$('sharedIncludeOutdated').onchange=()=>sharedClearAnswer();
$('sharedAgentMode').onchange=()=>sharedClearAnswer();
function sharedStopAsk(show=true){
  if(show)sharedResetTopic();
  sharedState.askVersion++;if(sharedState.askAbort)sharedState.askAbort.abort();sharedState.askAbort=null;
  if(sharedState.turn)fetch('/api/turn/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({turn_id:sharedState.turn})}).catch(()=>{});
  sharedState.turn=null;sharedState.asking=false;if(show)$('sharedAnswer').textContent='已停止共同记忆问答。';sharedControls();
}
$('sharedStopAsk').onclick=()=>sharedStopAsk();
async function sharedAskQuestion(choiceId=null,agentSelection=null){
  if(!sharedState.space||sharedState.busy||sharedState.asking)return;
  const query=$('sharedQuestion').value.trim()||((choiceId||agentSelection)?'选择活动':'');if(!query){sharedMessage('请先输入共同记忆问题。');return;}
  sharedClearAnswer(true);$('sharedChoices').replaceChildren();$('sharedChoices').hidden=true;const epoch=sharedState.epoch,version=sharedState.askVersion,id=sharedState.space.id,auth=sharedState.token;
  sharedState.asking=true;sharedState.turn=crypto.randomUUID();sharedState.askAbort=new AbortController();$('sharedAnswer').textContent='正在根据你可见的共同记忆回答…';sharedControls();
  try{const result=await sharedApi($('sharedAgentMode').checked?'/agent/memory/ask':'/ask',{space_id:id,query,conversation_mode:true,conversation_id:sharedState.conversation,choice_id:choiceId,turn_id:sharedState.turn,include_outdated:$('sharedIncludeOutdated').checked,...(agentSelection||{})},sharedState.askAbort.signal);if(epoch!==sharedState.epoch||version!==sharedState.askVersion){if(typeof sharedDiscardAgentResult==='function')sharedDiscardAgentResult({...result,space_id:id},auth);return;}
    if(typeof diagnosticOverview!=='undefined')diagnosticOverview.noteShared(result,query);
    sharedState.conversation=result.conversation?.id||null;
    $('sharedTopic').textContent=result.conversation?.topic?'当前话题：'+result.conversation.topic+'。可以继续问，也可以开始新话题。':result.conversation?.needs_clarification?'请先选择活动，再回答刚才的问题。':'';
    for(const choice of result.conversation?.choices||[]){const button=document.createElement('button');button.className='secondary-button';button.textContent=choice.label;button.onclick=()=>sharedAskQuestion(choice.id);$('sharedChoices').append(button);}
    $('sharedChoices').hidden=!$('sharedChoices').children.length;
    if(typeof sharedFeedbackOffer==='function')sharedFeedbackOffer(result);
    $('sharedAnswer').textContent=result.answer;$('sharedRetrievalInfo').textContent=retrievalDiagnosticText(result.retrieval);sharedRenderAnswerSources(result);sharedRenderAgent(result.agent);if(typeof sharedRenderAgentDraft==='function')sharedRenderAgentDraft(result);}
  catch(error){if(epoch===sharedState.epoch&&version===sharedState.askVersion){if(typeof diagnosticOverview!=='undefined')diagnosticOverview.noteShared({diagnostic:error.diagnostic},query);sharedResetTopic();$('sharedAnswer').textContent=error.name==='AbortError'?'已停止。':error.message;}}
  finally{if(epoch===sharedState.epoch&&version===sharedState.askVersion){sharedState.asking=false;sharedState.askAbort=null;sharedState.turn=null;sharedControls();}}
}
$('sharedAsk').onclick=()=>sharedAskQuestion();
// Refresh permissions without persisting shared content; writes also recheck on the server.
setInterval(()=>{if(sharedState.token&&!sharedState.busy&&!sharedState.asking)sharedRun(sharedRefresh);},30000);
sharedEditorLabels();sharedControls();

function sharedRenderAgent(agent){
  $('sharedAgentPanel').hidden=!agent;$('sharedAgentSteps').replaceChildren();$('sharedAgentStatus').textContent='';
  if(!agent)return;
  const states={done:'本轮查询已完成。',needs_confirmation:'预览已就绪，等待你核对后确认；尚未保存。',needs_selection:'请先选中要修改的记忆。',needs_details:'需要补充具体内容或目标。',needs_clarification:'请先选择活动，再继续查询。',failed:'工具未完成，可重新提问或关闭自动工具后重试。',timeout:'本轮已超时停止，请缩小问题范围。'};
  $('sharedAgentStatus').textContent=states[agent.status]||'工具已停止。';
  const labels={search_memory:'查询记忆',read_material:'读取资料',get_current_time:'获取当前时间',invalid_request:'工具请求校验',save_memory_preview:'生成新增预览',replace_memory_preview:'替代安排预览',update_memory_preview:'生成修改预览'};
  const statuses={done:'完成',failed:'失败',blocked:'已拦截',cancelled:'已停止',running:'进行中'};
  for(const step of agent.steps||[]){const row=document.createElement('li');row.textContent=(labels[step.name]||'工具请求')+' · '+(statuses[step.status]||'未完成')+(Number.isInteger(step.count)?' · '+step.count+' 条资料':'')+(Number.isFinite(step.elapsed_ms)?' · '+step.elapsed_ms+' 毫秒':'');$('sharedAgentSteps').append(row);}
}

// 对话页缺少哪一步，就把用户直接送到那一步（登录框或空间下拉）。
