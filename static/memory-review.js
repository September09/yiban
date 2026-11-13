let memoryReviewPlan=null, memoryReviewVersion=0, memoryHistoryVersion=0;
function memoryReviewControls(){
  const locked=memoryLocked(),valid=memoryReviewPlan?.target.id===activeProfile;
  $('memoryReviewChoice').disabled=locked||!valid;
  $('memoryReviewConsent').disabled=locked||!valid;
  $('confirmMemoryReview').disabled=locked||!valid||!$('memoryReviewConsent').checked;
  $('cancelMemoryReview').disabled=locked;
}
function resetMemoryReview(){
  const previous=memoryReviewPlan;if(previous)api('/api/memory/review/cancel',{profile_id:previous.target.id,token:previous.token}).catch(()=>{});
  memoryReviewVersion++;memoryReviewPlan=null;$('memoryReviewPanel').hidden=true;
  $('memoryReviewConsent').checked=false;$('memoryReviewDiff').replaceChildren();$('memoryReviewChoice').replaceChildren();
  $('memoryReviewStatus').textContent='';$('memoryReviewTarget').textContent='';memoryReviewControls();
}
function showMemoryReview(plan){
  if(plan.target.id!==activeProfile)return;
  resetMemoryReview();memoryReviewPlan=plan;$('personalPanel').open=true;$('memoryReviewPanel').hidden=false;
  $('memoryReviewTarget').textContent='保存到：'+plan.target.name;
  if(plan.kind==='conflict'){
    $('memoryReviewStatus').textContent='请核对两条记忆的状态变化。尚未修改；确认后同时生效，原记录和历史保留。';
    for(const change of plan.changes){const box=document.createElement('article');box.className='overview-memory';for(const text of [change.before.content,'修改前：'+personalStateText(change.before),'确认后：'+personalStateText(change.after)]){const line=document.createElement('p');line.textContent=text;box.append(line);}$('memoryReviewDiff').append(box);}
    const option=document.createElement('option');option.value='pair';option.textContent=plan.action==='keep_both'?'两条均待核实并存（仍会提示疑似冲突）':'按上述对照更新两条状态';$('memoryReviewChoice').append(option);$('memoryReviewChoice').value='pair';
    if(typeof showWorkspacePage==='function')showWorkspacePage('personal');memoryReviewControls();return;
  }
  $('memoryReviewStatus').textContent=plan.stale?'这条记忆在你开始编辑后发生了变化，下面显示数据库里的最新内容。尚未保存，请重新核对。':plan.conflicts.length?'发现可能冲突的表述。尚未保存，请选择并存或替换。':'尚未保存，请核对修改前后的内容。';
  function line(label,text){const p=document.createElement('p');p.textContent=label+'：'+text;$('memoryReviewDiff').append(p);}
  if(plan.original)line('当前内容',plan.original.content);
  line('准备保存',plan.content);line('保存后状态',personalStateText(plan));if(plan.original)line('原状态',personalStateText(plan.original));if(plan.state_reset||plan.conflicts.length)line('说明','正文／日期变化或存在疑似冲突，本次保存后为待核实。');
  for(const conflict of plan.conflicts){line('可能冲突',conflict.content);line('该条当前状态',personalStateText(conflict));}
  const targets=[];
  if(plan.original)targets.push({id:plan.original.id,label:'更新正在编辑的这条记忆'});
  for(const item of plan.conflicts)if(!targets.some(x=>x.id===item.id))targets.push({id:item.id,label:'替换：'+item.content});
  if(plan.can_append){const option=document.createElement('option');option.value='append';option.textContent='保留现有内容，另存一条（并存）';$('memoryReviewChoice').append(option);}
  for(const target of targets){const option=document.createElement('option');option.value=target.id;option.textContent=target.label;$('memoryReviewChoice').append(option);}
  $('memoryReviewChoice').value=plan.original?.id||(plan.can_append?'append':targets[0]?.id||'');
  if(typeof showWorkspacePage==='function')showWorkspacePage('personal');
  notice('请在「个人记忆」核对变更并确认；当前尚未写入。');memoryReviewControls();
}
async function confirmMemoryReview(){
  const plan=memoryReviewPlan;if(memoryLocked()||!plan||plan.target.id!==activeProfile||!$('memoryReviewConsent').checked)return;
  const identity=activeProfile,version=memoryReviewVersion,choice=$('memoryReviewChoice').value;
  memoryBusy=true;personalControls();
  try{
    await api(plan.kind==='conflict'?'/api/memory/conflicts/confirm':'/api/memory/review/confirm',{profile_id:identity,token:plan.token,confirmed:true,resolution:choice==='append'?'append':'replace',replace_id:choice==='append'?null:choice});
    if(identity!==activeProfile||version!==memoryReviewVersion)return;
    resetMemoryReview();resetMemoryEditor();closeMemoryHistory();await refreshMemories();notice('记忆变更已确认保存，可点击「修改历史」查看。');
  }catch(error){if(identity===activeProfile&&version===memoryReviewVersion){memoryReviewPlan=null;$('memoryReviewConsent').checked=false;$('memoryReviewStatus').textContent=error.message+' 请重新点击保存记忆，或重新发送记住指令。';}}
  finally{memoryBusy=false;personalControls();}
}
function closeMemoryHistory(){memoryHistoryVersion++;$('memoryHistoryPanel').hidden=true;$('memoryHistoryItems').replaceChildren();$('memoryHistoryStatus').textContent='';}
async function showMemoryHistory(id){
  if(!activeProfile||memoryLocked())return;
  closeMemoryHistory();const version=memoryHistoryVersion,identity=activeProfile;$('memoryHistoryPanel').hidden=false;$('memoryHistoryStatus').textContent='正在读取历史…';
  try{
    const data=await(await api('/api/memory/history',{profile_id:identity,id})).json();
    if(version!==memoryHistoryVersion||identity!==activeProfile)return;
    $('memoryHistoryStatus').textContent=`共 ${data.total} 次记录，显示最近 ${data.entries.length} 次。`;
    const labels={baseline:'升级时已有内容（更早历史未知）',created:'新建记忆',edited:'修改内容',imported:'文件导入',confirmed_replace:'确认替换',confirmed_append:'确认并存新增',confirmed_state:'确认状态／日期调整',state_changed:'状态／日期调整',conflict_prefer:'确认冲突取舍',conflict_keep_both:'确认待核实并存'};
    for(const item of data.entries){
      const row=document.createElement('li'),meta=document.createElement('small'),before=document.createElement('span'),after=document.createElement('span');
      meta.className='memory-meta';meta.textContent=`${memoryTime(item.changed)}（北京时间） · ${labels[item.operation]||'变更'}`;
      before.textContent=item.before_content===null?'':`修改前：${item.before_content}`;after.textContent=`记录内容：${item.after_content}`;
      const state=document.createElement('small');state.className='memory-meta';state.textContent='状态：'+(personalStatusNames[item.before_status]||'更早状态未知')+' → '+(personalStatusNames[item.after_status]||'更早状态未知')+'；日期：'+(item.before_event_date||'未记录')+' → '+(item.after_event_date||'未记录')+'；新说明：'+(item.after_status_reason||'未填写');row.append(meta,before,after,state);$('memoryHistoryItems').append(row);
    }
  }catch(error){if(version===memoryHistoryVersion)$('memoryHistoryStatus').textContent=error.message;}
}
$('memoryReviewConsent').onchange=memoryReviewControls;
$('memoryReviewChoice').onchange=()=>{$('memoryReviewConsent').checked=false;memoryReviewControls();};
$('confirmMemoryReview').onclick=confirmMemoryReview;
$('cancelMemoryReview').onclick=()=>{if(!memoryLocked()){resetMemoryReview();notice('已取消，记忆未修改。');}};
$('closeMemoryHistory').onclick=closeMemoryHistory;
$('memoryText').oninput=()=>{$('memoryState').value='pending';$('memoryStateReason').value='';if(memoryReviewPlan)resetMemoryReview();};
memoryReviewControls();
