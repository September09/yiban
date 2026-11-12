// Feedback is explicitly saved on this machine and scoped to the current space account.
const feedbackCategories={correct:'回答正确',wrong:'回答有误',missing:'遗漏资料',citation:'引用有误'};
const feedbackStates={open:'待处理',working:'处理中',resolved:'已复核'};
const feedbackState={receipt:null,formVersion:0,loadVersion:0,saving:false,loading:false,record:null};

function sharedFeedbackResetAnswer(){
  feedbackState.formVersion++;
  feedbackState.receipt=null;
  feedbackState.saving=false;
  $('answerFeedbackForm').hidden=true;
  $('answerFeedbackConsent').checked=false;
  $('answerFeedbackNote').value='';
  $('answerFeedbackStatus').textContent='';
}
function sharedFeedbackClear(){
  sharedFeedbackResetAnswer();
  feedbackState.loadVersion++;
  feedbackState.loading=false;
  feedbackState.record=null;
  $('feedbackList').replaceChildren();
  $('feedbackDetail').replaceChildren();
  $('feedbackInfo').textContent='登录共同记忆空间后，查看本账号的反馈。';
}
function sharedFeedbackOffer(result){
  if(typeof result.feedback_receipt!=='string')return;
  feedbackState.receipt=result.feedback_receipt;
  $('answerFeedbackForm').hidden=false;
  $('answerFeedbackCategory').value='wrong';
  sharedFeedbackControls();
}
function sharedFeedbackControls(){
  const allowed=!!(sharedState.token&&sharedState.space);
  const locked=sharedState.busy||sharedState.asking||feedbackState.saving;
  $('answerFeedbackSave').disabled=!allowed||!feedbackState.receipt||locked||!$('answerFeedbackConsent').checked;
  for(const id of ['answerFeedbackCategory','answerFeedbackNote','answerFeedbackConsent'])$(id).disabled=locked;
  $('feedbackRefresh').disabled=!allowed||feedbackState.loading;
}
function feedbackContext(){
  const epoch=sharedState.epoch,token=sharedState.token,space=sharedState.space?.id;
  return ()=>epoch===sharedState.epoch&&token===sharedState.token&&space===sharedState.space?.id;
}
async function sharedFeedbackSave(){
  if($('answerFeedbackSave').disabled)return;
  const current=feedbackContext(),version=feedbackState.formVersion;
  feedbackState.saving=true;
  sharedFeedbackControls();
  try{
    await sharedApi('/feedback/submit',{space_id:sharedState.space.id,receipt:feedbackState.receipt,
      category:$('answerFeedbackCategory').value,note:$('answerFeedbackNote').value,consent:true});
    if(!current()||version!==feedbackState.formVersion)return;
    feedbackState.receipt=null;
    $('answerFeedbackStatus').textContent='反馈已保存在本机。可在「运行诊断 → 回答反馈」查看。';
  }catch(error){
    if(current()&&version===feedbackState.formVersion)$('answerFeedbackStatus').textContent=error.message;
  }finally{
    if(current()&&version===feedbackState.formVersion){feedbackState.saving=false;sharedFeedbackControls();}
  }
}
async function sharedFeedbackLoad(){
  if(!sharedState.token||!sharedState.space||feedbackState.loading)return;
  const current=feedbackContext(),version=++feedbackState.loadVersion;
  feedbackState.loading=true;
  $('feedbackDetail').replaceChildren();
  feedbackState.record=null;
  sharedFeedbackControls();
  try{
    const data=await sharedApi('/feedback/list',{space_id:sharedState.space.id});
    if(!current()||version!==feedbackState.loadVersion)return;
    $('feedbackList').replaceChildren();
    $('feedbackInfo').textContent=`${sharedState.space.name} · 本账号最近 ${data.records.length} 条反馈。保留90天，全机最多500条。`;
    for(const item of data.records){
      const row=document.createElement('li'),button=document.createElement('button');
      button.className='text-button';
      button.textContent=`${new Date(item.created*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · ${feedbackCategories[item.category]} · ${feedbackStates[item.state]}`;
      button.onclick=()=>sharedFeedbackDetail(item.id);
      row.append(button);$('feedbackList').append(row);
    }
    if(!data.records.length)$('feedbackInfo').textContent+=' 当前没有反馈。';
  }catch(error){
    if(current()&&version===feedbackState.loadVersion){$('feedbackList').replaceChildren();$('feedbackInfo').textContent=error.message;}
  }finally{
    if(current()&&version===feedbackState.loadVersion){feedbackState.loading=false;sharedFeedbackControls();}
  }
}
async function sharedFeedbackDetail(identity){
  feedbackState.loading=false;sharedFeedbackControls();
  const current=feedbackContext(),version=++feedbackState.loadVersion;
  $('feedbackDetail').replaceChildren();
  feedbackState.record=null;
  try{
    const row=await sharedApi('/feedback/detail',{space_id:sharedState.space.id,id:identity});
    if(!current()||version!==feedbackState.loadVersion)return;
    feedbackState.record=row;
    const panel=$('feedbackDetail');
    function line(label,value){const p=document.createElement('p');p.textContent=label+'：'+value;panel.append(p);}
    line('反馈类型',feedbackCategories[row.category]);
    if(!row.content_available){line('资料权限','来源已删除、内容已变化或当前不可访问，已隐藏问答及说明。');return;}
    const snapshot=row.snapshot;
    line('原问题',snapshot.question);
    line('当时回答',snapshot.answer);
    line('反馈说明',row.note||'未填写');
    if(row.sources_changed)line('资料变化','以下为当时快照，当前记忆已更新。');
    const cited=new Set(['model_reported','linked_record'].includes(snapshot.source_usage)?snapshot.answer_source_ids:[]);
    for(const source of snapshot.sources){
      line(cited.has(source.id)?'当时实际引用':'当时候选资料',source.content);
      line('状态与日期',(sharedStatusNames[source.status]||'待核实')+' · '+(source.event_date||'未填写事件日期'));
      line('来源',source.source_note||'未填写');
      if(source.evidence_source_name)line('原文资料名称',source.evidence_source_name);
    }
    const retest=document.createElement('button');retest.className='secondary-button';retest.textContent='把原问题带回问答';
    retest.onclick=()=>{
      if(feedbackState.record!==row||!current())return;
      sharedClearAnswer();
      $('sharedQuestion').value=snapshot.topic&&!snapshot.question.includes(snapshot.topic)?snapshot.topic+'，'+snapshot.question:snapshot.question;
      $('sharedIncludeOutdated').checked=snapshot.include_outdated;
      showWorkspacePage('shared');showSharedPage('question');$('sharedQuestion').focus();
      sharedMessage('已带入原问题。点击「询问共同记忆」重新查询，再回到反馈记录填写复核结果。');
    };
    const state=document.createElement('select');state.setAttribute('aria-label','反馈处理进度');
    for(const [value,label] of Object.entries(feedbackStates)){const option=document.createElement('option');option.value=value;option.textContent=label;state.append(option);}
    state.value=row.state;
    const note=document.createElement('textarea');note.maxLength=2000;note.value=row.resolution;note.setAttribute('aria-label','反馈处理说明');
    const save=document.createElement('button');save.className='secondary-button';save.textContent='保存处理进度';
    save.onclick=async()=>{
      if(save.disabled||feedbackState.record!==row||!current())return;
      save.disabled=true;
      try{
        await sharedApi('/feedback/update',{space_id:sharedState.space.id,id:row.id,version:row.version,state:state.value,resolution:note.value});
        if(current()&&feedbackState.record===row){await sharedFeedbackLoad();await sharedFeedbackDetail(row.id);}
      }catch(error){if(current()&&feedbackState.record===row){line('保存结果',error.message);save.disabled=false;}}
    };
    panel.append(retest,state,note,save);
  }catch(error){if(current()&&version===feedbackState.loadVersion)$('feedbackDetail').textContent=error.message;}
}
$('answerFeedbackConsent').onchange=sharedFeedbackControls;
$('answerFeedbackSave').onclick=sharedFeedbackSave;
$('feedbackRefresh').onclick=sharedFeedbackLoad;
$('feedbackPanel').ontoggle=()=>{if($('feedbackPanel').open)sharedFeedbackLoad();};
sharedFeedbackClear();sharedFeedbackControls();
