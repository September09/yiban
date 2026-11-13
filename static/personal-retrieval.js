// Evidence stays in this page only. Profile switching clears results and cancels in-flight work.
const personalQueryState={version:0,running:false,controller:null,turn:null,trace:null};
function personalMemoryCancel(clear=false){
  personalQueryState.version++;personalQueryState.controller?.abort();personalQueryState.controller=null;
  if(personalQueryState.turn)fetch('/api/turn/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({turn_id:personalQueryState.turn})}).catch(()=>{});
  if(personalQueryState.running){if(typeof diagnosticOverview!=='undefined')diagnosticOverview.finishLocal(personalQueryState.trace,'cancelled');busy=false;conversationDiagnostics.finish(personalQueryState.trace,'cancelled','stopped');}
  personalQueryState.running=false;personalQueryState.turn=null;personalQueryState.trace=null;
  $('personalMemoryAnswer').textContent='';$('personalMemorySources').replaceChildren();$('personalMemoryCandidates').replaceChildren();
  $('personalMemoryEvidence').hidden=true;$('personalMemoryResultInfo').textContent='';
  if(clear)$('personalMemoryQuestion').value='';
}
function personalMemoryControls(){
  const locked=busy||recording||enrolling||memoryBusy||wakePhase==='command';
  $('personalMemoryQuestion').disabled=locked;$('personalIncludeOutdated').disabled=locked;
  $('personalMemoryAsk').disabled=locked||!activeProfile;
  $('personalMemoryStop').disabled=!personalQueryState.running;
}
function personalSourceCard(source){
  const row=document.createElement('li'),body=document.createElement('p'),meta=document.createElement('small');
  body.textContent='资料 '+source.reference+'：'+source.content;
  meta.textContent=personalStateText(source)+'；来源：'+({manual:'主动录入',conversation:'对话指令保存',legacy:'历史来源未记录'}[source.source]||'未记录')+
    '；首次保存：'+(source.created?memoryTime(source.created)+'（北京时间）':'未记录')+
    '；更新：'+(source.updated?memoryTime(source.updated)+'（北京时间）':'未记录')+'。已保存不代表已核实或仍适用。';
  row.append(body,meta);return row;
}
function personalRetrievalInfo(meta){
  if(!meta)return '';
  return (meta.strategy==='hybrid'?'语义与文字混合检索':'文字检索')+' · 找到 '+meta.hit_count+' 条 · '+meta.elapsed_ms+' 毫秒'+
    (meta.fallback?' · 语义服务暂不可用，已回退文字检索':'');
}
function renderPersonalMemoryContext(row,result){
  if(!result?.profile)return;
  const details=document.createElement('details'),summary=document.createElement('summary');
  details.className='personal-context';summary.textContent='个人记忆检索 · '+result.profile.name+' · '+(result.sources.length?'候选 '+result.sources.length+' 条':'未找到相关长期记忆');details.append(summary);
  const note=document.createElement('p');note.textContent=personalRetrievalInfo(result.retrieval)+'。以下是提供给本轮普通聊天的候选资料，未确认是否被实际引用；普通聊天还可能使用当前对话内容。';details.append(note);
  for(const source of result.sources)details.append(personalSourceCard(source));
  row.item.append(details);
}
$('personalMemoryAsk').onclick=async()=>{
  const query=$('personalMemoryQuestion').value.trim();
  if(!activeProfile||busy||recording||enrolling||memoryBusy||wakePhase==='command')return;
  if(!query){$('personalMemoryResultInfo').textContent='请先输入想查询的个人记忆问题。';return;}
  cancelTurn();const version=personalQueryState.version,identity=activeProfile;
  const controller=new AbortController(),turn=crypto.randomUUID();personalQueryState.controller=controller;personalQueryState.turn=turn;
  personalQueryState.running=true;busy=true;personalQueryState.trace=conversationDiagnostics.start('text');
  const trace=personalQueryState.trace;if(typeof diagnosticOverview!=='undefined')diagnosticOverview.startLocal(trace,'personal',query);conversationDiagnostics.stage(trace,'request');controls();
  const current=()=>version===personalQueryState.version&&identity===activeProfile;
  $('personalMemoryResultInfo').textContent='正在查询当前档案的已保存记忆…';
  try{
    await pauseWake();if(!current())return;
    const result=await(await api('/api/memory/ask',{profile_id:identity,query,turn_id:turn,include_outdated:$('personalIncludeOutdated').checked},'application/json',controller.signal,conversationDiagnostics.headers(trace))).json();
    if(!current())return;
    if(typeof diagnosticOverview!=='undefined')diagnosticOverview.sourcesLocal(trace,result);
    $('personalMemoryAnswer').textContent=result.answer;
    $('personalMemoryResultInfo').textContent='查询档案：'+result.profile?.name+' · '+personalRetrievalInfo(result.retrieval)+
      (result.source_usage==='unavailable'?' · 引用未能核对，请重试':'');
    const cited=new Set(result.answer_source_ids||[]);
    for(const source of result.sources)(cited.has(source.id)?$('personalMemorySources'):$('personalMemoryCandidates')).append(personalSourceCard(source));
    $('personalMemoryEvidence').hidden=!result.sources.length;
    $('personalMemoryCandidatesPanel').hidden=!result.sources.some(s=>!cited.has(s.id));
    $('personalMemoryCitedTitle').textContent=cited.size?(result.source_usage==='program_selected'?'程序列出的冲突依据':'实际引用（模型声明）'):'没有已确认的引用';
    conversationDiagnostics.finish(trace);if(typeof diagnosticOverview!=='undefined')diagnosticOverview.finishLocal(trace);
  }catch(error){if(current()&&error.name!=='AbortError'){$('personalMemoryResultInfo').textContent='查询未完成：'+error.message;conversationDiagnostics.finish(trace,'error','request');if(typeof diagnosticOverview!=='undefined')diagnosticOverview.finishLocal(trace,'failed');}}
  finally{
    conversationDiagnostics.refresh(trace);
    if(current()){personalQueryState.running=false;personalQueryState.controller=null;personalQueryState.turn=null;personalQueryState.trace=null;busy=false;controls();idle();returnToWake();}
  }
};
$('personalMemoryStop').onclick=()=>{personalMemoryCancel();$('personalMemoryResultInfo').textContent='已停止查询。';controls();idle();returnToWake();};
$('personalMemoryQuestion').oninput=()=>{if(!personalQueryState.running){personalMemoryCancel();personalMemoryControls();}};
personalMemoryCancel();personalMemoryControls();
