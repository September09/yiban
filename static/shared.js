// Space identities are password-authenticated and independent of voice/personal profiles.
// Tokens, shared drafts and answers stay in this page's memory, never localStorage.
const sharedState={token:null,account:null,spaces:[],space:null,members:[],memories:[],edit:null,plan:null,
  epoch:0,loadVersion:0,busy:false,asking:false,askVersion:0,askAbort:null,turn:null};
const sharedStatusNames={pending:'待核实',valid:'有效',outdated:'过时'};
const sharedRoles={owner:'管理员',editor:'编辑成员',reader:'只读成员'};
function sharedMessage(text){$('sharedStatus').textContent=text;}
function sharedResetReview(){sharedState.plan=null;$('sharedReviewPanel').hidden=true;$('sharedPublishConsent').checked=false;$('sharedConflictConsent').checked=false;$('sharedReviewSummary').textContent='';$('sharedReviewContent').replaceChildren();}
function sharedCloseHistory(){$('sharedHistoryPanel').hidden=true;$('sharedHistoryInfo').textContent='';$('sharedHistoryList').replaceChildren();}
function sharedClearAnswer(){sharedStopAsk(false);$('sharedAnswer').textContent='';$('sharedSources').replaceChildren();$('sharedSourcesPanel').hidden=true;}
function sharedResetEditor(){
  sharedState.edit=null;sharedResetReview();$('sharedMemoryStatus').value='pending';$('sharedStatusReason').value='';$('sharedMemoryText').value='';$('sharedEventDate').value='';$('sharedSourceNote').value='';
  $('sharedVisibility').value='selected';$('sharedEditPolicy').value='author';$('sharedEditorTitle').textContent='发布共同记忆';sharedRenderAudience();
}
function sharedClearSpace(){
  if(typeof sharedCloseEvidence==='function')sharedCloseEvidence();
  $('sharedStatusFilter').value='all';$('sharedIncludeOutdated').checked=false;
  if(typeof materialReset==='function')materialReset();
  if(typeof sharedVoiceStop==='function')sharedVoiceStop();
  sharedState.epoch++;sharedState.loadVersion++;sharedState.busy=false;sharedState.space=null;sharedState.members=[];sharedState.memories=[];
  sharedClearAnswer();sharedCloseHistory();sharedResetEditor();$('sharedSpaceSelect').value='';$('sharedSearch').value='';
  $('sharedMemberList').replaceChildren();$('sharedMemoryList').replaceChildren();$('sharedCount').textContent='请选择空间';$('sharedSpaceInfo').textContent='';sharedControls();
}
function sharedLock(message='已退出空间账号，页面中的共同记忆已清空。'){
  const oldToken=sharedState.token;
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
  if(!response.ok){
    if(response.status===401&&token===sharedState.token&&epoch===sharedState.epoch)sharedLock('登录失效或账号口令不正确，请重新登录。');
    if(response.status===403&&token===sharedState.token&&epoch===sharedState.epoch)sharedClearSpace();
    const error=new Error(result.error||'操作失败，请重试。');error.status=response.status;throw error;
  }
  return result;
}
async function sharedRun(work){
  if(sharedState.busy||sharedState.asking)return;
  const epoch=sharedState.epoch;sharedState.busy=true;sharedControls();
  try{await work(epoch);}catch(error){sharedMessage(error.message);}
  finally{if(epoch===sharedState.epoch)sharedState.busy=false;sharedControls();}
}
function sharedControls(){
  const logged=!!sharedState.token,space=sharedState.space,locked=sharedState.busy||sharedState.asking||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());
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
  sharedAudienceControls();
  $('sharedCancelEdit').disabled=locked;$('sharedCancelReview').disabled=locked;
  $('sharedSearch').disabled=!space;$('sharedStatusFilter').disabled=!space;$('sharedIncludeOutdated').disabled=!space||locked;
  $('sharedPublishConsent').disabled=locked||!sharedState.plan;
  $('sharedConflictConsent').disabled=locked||!sharedState.plan;
  $('sharedConfirm').disabled=locked||!sharedState.plan||!$('sharedPublishConsent').checked||
    (sharedState.plan.conflicts.length>0&&!$('sharedConflictConsent').checked);
  $('sharedAsk').disabled=!space||locked;$('sharedQuestion').disabled=!space||locked;$('sharedStopAsk').disabled=!sharedState.asking;
  $('sharedOwnership').textContent=space?`归属：${space.kind==='family'?'家庭':'团队'}「${space.name}」；发布者：${sharedState.edit?.author_name||sharedState.account.name}。`:'请选择空间。';
  if(typeof sharedVoiceControls==='function')sharedVoiceControls();
  if(typeof materialControls==='function')materialControls();
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
    box.onchange=()=>{sharedResetReview();sharedControls();};label.append(box,text);$('sharedAudience').append(label);
  }
  sharedAudienceControls();
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
  sharedRenderSpaces();if(sharedState.space)await sharedLoadCurrent();else sharedControls();
}
async function sharedLoadCurrent(){
  if(!sharedState.space)return;
  const epoch=sharedState.epoch,id=sharedState.space.id,load=++sharedState.loadVersion;
  const [result,members]=await Promise.all([sharedApi('/memories/list',{space_id:id}),sharedApi('/members/list',{space_id:id})]);
  if(epoch!==sharedState.epoch||sharedState.space?.id!==id||load!==sharedState.loadVersion)return;
  if(sharedState.space.version!==result.space.version){if(typeof sharedCloseEvidence==='function')sharedCloseEvidence();sharedResetReview();sharedCloseHistory();sharedClearAnswer();}
  const materialChanged=sharedState.space.version!==result.space.version;
  sharedState.space=result.space;sharedState.members=members.members;sharedState.memories=result.memories;
  if(typeof materialPermissionsChanged==='function'&&materialChanged)materialPermissionsChanged();
  else if(typeof materialAudience==='function')materialAudience();
  if(sharedState.edit&&!result.memories.some(x=>x.id===sharedState.edit.id&&x.can_edit))sharedResetEditor();
  $('sharedSpaceInfo').textContent=`${result.space.kind==='family'?'家庭':'团队'}空间 · 我的角色：${sharedRoles[result.space.role]}。管理员始终可管理空间内全部记忆。`;
  sharedRenderMembers();sharedRenderMemories();if(!sharedState.edit)sharedRenderAudience();sharedControls();sharedAudienceControls();
}
function sharedRenderMembers(){
  $('sharedMemberList').replaceChildren();const space=sharedState.space;
  for(const member of sharedState.members){
    const row=document.createElement('li'),text=document.createElement('span');text.textContent=member.name+' · '+sharedRoles[member.role];row.append(text);
    if(space.role==='owner'&&member.role!=='owner'){
      const controls=document.createElement('div');controls.className='shared-member-controls';const role=document.createElement('select');role.setAttribute('aria-label','成员 '+member.name+' 的角色');
      for(const value of ['reader','editor']){const option=document.createElement('option');option.value=value;option.textContent=sharedRoles[value];role.append(option);}role.value=member.role;
      const save=document.createElement('button');save.className='text-button';save.textContent='保存角色';save.onclick=()=>sharedRun(async()=>{if(sharedState.space?.id!==space.id)return;await sharedApi('/members/set',{space_id:space.id,name:member.name,role:role.value});await sharedRefresh();sharedMessage('成员角色已更新。');});
      const remove=document.createElement('button');remove.className='text-button danger';remove.textContent='移除成员';remove.onclick=()=>sharedRun(async()=>{if(sharedState.space?.id!==space.id||!window.confirm('移除 '+member.name+' 后，该账号将不能再访问这个空间。确认移除？'))return;await sharedApi('/members/remove',{space_id:space.id,account_id:member.id});await sharedRefresh();sharedMessage('成员已移除，指定可见授权也已撤销。');});
      controls.append(role,save,remove);row.append(controls);
    }
    $('sharedMemberList').append(row);
  }
}
function sharedRenderMemories(){
  $('sharedMemoryList').replaceChildren();const query=$('sharedSearch').value.trim().toLocaleLowerCase(),space=sharedState.space;
  if(!space){$('sharedCount').textContent='请选择空间';return;}
  const filter=$('sharedStatusFilter').value||'all';
  const items=sharedState.memories.filter(item=>item.content.toLocaleLowerCase().includes(query)&&(filter==='all'||(item.status||'pending')===filter));
  $('sharedCount').textContent=`我可见 ${sharedState.memories.length} 条${query?'，匹配 '+items.length+' 条':''}`;
  for(const item of items){
    const row=document.createElement('li'),content=document.createElement('span'),meta=document.createElement('small');content.textContent=item.content;meta.className='memory-meta';
    const audience=item.visibility==='all'?'全体成员':'指定成员（作者和管理员始终可见）';
    meta.textContent=`归属：${space.name} · 作者：${item.author_name} · 最近修改：${item.editor_name}\n可见：${audience} · 修改：${item.edit_policy==='author'?'作者和管理员':'可见的编辑成员'}\n更新时间：${memoryTime(item.updated)}（北京时间）${item.event_date?'\n事件日期：'+item.event_date:''}${item.source_note?'\n来源说明：'+item.source_note:''}`;
    const badge=document.createElement('strong');badge.className='memory-state memory-state-'+(item.status||'pending');badge.textContent=sharedStatusNames[item.status||'pending'];
    row.append(badge,content,meta);
    if(item.status_reason){const reason=document.createElement('small');reason.className='memory-meta';reason.textContent='状态说明：'+item.status_reason;row.append(reason);}
    const evidence=document.createElement('button');evidence.className='text-button';evidence.textContent=item.has_evidence?'查看原文依据':'未保留原文依据';evidence.disabled=!item.has_evidence;
    evidence.onclick=()=>sharedOpenEvidence(item);row.append(evidence);
    if(item.can_edit){const edit=document.createElement('button');edit.className='text-button';edit.textContent='编辑共同记忆';edit.onclick=()=>{if(sharedState.busy||sharedState.asking||sharedState.space?.id!==space.id)return;sharedResetReview();sharedState.edit=item;$('sharedEditorTitle').textContent='编辑共同记忆';$('sharedMemoryText').value=item.content;$('sharedMemoryStatus').value=item.status||'pending';$('sharedStatusReason').value=item.status_reason||'';$('sharedEventDate').value=item.event_date;$('sharedSourceNote').value=item.source_note;$('sharedVisibility').value=item.visibility;$('sharedEditPolicy').value=item.edit_policy;sharedRenderAudience();sharedControls();sharedAudienceControls();$('sharedMemoryText').focus();};row.append(edit);}
    if(item.can_delete){const remove=document.createElement('button');remove.className='text-button danger';remove.textContent='删除共同记忆';remove.onclick=()=>sharedRun(async()=>{if(sharedState.space?.id!==space.id||!window.confirm('删除这条共同记忆及其全部历史？'))return;await sharedApi('/memories/delete',{space_id:space.id,id:item.id,version:item.version,confirmed:true});sharedResetEditor();await sharedRefresh();sharedMessage('共同记忆及历史已删除。');});row.append(remove);}
    if(item.can_history){const history=document.createElement('button');history.className='text-button';history.textContent='查看共同记忆历史';history.onclick=()=>sharedRun(async epoch=>{if(sharedState.space?.id!==space.id)return;const result=await sharedApi('/memories/history',{space_id:space.id,id:item.id});if(epoch!==sharedState.epoch)return;sharedCloseHistory();$('sharedHistoryPanel').hidden=false;$('sharedHistoryInfo').textContent=`共 ${result.total} 次记录，显示最近 ${result.entries.length} 次。历史仅作者和管理员可见。`;
      for(const entry of result.entries){const line=document.createElement('li');line.textContent=`${memoryTime(entry.changed)}（北京时间） · 操作账号：${entry.actor_name}\n${entry.before?'修改前：'+entry.before.content:'新建记忆'}\n记录内容：${entry.after.content}\n可见范围：${entry.after.visibility==='all'?'全体成员':'指定成员'} · 修改权限：${entry.after.edit_policy==='author'?'作者和管理员':'可见的编辑成员'}`;line.textContent+='\n状态：'+(entry.before?(sharedStatusNames[entry.before.status]||'当时未记录')+' → ':'')+(sharedStatusNames[entry.after.status]||'当时未记录')+(entry.after.status_reason?' · '+entry.after.status_reason:'');$('sharedHistoryList').append(line);}});row.append(history);}
    $('sharedMemoryList').append(row);
  }
  if(!items.length){const row=document.createElement('li');row.textContent=query?'没有匹配的可见记忆。':'当前没有你可见的共同记忆。';$('sharedMemoryList').append(row);}
}
function sharedPayload(){
  return {space_id:sharedState.space.id,id:sharedState.edit?.id,version:sharedState.edit?.version,content:$('sharedMemoryText').value,status:$('sharedMemoryStatus').value,status_reason:$('sharedStatusReason').value,
    visibility:$('sharedVisibility').value,edit_policy:$('sharedEditPolicy').value,event_date:$('sharedEventDate').value,source_note:$('sharedSourceNote').value,
    viewers:[...$('sharedAudience').querySelectorAll('input:checked')].filter(x=>x.dataset.implicit!=='true').map(x=>x.value)};
}
$('sharedRegister').onclick=()=>sharedRun(async()=>{await sharedApi('/auth/register',{name:$('sharedAccountName').value,password:$('sharedPassword').value});$('sharedPassword').value='';sharedMessage('空间账号已注册，请输入口令登录。');});
$('sharedLogin').onclick=()=>sharedRun(async epoch=>{const result=await sharedApi('/auth/login',{name:$('sharedAccountName').value,password:$('sharedPassword').value});if(epoch!==sharedState.epoch)return;sharedState.token=result.token;sharedState.account=result.account;$('sharedPassword').value='';await sharedRefresh();sharedMessage('已登录空间账号。请选择空间或创建家庭／团队空间。');});
$('sharedLogout').onclick=async()=>{const token=sharedLock();if(token)try{await fetch('/api/shared/auth/logout',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:'{}'});}catch{sharedMessage('页面已锁定；网络异常时请注意其他已登录会话可能仍有效。');}};
$('sharedChangePassword').onclick=()=>sharedRun(async()=>{await sharedApi('/auth/password',{old_password:$('sharedOldPassword').value,new_password:$('sharedNewPassword').value});sharedLock('口令已修改，所有空间登录会话已退出。请重新登录。');});
$('sharedRefresh').onclick=()=>sharedRun(sharedRefresh);
$('sharedSpaceSelect').onchange=()=>{if(sharedState.busy||sharedState.asking)return;const id=$('sharedSpaceSelect').value;sharedClearSpace();sharedState.space=sharedState.spaces.find(x=>x.id===id)||null;$('sharedSpaceSelect').value=id;sharedRun(sharedLoadCurrent);};
$('sharedCreateSpace').onclick=()=>sharedRun(async()=>{const result=await sharedApi('/spaces/create',{name:$('sharedSpaceName').value,kind:$('sharedSpaceKind').value});$('sharedSpaceName').value='';await sharedRefresh();sharedClearAnswer();sharedCloseHistory();$('sharedSearch').value='';sharedState.space=sharedState.spaces.find(x=>x.id===result.id)||null;sharedRenderSpaces();sharedResetEditor();await sharedLoadCurrent();sharedMessage('空间已创建，你是管理员。成员注册账号后，可在这里添加。');});
$('sharedDeleteSpace').onclick=()=>sharedRun(async()=>{const space=sharedState.space;if(!space||!window.confirm('删除「'+space.name+'」及其全部成员关系、共同记忆和修改历史？个人记忆不受影响。'))return;await sharedApi('/spaces/delete',{space_id:space.id,version:space.version,confirmed:true});sharedClearSpace();await sharedRefresh();sharedMessage('空间及共同记忆已删除。');});
$('sharedSetMember').onclick=()=>sharedRun(async()=>{if(!sharedState.space)return;await sharedApi('/members/set',{space_id:sharedState.space.id,name:$('sharedMemberName').value,role:$('sharedMemberRole').value});$('sharedMemberName').value='';await sharedRefresh();sharedMessage('成员角色已保存。');});
$('sharedPreview').onclick=()=>sharedRun(async epoch=>{if(!sharedState.space)return;sharedResetReview();const plan=await sharedApi('/memories/preview',sharedPayload());if(epoch!==sharedState.epoch)return;sharedState.plan=plan;$('sharedReviewPanel').hidden=false;$('sharedConflictLabel').hidden=!plan.conflicts.length;
  $('sharedReviewSummary').textContent=`归属：${plan.kind==='family'?'家庭':'团队'}「${plan.space_name}」；原作者：${plan.author_name}；本次操作：${plan.actor_name}。可见：${plan.after.visibility==='all'?'全体成员（含以后加入的成员），当前为：':''}${plan.audience.join('、')}。修改权限：${plan.after.edit_policy==='author'?'作者和管理员':'可见的编辑成员'}。`;
  for(const text of [...(plan.before?['修改前：'+plan.before.content]:[]),'准备保存：'+plan.after.content,'记忆状态：'+sharedStatusNames[plan.after.status]+'；说明：'+(plan.after.status_reason||'未填写'),...(plan.before?.has_evidence?['已保留的原文摘录随这条记忆的可见范围开放，完整原文仍仅作者和管理员可见。']:[]),...plan.conflicts.map(x=>'可能冲突：'+x.content)]){const line=document.createElement('p');line.textContent=text;$('sharedReviewContent').append(line);}sharedMessage('尚未发布。请核对内容与可见范围，预览 10 分钟内有效。');});
$('sharedConfirm').onclick=()=>sharedRun(async()=>{const plan=sharedState.plan;if(!plan||!$('sharedPublishConsent').checked||(plan.conflicts.length&&!$('sharedConflictConsent').checked))return;await sharedApi('/memories/confirm',{space_id:sharedState.space.id,token:plan.token,confirmed:true,acknowledge_conflicts:$('sharedConflictConsent').checked});sharedResetEditor();await sharedRefresh();sharedMessage('共同记忆已发布，已按可见范围和修改权限保存。');});
$('sharedCancelReview').onclick=()=>{if(sharedState.busy)return;sharedResetReview();sharedControls();sharedMessage('已取消发布，未写入。');};
$('sharedCancelEdit').onclick=()=>{if(sharedState.busy)return;sharedResetEditor();sharedControls();sharedAudienceControls();};
for(const id of ['sharedMemoryText','sharedMemoryStatus','sharedStatusReason','sharedEventDate','sharedSourceNote','sharedEditPolicy'])$(id).oninput=()=>{sharedResetReview();sharedControls();sharedAudienceControls();};
$('sharedVisibility').onchange=()=>{sharedResetReview();$('sharedAudience').hidden=$('sharedVisibility').value!=='selected';sharedControls();sharedAudienceControls();};
$('sharedPublishConsent').onchange=sharedControls;$('sharedConflictConsent').onchange=sharedControls;
$('sharedSearch').oninput=sharedRenderMemories;$('sharedStatusFilter').onchange=sharedRenderMemories;$('sharedCloseHistory').onclick=sharedCloseHistory;
function sharedStopAsk(show=true){
  sharedState.askVersion++;if(sharedState.askAbort)sharedState.askAbort.abort();sharedState.askAbort=null;
  if(sharedState.turn)fetch('/api/turn/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({turn_id:sharedState.turn})}).catch(()=>{});
  sharedState.turn=null;sharedState.asking=false;if(show)$('sharedAnswer').textContent='已停止共同记忆问答。';sharedControls();
}
$('sharedStopAsk').onclick=()=>sharedStopAsk();
$('sharedAsk').onclick=async()=>{
  if(!sharedState.space||sharedState.busy||sharedState.asking)return;
  const query=$('sharedQuestion').value.trim();if(!query){sharedMessage('请先输入共同记忆问题。');return;}
  sharedClearAnswer();const epoch=sharedState.epoch,version=sharedState.askVersion,id=sharedState.space.id;
  sharedState.asking=true;sharedState.turn=crypto.randomUUID();sharedState.askAbort=new AbortController();$('sharedAnswer').textContent='正在根据你可见的共同记忆回答…';sharedControls();
  try{const result=await sharedApi('/ask',{space_id:id,query,turn_id:sharedState.turn,include_outdated:$('sharedIncludeOutdated').checked},sharedState.askAbort.signal);if(epoch!==sharedState.epoch||version!==sharedState.askVersion)return;$('sharedAnswer').textContent=result.answer;$('sharedSourcesPanel').hidden=!result.sources.length;for(const source of result.sources){const row=document.createElement('li');row.textContent=`${source.content}\n提供者：${source.author_name}${source.event_date?' · 事件日期：'+source.event_date:''}`;row.textContent+='\n状态：'+sharedStatusNames[source.status||'pending']+(source.status_reason?' · '+source.status_reason:'');$('sharedSources').append(row);}}
  catch(error){if(epoch===sharedState.epoch&&version===sharedState.askVersion)$('sharedAnswer').textContent=error.name==='AbortError'?'已停止。':error.message;}
  finally{if(epoch===sharedState.epoch&&version===sharedState.askVersion){sharedState.asking=false;sharedState.askAbort=null;sharedState.turn=null;sharedControls();}}
};
// Refresh permissions without persisting shared content; writes also recheck on the server.
setInterval(()=>{if(sharedState.token&&!sharedState.busy&&!sharedState.asking)sharedRun(sharedRefresh);},30000);
sharedControls();
