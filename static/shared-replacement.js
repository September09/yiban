// Transient replacement editor; no content or confirmation tokens are persisted.
let sharedReplacementTarget=null;
function sharedCloseLineage(){$('sharedLineagePanel').hidden=true;$('sharedLineageList').replaceChildren();$('sharedLineageInfo').textContent='';}
function sharedReplacementClear(){sharedReplacementTarget=null;$('sharedReplacementPanel').hidden=true;for(const id of ['sharedReplacementText','sharedReplacementDate','sharedReplacementReason'])$(id).value='';$('sharedReplacementInfo').textContent='';sharedCloseLineage();}
function sharedReplacementControls(){const locked=sharedState.busy||sharedState.asking||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());for(const id of ['sharedReplacementText','sharedReplacementDate','sharedReplacementReason','sharedReplacementPreview','sharedReplacementCancel'])$(id).disabled=locked||!sharedReplacementTarget;}
function sharedReplacementButtons(row,item){
  const space=sharedState.space?.id;
  if(item.can_replace){const button=document.createElement('button');button.className='text-button';button.textContent='替代为新安排';
    button.onclick=()=>{if(sharedState.busy||sharedState.asking||sharedState.space?.id!==space)return;sharedClearAnswer();sharedReplacementClear();sharedReplacementTarget={id:item.id,version:item.version,space};$('sharedReplacementPanel').hidden=false;$('sharedReplacementText').value=item.content;$('sharedReplacementDate').value=item.event_date||'';$('sharedReplacementInfo').textContent=`旧安排：${item.content}\n原状态：${sharedStatusNames[item.status]}；日期：${item.event_date||'未填写'}。新安排沿用原作者、可见范围和修改权限；下一步展示完整对照，此处尚未保存。`;sharedReplacementControls();$('sharedReplacementText').focus();};row.append(button);}
  if(item.has_replacement){const button=document.createElement('button');button.className='text-button';button.textContent='查看新旧安排追溯';
    button.onclick=()=>sharedRun(async epoch=>{if(sharedState.space?.id!==space)return;const result=await sharedApi('/memories/lineage',{space_id:space,id:item.id,version:item.version});if(epoch!==sharedState.epoch||space!==sharedState.space?.id)return;sharedCloseLineage();$('sharedLineagePanel').hidden=false;$('sharedLineageInfo').textContent='按替代顺序展示当前账号可见的记录。'+(result.limited?'部分关联记录不可见或超出展示范围。':'');
      for(const record of result.records){const li=document.createElement('li'),p=document.createElement('p');p.textContent=`${record.id===result.selected_id?'本次查看 · ':''}${sharedStatusNames[record.status]} · 事件日期：${record.event_date||'未填写'}\n${record.content}\n状态说明：${record.status_reason||'未填写'}\n来源：${record.source_note||'未填写'}`;li.append(p);
        if(record.has_evidence){const evidence=document.createElement('button');evidence.className='text-button';evidence.textContent='查看这条记录的原文依据';evidence.onclick=()=>sharedOpenEvidence(record);li.append(evidence);}$('sharedLineageList').append(li);}$('sharedLineagePanel').scrollIntoView({block:'nearest'});});row.append(button);}
}
$('sharedReplacementPreview').onclick=()=>sharedRun(async epoch=>{
  const target=sharedReplacementTarget,auth=sharedState.token;if(!target||target.space!==sharedState.space?.id)return;
  const data={space_id:target.space,id:target.id,version:target.version,content:$('sharedReplacementText').value,event_date:$('sharedReplacementDate').value,reason:$('sharedReplacementReason').value};
  sharedClearAnswer();const result=await sharedApi('/agent/memory/replace-preview',data);
  if(epoch!==sharedState.epoch||sharedReplacementTarget!==target){sharedDiscardAgentResult(result,auth);return;}
  sharedReplacementClear();if(typeof showSharedPage==='function')showSharedPage('question');sharedRenderAgent(result.agent);sharedRenderAgentDraft(result);$('sharedAnswer').textContent=result.answer;$('sharedAgentReview').scrollIntoView({block:'nearest'});
});
$('sharedReplacementCancel').onclick=()=>{if(sharedState.busy||sharedState.asking)return;sharedReplacementClear();sharedMessage('已取消新安排编辑，没有修改记忆。');};
$('sharedCloseLineage').onclick=sharedCloseLineage;
