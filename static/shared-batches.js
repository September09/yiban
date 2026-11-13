// Batch data and confirmation tokens remain in memory; no audio is acquired here.
const batchState={generation:0,running:false,batches:[],detail:null,plan:null};
function batchMessage(text){$('sharedBatchStatus').textContent=text;}
function batchDiscardPlan(){
  const key=batchState.plan?.token,auth=sharedState.token;batchState.plan=null;
  $('sharedBatchReview').hidden=true;$('sharedBatchReviewBody').replaceChildren();
  $('sharedBatchConsent').checked=false;$('sharedBatchScope').checked=false;
  if(key&&auth)fetch('/api/shared/batches/cancel',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+auth},body:JSON.stringify({token:key})}).catch(()=>{});
}
function sharedBatchesClear(){
  batchDiscardPlan();batchState.generation++;batchState.running=false;batchState.batches=[];batchState.detail=null;
  for(const id of ['sharedBatchList','sharedBatchItems','sharedBatchEvents'])$(id).replaceChildren();
  $('sharedBatchDetail').hidden=true;$('sharedBatchInfo').textContent='';$('sharedBatchReason').value='';
  $('sharedBatchSearch').value='';$('sharedBatchSource').hidden=true;$('sharedBatchSourceText').textContent='';
  batchMessage('选择空间后，点击刷新批次。旧资料没有可靠的批次关联，不会自动拼成历史批次。');
}
function batchLocked(){return sharedState.busy||sharedState.asking||batchState.running||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());}
function sharedBatchesControls(){
  const locked=batchLocked(),selected=!!sharedState.space&&!!sharedState.token;
  $('sharedBatchLoad').disabled=!selected||locked;$('sharedBatchSearch').disabled=!selected||locked;
  $('sharedBatchReason').disabled=locked||!batchState.detail?.batch.can_undo;
  $('sharedBatchPreview').hidden=!batchState.detail?.batch.can_undo;
  $('sharedBatchPreview').disabled=locked||!batchState.detail?.batch.eligible_count||!$('sharedBatchReason').value.trim();
  for(const id of ['sharedBatchConsent','sharedBatchScope'])$(id).disabled=locked||!batchState.plan;
  $('sharedBatchConfirm').disabled=locked||!batchState.plan||!$('sharedBatchConsent').checked||!$('sharedBatchScope').checked;
  $('sharedBatchCancel').disabled=locked;
}
async function batchRun(work){
  if(!sharedState.token||!sharedState.space||batchLocked())return;
  const epoch=sharedState.epoch,auth=sharedState.token,space=sharedState.space.id,generation=batchState.generation;
  const base=()=>epoch===sharedState.epoch&&auth===sharedState.token&&space===sharedState.space?.id;
  const current=()=>base()&&generation===batchState.generation;
  const request=(path,data={})=>sharedApi('/batches/'+path,{...data,space_id:space});
  sharedState.busy=true;batchState.running=true;sharedControls();
  try{await work(request,current,base);}catch(error){if(current()){if(error.status===409)batchDiscardPlan();batchMessage(error.message);}}
  finally{if(base()){batchState.running=false;sharedState.busy=false;sharedControls();}}
}
function batchRenderList(){
  $('sharedBatchList').replaceChildren();const query=$('sharedBatchSearch').value.trim().toLowerCase();
  const items=batchState.batches.filter(b=>(b.name+' '+b.actor_name+' '+b.id).toLowerCase().includes(query));
  for(const item of items){
    const row=document.createElement('li'),info=document.createElement('p'),button=document.createElement('button');
    info.textContent=item.name+'\n'+memoryTime(item.created)+'（北京时间） · 导入者：'+item.actor_name+'\n当前可见 '+item.visible_count+' 条；在库 '+item.active_count+' 条，回收站 '+item.trash_count+' 条。';
    button.className='secondary-button';button.textContent='查看本批记忆与来源';button.onclick=()=>batchOpen(item.id);
    row.append(info,button);$('sharedBatchList').append(row);
  }
  if(!items.length){const row=document.createElement('li');row.textContent=query?'没有匹配的批次。':'当前没有可见的导入批次。';$('sharedBatchList').append(row);}
}
function batchRecord(row,item,label=''){
  const p=document.createElement('p');p.textContent=label+(item.content||'记录已不在当前库中')+
    (item.content?'\n状态：'+sharedStatusNames[item.status]+(item.status_reason?'（'+item.status_reason+'）':'')+'；日期：'+(item.event_date||'未指定')+'\n来源资料：'+(item.source_name||'未提供')+'；来源说明：'+(item.source_note||'未填写')+
    '\n作者：'+item.author_name+'；可见：'+item.audience.join('、')+'；修改权限：'+(item.edit_policy==='author'?'作者和管理员':'可见的编辑成员'):'');row.append(p);
}
function batchOpen(identity){return batchRun(async(request,current)=>{
  batchDiscardPlan();$('sharedBatchSource').hidden=true;$('sharedBatchSourceText').textContent='';
  const result=await request('detail',{batch_id:identity});if(!current())return;
  batchState.detail=result;$('sharedBatchDetail').hidden=false;$('sharedBatchReason').value='';
  const b=result.batch;$('sharedBatchInfo').textContent=b.name+' · 批次编号：'+b.id+'\n'+memoryTime(b.created)+'（北京时间） · 导入者：'+b.actor_name+'\n当前可撤销 '+b.eligible_count+' 条。这里只显示当前账号有权查看的记忆。';
  $('sharedBatchItems').replaceChildren();$('sharedBatchEvents').replaceChildren();
  for(const item of result.items){
    const row=document.createElement('li');batchRecord(row,item);
    const state=document.createElement('p');state.textContent=item.state==='trash'?'已在回收站':item.eligible?'保持导入原样，可预览撤销':item.reason;row.append(state);
    if(item.has_evidence){
      const quote=document.createElement('button');quote.className='text-button';quote.textContent='查看原文摘录';quote.onclick=()=>batchSource(item,false);row.append(quote);
      if(item.can_read_full){const full=document.createElement('button');full.className='text-button';full.textContent='查看完整原文';full.onclick=()=>batchSource(item,true);row.append(full);}
    }else if(item.content){const note=document.createElement('p');note.textContent='导入时未归档原文；来源名称无法还原原文。';row.append(note);}
    $('sharedBatchItems').append(row);
  }
  for(const e of result.events){const row=document.createElement('li');row.textContent=memoryTime(e.created)+'（北京时间） · '+e.actor_name+'撤销 '+e.withdrawn+' 条\n说明：'+e.reason;$('sharedBatchEvents').append(row);}
  batchMessage('批次已加载。查看和预览不会修改记忆。');
});}
function batchSource(item,full){return batchRun(async(request,current)=>{
  const result=await request('source',{batch_id:batchState.detail.batch.id,id:item.id,expected_version:item.version,include_full:full});if(!current())return;
  $('sharedBatchSource').hidden=false;
  $('sharedBatchSourceText').textContent=result.available?'资料：'+result.source_name+'\n'+(result.content_changed?'当前正文已修改，以下依据对应导入时内容。\n':'')+
    '导入时记忆：'+result.captured_content+'\n'+result.quotes.map(q=>q.location+'：'+q.text).join('\n')+
    (result.source_text!==undefined?'\n\n完整文字快照：\n'+result.source_text:'')+'\n原文可能含识别或人工修订；引文相符不代表事实已经核实。':result.message;
});}
$('sharedBatchLoad').onclick=()=>batchRun(async(request,current)=>{
  batchDiscardPlan();batchState.detail=null;$('sharedBatchDetail').hidden=true;$('sharedBatchSource').hidden=true;$('sharedBatchSourceText').textContent='';
  const result=await request('list');if(!current())return;
  batchState.batches=result.batches;batchRenderList();
  batchMessage('已加载 '+result.batches.length+' 个可见批次。'+(result.untracked_count?'另有 '+result.untracked_count+' 条历史／手动记忆没有可靠批次关联，继续在记忆库管理。':''));
});
$('sharedBatchPreview').onclick=()=>batchRun(async(request,current)=>{
  if(!batchState.detail)return;batchDiscardPlan();
  const plan=await request('preview',{batch_id:batchState.detail.batch.id,reason:$('sharedBatchReason').value});if(!current())return;
  batchState.plan=plan;$('sharedBatchReview').hidden=false;
  const panel=$('sharedBatchReviewBody'),info=document.createElement('p');
  info.textContent='归属：「'+plan.space_name+'」；操作人：'+plan.actor_name+'；批次：'+plan.batch.name+'\n说明：'+plan.reason+'\n将移入回收站 '+plan.withdraw.length+' 条，保留 '+plan.retained.length+' 条。正文、历史和原文保留；移入后停止参与问答。预览10分钟有效。';panel.append(info);
  for(const item of plan.withdraw)batchRecord(panel,item,'将移入回收站：');
  for(const item of plan.retained){batchRecord(panel,item,'本次保留：');const p=document.createElement('p');p.textContent=item.reason;panel.append(p);}
  batchMessage('尚未撤销。请逐条核对移入与保留清单，再勾选两项确认。');
});
$('sharedBatchConfirm').onclick=()=>batchRun(async(request,current,base)=>{
  const p=batchState.plan;if(!p||!$('sharedBatchConsent').checked||!$('sharedBatchScope').checked)return;
  const result=await request('confirm',{token:p.token,confirmed:true,scope_confirmed:true});if(!current())return;
  sharedBatchesClear();await sharedRefresh();if(!base())return;
  batchMessage((result.already_applied?'此预览已执行，没有重复撤销。':'已撤销 '+result.withdrawn+' 条，保留 '+result.retained+' 条。')+'可刷新批次查看；误撤销可到“备份与恢复”从回收站恢复。');
});
$('sharedBatchReason').oninput=()=>{batchDiscardPlan();sharedBatchesControls();};
$('sharedBatchSearch').oninput=batchRenderList;
for(const id of ['sharedBatchConsent','sharedBatchScope'])$(id).onchange=sharedBatchesControls;
$('sharedBatchCancel').onclick=()=>{if(batchLocked())return;batchDiscardPlan();sharedBatchesControls();batchMessage('已取消撤销预览，没有修改记忆。');};
$('sharedBatchCloseSource').onclick=()=>{$('sharedBatchSource').hidden=true;$('sharedBatchSourceText').textContent='';};
sharedBatchesClear();sharedBatchesControls();
