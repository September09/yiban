// Model-reported citations are checked against this turn's authorized sources.
// Neither candidate retrieval nor a valid reference number proves a claim is true.
function sharedClearSourceCards(){
  $('sharedAnswerSources').hidden=true;$('sharedCitationStatus').textContent='';
  $('sharedCitedPanel').hidden=true;$('sharedCitedSources').replaceChildren();
  $('sharedSources').replaceChildren();$('sharedSourcesPanel').hidden=true;$('sharedSourcesPanel').open=false;
  $('sharedCandidatesTitle').textContent='候选资料';
}
function sharedSourceCard(source,kind,spaceId,answerVersion){
  const row=document.createElement('li');row.className='answer-source-card';
  const title=document.createElement('strong');title.textContent=`资料 ${source.reference||'—'} · ${kind}`;
  const badge=document.createElement('span');badge.className='memory-state memory-state-'+(source.status||'pending');badge.textContent=sharedStatusNames[source.status||'pending'];
  const body=document.createElement('span');body.textContent=source.content;
  const meta=document.createElement('small');meta.className='memory-meta';
  meta.textContent=`提供者：${source.author_name||'未记录'}\n事件日期：${source.event_date||'未填写'}\n记录更新：${source.updated?memoryTime(source.updated)+'（北京时间）':'未记录'}\n来源说明：${source.source_note||'未填写'}`;
  const reason=document.createElement('span');reason.className='feature-help';reason.textContent=source.status_reason?'状态说明：'+source.status_reason:'';
  const evidence=document.createElement('button');evidence.className='text-button';
  evidence.textContent=source.has_evidence?'查看原文摘录':'未保留原文依据';evidence.disabled=!source.has_evidence;
  evidence.onclick=()=>{if(sharedState.space?.id!==spaceId||sharedState.askVersion!==answerVersion)return;sharedOpenEvidence(source);};
  const origin=document.createElement('span');origin.className='feature-help';
  origin.textContent=source.has_evidence?'原文资料：'+(source.evidence_source_name||'已归档资料'):'来源说明仅是备注，不能据此还原原文。';
  row.append(title,badge,body,meta,reason,origin,evidence);return row;
}
function sharedRenderAnswerSources(result){
  sharedClearSourceCards();const sources=Array.isArray(result.sources)?result.sources:[];
  if(!sources.length)return;
  const ids=result.answer_source_ids,known=new Set(sources.map(item=>item.id));
  const confirmed=['model_reported','linked_record'].includes(result.source_usage)&&Array.isArray(ids)&&new Set(ids).size===ids.length&&ids.every(id=>typeof id==='string'&&known.has(id));
  const cited=new Set(confirmed?ids:[]);
  const used=sources.filter(item=>cited.has(item.id)),candidates=sources.filter(item=>!cited.has(item.id));
  $('sharedAnswerSources').hidden=false;
  $('sharedCitationStatus').textContent=confirmed
    ?(used.length?(result.source_usage==='linked_record'?'实际引用由程序按替代关联定位，并直接展示已读取的旧记录；过时内容仅供回顾。':'实际引用依据模型返回的编号区分，已核对编号范围；仍请结合原文核实回答。'):'本轮没有实际引用；下方候选资料不能作为这个回答的事实依据。')
    :'引用关系未确认：模型未返回有效的引用编号。下方仅列候选资料，不能认定它们支持回答。';
  const space=sharedState.space?.id,version=sharedState.askVersion;
  for(const source of used)$('sharedCitedSources').append(sharedSourceCard(source,'实际引用',space,version));
  $('sharedCitedPanel').hidden=!used.length;
  for(const source of candidates)$('sharedSources').append(sharedSourceCard(source,confirmed?'未被引用的候选':'引用关系未确认',space,version));
  $('sharedSourcesPanel').hidden=!candidates.length;
  $('sharedCandidatesTitle').textContent=(confirmed?'其他候选资料（未被引用）':'候选资料（引用关系未确认）')+` · ${candidates.length} 条`;
}
