// One reviewed recovery action at a time; drafts and confirmation tokens remain in RAM.
const recoveryLabels={trash:'移入回收站',trash_restore:'从回收站恢复',history_restore:'恢复历史内容',undo_replacement:'纠正最近一次替代'};
const recoveryState={request:null,plan:null,version:0,running:false};
function recoveryMessage(text){$('sharedRecoveryStatus').textContent=text;}
function recoveryDiscardPlan(){
  const key=recoveryState.plan?.token,auth=sharedState.token;
  recoveryState.plan=null;$('sharedRecoveryReview').hidden=true;$('sharedRecoveryReviewBody').replaceChildren();
  for(const id of ['sharedRecoveryConsent','sharedRecoveryScope','sharedRecoveryConflictConsent'])$(id).checked=false;
  if(key&&auth)fetch('/api/shared/recovery/cancel',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+auth},body:JSON.stringify({token:key})}).catch(()=>{});
}
function sharedRecoveryClear(){
  recoveryDiscardPlan();recoveryState.request=null;recoveryState.version++;recoveryState.running=false;
  $('sharedRecoveryEditor').hidden=true;$('sharedRecoveryReason').value='';$('sharedRecoveryTarget').textContent='';
  $('sharedTrashList').replaceChildren();$('sharedRecoveryEvents').replaceChildren();
  recoveryMessage('选择空间后可查看回收站。历史恢复和替代纠正可从记忆库发起。');
}
function recoveryLocked(){return sharedState.busy||sharedState.asking||recoveryState.running||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());}
function sharedRecoveryControls(){
  const locked=recoveryLocked(),selected=!!sharedState.token&&!!sharedState.space;
  for(const id of ['sharedRecoveryLoad','sharedRecoveryLoadEvents','sharedOpenRecovery'])$(id).disabled=locked||!selected;
  $('sharedRecoveryReason').disabled=locked||!recoveryState.request;
  $('sharedRecoveryPreview').disabled=locked||!selected||!recoveryState.request;
  for(const id of ['sharedRecoveryConsent','sharedRecoveryScope','sharedRecoveryConflictConsent'])$(id).disabled=locked||!recoveryState.plan;
  $('sharedRecoveryConfirm').disabled=locked||!recoveryState.plan||!$('sharedRecoveryConsent').checked||!$('sharedRecoveryScope').checked||
    (!!recoveryState.plan?.conflicts?.length&&!$('sharedRecoveryConflictConsent').checked);
  $('sharedRecoveryCancel').disabled=locked;
}
async function recoveryRun(work){
  if(!sharedState.token||!sharedState.space||recoveryLocked())return;
  const epoch=sharedState.epoch,auth=sharedState.token,space=sharedState.space.id,version=recoveryState.version;
  const base=()=>epoch===sharedState.epoch&&auth===sharedState.token&&space===sharedState.space?.id;
  const current=()=>base()&&version===recoveryState.version;
  recoveryState.running=true;sharedState.busy=true;sharedControls();
  try{await work(current,base);}catch(error){if(current())recoveryMessage(error.message);}
  finally{if(base()){recoveryState.running=false;sharedState.busy=false;sharedControls();}}
}
function sharedRecoveryOpen(){showWorkspacePage('shared');showSharedPage('backup');$('sharedRecoveryPanel').open=true;$('sharedRecoveryPanel').scrollIntoView?.({block:'nearest'});}
function sharedRecoveryStart(request,description){
  if(recoveryLocked()||!sharedState.space)return;
  recoveryDiscardPlan();recoveryState.version++;recoveryState.request={...request,space_id:sharedState.space.id};
  $('sharedRecoveryReason').value=request.operation==='trash'?'用户确认移入回收站。':'';
  $('sharedRecoveryTarget').textContent=description;$('sharedRecoveryEditor').hidden=false;
  recoveryMessage('尚未执行。填写操作说明，再查看前后对照。');sharedRecoveryOpen();sharedRecoveryControls();
}
function recoveryRenderRecord(panel,title,record){
  const p=document.createElement('p');p.textContent=title+'：'+record.content+'\n作者：'+record.author_name+'；状态：'+sharedStatusNames[record.status]+'；事件日期：'+(record.event_date||'未填写')+
    '\n来源说明：'+(record.source_note||'未填写')+'\n状态说明：'+(record.status_reason||'未填写')+
    '\n可见：'+(record.visibility==='all'?'全体成员（含以后加入的成员），当前为：':'')+record.audience.join('、')+
    '；修改：'+(record.edit_policy==='author'?'作者和管理员':'可见的编辑成员');panel.append(p);
}
$('sharedRecoveryLoad').onclick=()=>recoveryRun(async current=>{
  const result=await sharedApi('/recovery/list',{space_id:sharedState.space.id});if(!current())return;
  $('sharedTrashList').replaceChildren();
  for(const item of result.items){
    const row=document.createElement('li'),p=document.createElement('p'),button=document.createElement('button');
    p.textContent=item.content+'\n原状态：'+sharedStatusNames[item.status]+'；移入时间：'+memoryTime(item.created)+'（北京时间） · 操作人：'+item.deleted_name;
    button.className='text-button';button.textContent='准备恢复此条';
    button.onclick=()=>sharedRecoveryStart({operation:'trash_restore',trash_id:item.trash_id},'从回收站恢复：'+item.content);
    row.append(p,button);$('sharedTrashList').append(row);
  }
  recoveryMessage(result.items.length?'可恢复 '+result.items.length+' 条。仅作者（有编辑角色）和管理员可查看及恢复。':'当前没有你有权恢复的回收站记录。');
});
$('sharedOpenRecovery').onclick=()=>{if(recoveryLocked())return;sharedRecoveryOpen();$('sharedRecoveryLoad').onclick();};
$('sharedRecoveryLoadEvents').onclick=()=>recoveryRun(async current=>{
  const result=await sharedApi('/recovery/events',{space_id:sharedState.space.id});if(!current())return;
  $('sharedRecoveryEvents').replaceChildren();
  for(const event of result.events){
    const row=document.createElement('li'),details=document.createElement('details'),summary=document.createElement('summary'),p=document.createElement('p');
    summary.textContent=memoryTime(event.created)+'（北京时间） · '+recoveryLabels[event.operation]+' · '+event.actor_name;
    p.textContent='操作说明：'+event.reason;details.append(summary,p);
    for(const record of event.before)recoveryRenderRecord(details,'操作前',record);
    for(const record of event.after)recoveryRenderRecord(details,'操作后',record);
    row.append(details);$('sharedRecoveryEvents').append(row);
  }
  recoveryMessage('显示最近 '+result.events.length+' 条有权查看的操作记录。正文和范围政策为操作时记录，名单按现有成员显示；当前访问仍按现有权限检查。');
});
$('sharedRecoveryPreview').onclick=()=>recoveryRun(async current=>{
  recoveryDiscardPlan();
  const result=await sharedApi('/recovery/preview',{...recoveryState.request,reason:$('sharedRecoveryReason').value});if(!current())return;
  recoveryState.plan=result;const panel=$('sharedRecoveryReviewBody');
  const p=document.createElement('p');p.textContent='操作：'+recoveryLabels[result.operation]+'；归属：'+result.space_name+'；操作人：'+result.actor_name+'；说明：'+result.reason;panel.append(p);
  for(const record of result.before)recoveryRenderRecord(panel,'操作前',record);
  for(const record of result.after)recoveryRenderRecord(panel,'操作后',record);
  const note=document.createElement('p');
  note.textContent=result.operation==='trash'?'确认后移入回收站，保留历史和原文，停止参与查询。':
    result.operation==='undo_replacement'?'仅撤销本次替代关联。旧安排转为待核实，新记录转为过时；两条正文、各自权限、原文和历史继续保留。':
    '恢复内容先标待核实。历史恢复沿用当前权限；回收站恢复按删除时范围核对现有成员，历史和原文继续保留。';
  panel.append(note);
  if(result.history_count){const h=document.createElement('p');h.textContent='保留历史：'+result.history_count+' 条。';panel.append(h);}
  if(result.removed_viewers?.length){const warning=document.createElement('p');warning.textContent='这些账号已不在空间，不会恢复其指定可见权限：'+result.removed_viewers.join('、');panel.append(warning);}
  if(result.source){const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='保留原文 · '+result.source.name;pre.textContent=result.source.text;pre.style.whiteSpace='pre-wrap';details.append(summary,pre);panel.append(details);}
  for(const conflict of result.conflicts){const c=document.createElement('p');c.textContent='可能冲突：'+conflict.content;panel.append(c);}
  $('sharedRecoveryConflictLabel').hidden=!result.conflicts.length;$('sharedRecoveryReview').hidden=false;
  recoveryMessage('尚未执行。请核对前后内容和权限，预览10分钟有效。');
});
$('sharedRecoveryConfirm').onclick=()=>recoveryRun(async(current,base)=>{
  const plan=recoveryState.plan;
  if(!plan||!$('sharedRecoveryConsent').checked||!$('sharedRecoveryScope').checked||(plan.conflicts.length&&!$('sharedRecoveryConflictConsent').checked))return;
  const result=await sharedApi('/recovery/confirm',{space_id:plan.space_id,token:plan.token,confirmed:true,scope_confirmed:true,acknowledge_conflicts:$('sharedRecoveryConflictConsent').checked});
  if(!current())return;
  sharedRecoveryClear();await sharedRefresh();if(!base())return;
  showSharedPage('library');
  sharedMessage(result.already_applied?'此预览此前已执行，没有重复操作。请以当前列表为准。':
    result.operation==='trash'?'已移入回收站，历史和原文保留，可在备份与恢复中恢复。':
    result.operation==='undo_replacement'?'替代已纠正：旧安排待核实，新记录过时，双方历史与纠正原因已保留。':'内容已恢复为待核实，原文、历史与操作说明已保留。');
});
$('sharedRecoveryCancel').onclick=()=>{if(recoveryLocked())return;sharedRecoveryClear();sharedRecoveryControls();recoveryMessage('已取消本次操作，没有写入。');};
$('sharedRecoveryReason').oninput=()=>{recoveryDiscardPlan();sharedRecoveryControls();};
for(const id of ['sharedRecoveryConsent','sharedRecoveryScope','sharedRecoveryConflictConsent'])$(id).onchange=sharedRecoveryControls;
sharedRecoveryClear();sharedRecoveryControls();
