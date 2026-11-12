// Agent drafts are transient and session-bound; only an explicit button confirms them.
let sharedAgentDraft=null;
function sharedDiscardAgentResult(result,auth){
  const draft=result?.memory_preview||result?.memory_selection;if(!draft?.token||!auth)return;
  fetch('/api/shared/agent/memory/cancel',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+auth},body:JSON.stringify({space_id:draft.space_id||result.space_id,token:draft.token})}).then(()=>{if(auth===sharedState.token&&(draft.space_id||result.space_id)===sharedState.space?.id&&typeof sharedAuditObserve==='function')sharedAuditObserve({available:true});}).catch(()=>{});
}
function sharedResetAgent(send=true){
  const old=sharedAgentDraft;sharedAgentDraft=null;
  if(send&&old)sharedDiscardAgentResult({memory_preview:{token:old.data.token,space_id:old.space}},old.auth);
  $('sharedAgentReview').hidden=true;$('sharedAgentSelect').hidden=true;
  $('sharedAgentReviewSummary').textContent='';$('sharedAgentReviewBody').replaceChildren();$('sharedAgentSelectList').replaceChildren();
  $('sharedAgentReplaceConsent').checked=false;$('sharedAgentReplaceLabel').hidden=true;
  $('sharedAgentConsent').checked=false;$('sharedAgentConflictConsent').checked=false;$('sharedAgentConflictLabel').hidden=true;
}
function sharedAgentControls(){
  if(typeof workspaceActivity==='function')workspaceActivity();
  const locked=sharedState.busy||sharedState.asking||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());
  const draft=sharedAgentDraft?.kind==='preview'?sharedAgentDraft.data:null;
  $('sharedAgentReplaceConsent').disabled=locked||!draft;
  $('sharedAgentConsent').disabled=locked||!draft;$('sharedAgentConflictConsent').disabled=locked||!draft;
  $('sharedAgentConfirm').disabled=locked||!draft||!$('sharedAgentConsent').checked||(draft.operation==='replace'&&!$('sharedAgentReplaceConsent').checked)||(draft.conflicts.length>0&&!$('sharedAgentConflictConsent').checked);
  $('sharedAgentCancel').disabled=locked;$('sharedAgentSelectCancel').disabled=locked;
  for(const button of $('sharedAgentSelectList').querySelectorAll('button'))button.disabled=locked;
}
function sharedRenderAgentDraft(result){
  const draft=result.memory_preview,selection=result.memory_selection;
  sharedResetAgent();
  if(!draft&&!selection)return;
  sharedAgentDraft={kind:draft?'preview':'selection',data:draft||selection,space:sharedState.space.id,auth:sharedState.token};
  if(selection){
    $('sharedAgentSelect').hidden=false;
    for(const choice of selection.choices){const row=document.createElement('li'),text=document.createElement('p'),button=document.createElement('button');
      text.textContent=`${choice.number} · ${sharedStatusNames[choice.status]||'待核实'} · ${choice.event_date||'未填写日期'}\n${choice.content}`;
      button.className='secondary-button';button.textContent='选择第 '+choice.number+' 条';
      button.onclick=()=>{if(sharedAgentDraft?.data.token!==selection.token)return;const data={selection_token:selection.token,memory_choice:choice.number};sharedResetAgent(false);return sharedAskQuestion(null,data);};
      row.append(text,button);$('sharedAgentSelectList').append(row);
    }
  }else{
    $('sharedAgentReview').hidden=false;
    $('sharedAgentReviewSummary').textContent=`${draft.operation==='replace'?'新安排替代旧安排':draft.operation==='update'?'修改现有记忆':'新增记忆'} · 归属：${draft.kind==='family'?'家庭':'团队'}「${draft.space_name}」；原作者：${draft.author_name}；本次操作：${draft.actor_name}。\n保存后可见：${draft.after.visibility==='all'?'全体成员（含以后加入的成员）；当前成员：':'指定成员：'}${draft.audience.join('、')}。\n修改权限：${draft.after.edit_policy==='author'?'仅作者和管理员':'可见的编辑成员'}。`;
    const lines=[...(draft.before?[(draft.operation==='replace'?'旧安排（确认后过时，保留原文）：':'修改前：')+draft.before.content,'原可见范围：'+(draft.before.visibility==='all'?'全体成员':'指定成员（作者和管理员始终可见）'),'原事件日期：'+(draft.before.event_date||'未填写')]:[]),
      '准备保存：'+draft.after.content,'事件日期：'+(draft.after.event_date||'未填写'),'保存后状态：'+sharedStatusNames[draft.after.status]+'；说明：'+(draft.after.status_reason||'待成员核实'),
      ...(draft.operation==='replace'?['旧安排当前状态：'+sharedStatusNames[draft.before.status]+'；确认后：过时。原记录及原文依据保留，通过替代关联追溯；不会将旧依据复制为新安排的证明。']:[]),...(draft.operation!=='replace'&&draft.before?.has_evidence?['原文摘录保留；修改后的正文与旧摘录可能不同，原文可见范围随记忆权限变化。']:[]),...draft.conflicts.map(item=>'可能冲突：'+item.content)];
    for(const text of lines){const p=document.createElement('p');p.textContent=text;$('sharedAgentReviewBody').append(p);}
    $('sharedAgentReplaceLabel').hidden=draft.operation!=='replace';
    $('sharedAgentConflictLabel').hidden=!draft.conflicts.length;
    $('sharedAgentConfirm').textContent=draft.operation==='replace'?'确认新安排替代旧安排':draft.operation==='update'?'确认保存这次修改':'确认保存这条记忆';
  }
  sharedAgentControls();
}
$('sharedAgentReplaceConsent').onchange=sharedAgentControls;
$('sharedAgentConsent').onchange=sharedAgentControls;$('sharedAgentConflictConsent').onchange=sharedAgentControls;
function sharedCancelAgentDraft(){if(sharedState.busy||sharedState.asking)return;sharedResetAgent();$('sharedAnswer').textContent='已取消草稿，没有保存或修改记忆。';sharedAgentControls();}
$('sharedAgentCancel').onclick=sharedCancelAgentDraft;$('sharedAgentSelectCancel').onclick=sharedCancelAgentDraft;
$('sharedAgentConfirm').onclick=()=>sharedRun(async epoch=>{
  const draft=sharedAgentDraft;
  if(!draft||draft.kind!=='preview'||!$('sharedAgentConsent').checked||(draft.data.operation==='replace'&&!$('sharedAgentReplaceConsent').checked)||(draft.data.conflicts.length&&!$('sharedAgentConflictConsent').checked))return;
  try{
    await sharedApi('/agent/memory/confirm',{space_id:draft.space,token:draft.data.token,confirmed:true,replacement_confirmed:$('sharedAgentReplaceConsent').checked,acknowledge_conflicts:$('sharedAgentConflictConsent').checked});
    if(epoch!==sharedState.epoch)return;
    sharedResetAgent(false);sharedClearAnswer();await sharedRefresh();
    if(epoch===sharedState.epoch){$('sharedAnswer').textContent=draft.data.operation==='replace'?'新安排已生效，旧安排已标为过时并保留。可在记忆列表查看新旧安排追溯。':'已确认保存，记忆列表和修改历史已更新。';sharedMessage('已按预览中的归属、可见范围和内容保存。');}
  }catch(error){if(epoch===sharedState.epoch){sharedResetAgent();$('sharedAnswer').textContent=error.message+' 请重新生成并核对预览。';}throw error;}
});
sharedAgentControls();
