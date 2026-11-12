// Evidence is fetched on demand and stays out of personal chat/localStorage.
const sharedEvidenceState={generation:0,id:null};
function sharedCloseEvidence(){
  sharedEvidenceState.generation++;sharedEvidenceState.id=null;
  $('sharedEvidencePanel').hidden=true;$('sharedEvidenceSummary').textContent='';
  $('sharedEvidenceQuotes').replaceChildren();$('sharedEvidenceFull').textContent='';
  $('sharedEvidenceFullButton').hidden=true;
}
function sharedOpenEvidence(item,includeFull=false){
  return sharedRun(async epoch=>{
    const space=sharedState.space?.id;
    if(!space)return;
    sharedCloseEvidence();const generation=sharedEvidenceState.generation;
    let result;
    try{result=await sharedApi('/memories/evidence',{space_id:space,id:item.id,expected_version:item.version,include_full:includeFull});}
    catch(error){if(epoch===sharedState.epoch&&generation===sharedEvidenceState.generation){sharedCloseEvidence();if(error.status===409)sharedClearAnswer();}throw error;}
    if(epoch!==sharedState.epoch||generation!==sharedEvidenceState.generation||space!==sharedState.space?.id)return;
    sharedEvidenceState.id=item.id;
    $('sharedEvidencePanel').hidden=false;$('sharedEvidencePanel').scrollIntoView?.({block:'center',behavior:'smooth'});$('sharedEvidenceQuotes').replaceChildren();$('sharedEvidenceFull').textContent='';
    $('sharedEvidenceFullButton').hidden=!result.available||!result.can_read_full||includeFull;
    $('sharedEvidenceFullButton').onclick=()=>sharedOpenEvidence(item,true);
    if(!result.available){$('sharedEvidenceSummary').textContent=result.message;return;}
    $('sharedEvidenceSummary').textContent='资料：'+result.source_name+' · 保存时间：'+memoryTime(result.created)+'（北京时间）\n'+
      '这是整理时核对过的文字快照，可能包含识别结果和人工修订；记录有人这样说过，不代表事实已经证实。\n'+
      (result.content_changed?'当前正文已修改，以下依据对应导入时的内容，请重新核对。\n':'')+
      '导入时的记忆：'+result.captured_content+'\n完整原文仅作者和管理员可查看；摘录随当前记忆的可见范围开放。';
    for(const quote of result.quotes){const row=document.createElement('li');row.textContent=quote.location+'\n'+quote.text;$('sharedEvidenceQuotes').append(row);}
    if(result.source_text!==undefined){
      $('sharedEvidenceFull').textContent=result.source_text.split('\n').map((line,i)=>(i+1)+'  '+line).join('\n');
    }
  });
}
$('sharedCloseEvidence').onclick=sharedCloseEvidence;
