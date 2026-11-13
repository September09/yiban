// Source bodies are loaded under current permissions; no overview data enters browser storage.
const diagnosticOverview=(()=>{
  let sharedRows=[],localRows=[],selected=null,generation=0,loading=false,timer=null,sharedNotice='',latestUnavailable=false;
  const labels={...auditStatusNames},errors=new Set(['failed','timeout','rejected','interrupted']);
  const reasons={...auditReasonNames,model_connection:'模型连接失败，请检查模型服务是否启动以及接口连通情况。',model_http:'模型接口拒绝请求，请结合 HTTP 状态检查模型权限、额度或服务状态。',model_output:'模型输出不完整或格式不符合要求，请重试或缩小问题范围。',tool_protocol:'模型请求了无效工具或参数；程序已拦截，未执行该操作。',no_result:'未找到相关资料；这是查询结果，不是程序错误。',network:'页面没有收到完整结果，请检查连接后重试；涉及保存时先核对记忆列表。'};
  const questions=new Map();
  const visible=()=>!$('viewDiagnostics').hidden;
  const context=()=>{const auth=sharedState.token,space=sharedState.space?.id,epoch=sharedState.epoch;return()=>auth===sharedState.token&&space===sharedState.space?.id&&epoch===sharedState.epoch;};
  const modelText=row=>{
    const models=row.models||[],calls=row.usage?.model_calls;
    if(!models.length)return calls>0?'有模型调用，模型名称未记录':row.status==='running'?'等待模型调用信息':calls===0?'本轮未调用模型（程序／规则处理）':'模型信息未记录';
    return models.map(m=>m.model+' · '+(m.route==='local_service'?'本机接口':'远端接口')).join('；')+(Number.isInteger(calls)?'；调用尝试 '+calls+' 次':'');
  };
  function controls(){$('overviewRefresh').disabled=loading;$('overviewClearLocal').disabled=!localRows.length;}
  function clearShared(){generation++;sharedRows=[];questions.clear();sharedNotice='';latestUnavailable=false;loading=false;clearTimeout(timer);timer=null;render();}
  function clearLocal(){localRows=[];render();}
  function rows(){
    const scope=$('overviewScope').value||'all';
    return [...(scope!=='local'?sharedRows:[]),...(scope!=='shared'?localRows:[])].filter(r=>!$('overviewErrors').checked||errors.has(r.status)).sort((a,b)=>b.started-a.started);
  }
  function render(){
    if(!$('overviewList'))return;
    const items=rows();if(!items.some(r=>r.id===selected))selected=items[0]?.id||null;
    $('overviewList').replaceChildren();$('overviewDetail').replaceChildren();
    $('overviewInfo').textContent=(sharedState.space?'当前空间：「'+sharedState.space.name+'」；共同记忆仅本人最近50次请求。':'共同记忆需登录并选择空间。')+' 本页个人查询／普通对话 '+localRows.length+' 轮。'+sharedNotice;
    for(const row of items){
      const button=document.createElement('button');button.className='trace-turn'+(row.id===selected?' selected':'');button.setAttribute('aria-pressed',String(row.id===selected));
      button.textContent=new Date(row.started*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' · '+(row.kind==='shared'?'共同记忆':row.kind==='personal'?'个人查询':'普通对话')+' · '+(labels[row.status]||'状态未知')+'\n'+(questions.get(row.id)||row.question||'轮次 '+row.id.slice(0,8));
      button.onclick=()=>{selected=row.id;render();};$('overviewList').append(button);
    }
    const row=items.find(r=>r.id===selected),panel=$('overviewDetail');
    const add=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;panel.append(node);return node;};
    if(!row){add('p','当前没有可展示的轮次。先发送文字问题，再回到这里刷新。');controls();return;}
    add('h3',(row.kind==='shared'?'共同记忆':row.kind==='personal'?'个人查询':'普通对话')+' · '+(labels[row.status]||'状态未知'));
    add('p','本轮问题：'+(questions.get(row.id)||row.question||'未保存问题正文；可按时间和轮次编号定位。'));
    const summary=add('div','');summary.className='overview-summary';
    const summaryLine=(title,value)=>{const box=document.createElement('div'),name=document.createElement('strong'),body=document.createElement('p');name.textContent=title;body.textContent=value;box.append(name,body);summary.append(box);};
    const mats=row.materials||{searched:[],read:[],cited:[]},known=!!row.materials;
    summaryLine('查询候选',known?(row.search_performed===false?'本轮未执行记忆检索':mats.searched.length+' 条（不等于引用）'):'旧记录／尚未返回，未知');
    summaryLine('实际引用',row.citation_checked?mats.cited.length+' 条':row.kind==='chat'?'普通聊天未核对引用':'引用关系未确认');
    summaryLine('使用模型',modelText(row));summaryLine('处理耗时',auditDuration(row.elapsed_ms));
    const failure=row.failure;
    summaryLine('错误／结果说明',failure?(reasons[failure.code]||'处理异常，请检查失败阶段。')+(failure.http_status?' HTTP '+failure.http_status:''):
      row.status==='running'?'仍在处理，可以刷新查看当前阶段。':row.citation_status==='unavailable'?'引用编号未能核对，无法确认哪些候选真正支持回答。':row.reason==='ok'&&row.search_performed&&known&&!mats.searched.length?reasons.no_result:reasons[row.reason]||'本轮没有记录到错误。');
    if(row.retrieval?.strategy)add('p',retrievalDiagnosticText(row.retrieval));
    if(row.kind==='chat')add('p','普通聊天可能使用近期对话和通用知识；下列长期记忆仅为候选，不能认定回答实际引用了它们。');
    if(row.citation_status==='model_reported')add('p','实际引用依据模型声明的编号区分，已校验编号范围；仍需核对原文是否支持结论。');
    if(row.citation_status==='linked_record')add('p','实际引用由程序按替代关系定位；过时记录仅供回顾。');
    if(row.kind==='shared')add('p','记忆正文按当前权限和版本重新读取；变化、删除或失去权限时隐藏。诊断库不保存当时正文。');
    else add('p','以下为本页当轮返回的资料，刷新或切换档案后清空；修改／删除个人记忆时也会清空本页记录。');
    function materialGroup(title,items,empty){
      const section=document.createElement('section'),heading=document.createElement('h4');heading.textContent=title;section.append(heading);
      if(!items.length){const p=document.createElement('p');p.textContent=empty;section.append(p);}
      for(const source of items){
        const box=document.createElement('article');box.className='overview-memory';
        const p=document.createElement('p');p.textContent=source.available===false?source.reason:source.content;box.append(p);
        if(source.available!==false){
          const meta=document.createElement('small');meta.textContent='状态：'+(sharedStatusNames[source.status]||(source.status==='recorded'?'已保存（未设核实状态）':'未记录'))+'；日期：'+(source.event_date||'未填写')+'\n来源：'+(source.source_note||source.evidence_source_name||({manual:'主动录入',conversation:'对话指令保存',legacy:'历史来源未记录'}[source.source]||'未记录'));box.append(meta);
          if(row.kind==='shared'&&source.has_evidence){const button=document.createElement('button');button.className='text-button';button.textContent='查看原文摘录';button.onclick=()=>{if(!sharedState.space||loading)return;sharedOpenEvidence(source);showWorkspacePage('shared');showSharedPage('question');};box.append(button);}
        }
        section.append(box);
      }
      panel.append(section);
    }
    materialGroup('实际引用的记忆',mats.cited,row.citation_checked?'没有实际引用的记忆。':'未确认实际引用；不能把候选当成依据。');
    materialGroup('查询返回的候选记忆',mats.searched,row.search_performed?'没有返回候选记忆。':'未执行检索或未记录候选。');
    if(row.kind==='shared')materialGroup('已读取／提供给回答阶段的资料',mats.read,'本轮未记录读取资料；检索返回不代表已读取。');
    const steps=add('details',''),heading=document.createElement('summary');heading.textContent='阶段耗时与工具调用（展开查看）';steps.append(heading);
    if(failure?.stage){const p=document.createElement('p');p.textContent='失败阶段：'+(auditStageNames[failure.stage]||failure.stage);steps.append(p);}
    for(const step of row.steps||[]){const p=document.createElement('p');p.textContent=(auditStageNames[step.stage]||step.stage)+' · '+({running:'进行中',done:'完成',error:'失败',cancelled:'取消'}[step.status]||'未知')+' · '+auditDuration(step.elapsed_ms);steps.append(p);}
    for(const tool of row.tools||[]){const p=document.createElement('p');p.textContent=(auditToolNames[tool.name]||'工具')+' · '+({done:'完成',failed:'失败',blocked:'已拦截',cancelled:'已取消',running:'处理中'}[tool.status]||'状态未知')+' · '+auditDuration(tool.elapsed_ms);steps.append(p);}
    const note=document.createElement('p');note.textContent='阶段可能嵌套，不能相加当作总耗时。'+(row.usage?auditUsage(row.usage):'模型调用与Token用量未记录；费用未知。');steps.append(note);controls();
  }
  function schedule(){clearTimeout(timer);timer=null;if(visible()&&sharedState.asking&&sharedState.token&&sharedState.space)timer=setTimeout(()=>load(),1200);}
  async function load(){
    if(loading)return;
    if(!sharedState.token||!sharedState.space){sharedRows=[];render();return;}
    const current=context(),version=generation;loading=true;controls();
    try{
      const result=await sharedApi('/diagnostics/overview',{space_id:sharedState.space.id});
      if(!current()||version!==generation)return;
      sharedRows=(result.available?result.records:[]).map(row=>({...row,kind:'shared'}));
      if(!result.available)sharedNotice='共同记忆诊断记录暂不可用，请以操作结果和修改历史为准。';else if(!latestUnavailable)sharedNotice='';render();
    }catch(error){if(current()&&version===generation){sharedRows=[];sharedNotice='读取诊断失败，请检查登录和空间权限后刷新。';render();}}
    finally{if(current()&&version===generation){loading=false;controls();schedule();}}
  }
  function noteShared(result,query){
    if(result?.diagnostic?.available===false){latestUnavailable=true;sharedNotice='本轮诊断未能完整保存；请以原操作结果和修改历史为准，不据此重复保存。';render();}
    else if(result?.diagnostic?.available){latestUnavailable=false;sharedNotice='';}
    if(result?.diagnostic?.available&&result.diagnostic.id){questions.set(result.diagnostic.id,query.slice(0,160));while(questions.size>50)questions.delete(questions.keys().next().value);selected=result.diagnostic.id;}
    if(visible())load();
  }
  function startLocal(id,kind,question){
    localRows.unshift({id,kind,question:question.slice(0,160),started:Date.now()/1000,start:performance.now(),status:'running',reason:'ok',citation_checked:false,models:[]});
    localRows.splice(20);selected=id;render();
  }
  function sourcesLocal(id,result,ordinary=false){
    const row=localRows.find(r=>r.id===id);if(!row)return;
    const sources=Array.isArray(result.sources)?result.sources:[],ids=result.answer_source_ids;
    const checked=!ordinary&&['model_reported','linked_record','program_selected'].includes(result.source_usage)&&Array.isArray(ids)&&new Set(ids).size===ids.length&&ids.every(id=>sources.some(s=>s.id===id));
    const cited=new Set(checked?ids:[]);
    row.materials={searched:sources.map(s=>({...s,available:true})),read:ordinary?[]:sources.map(s=>({...s,available:true})),cited:sources.filter(s=>cited.has(s.id)).map(s=>({...s,available:true}))};
    row.search_performed=!!result.profile;row.citation_checked=checked;row.citation_status=result.source_usage;render();
  }
  function finishLocal(id,status='done'){
    const row=localRows.find(r=>r.id===id);if(!row)return;
    if(row.status!=='running')return;
    row.status=status;row.elapsed_ms=Math.max(0,Math.round(performance.now()-row.start));
    if(status==='failed'&&!row.failure)row.reason='network';if(status==='cancelled')row.reason='cancelled';render();
  }
  function traceLocal(id,data){
    const row=localRows.find(r=>r.id===id);if(!row||!data.available)return;
    row.models=data.models||[];row.usage=data.usage;row.steps=data.steps||[];row.failure=['asr','speaker','tts'].includes(data.failure?.stage)?null:data.failure||null;
    if(row.failure&&row.status==='done'){row.status=row.failure.code==='cancelled'?'cancelled':'failed';row.reason=row.failure.code;}
    render();
  }
  $('overviewRefresh').onclick=load;$('overviewScope').onchange=render;$('overviewErrors').onchange=render;
  $('overviewClearLocal').onclick=clearLocal;
  render();return {render,load,schedule,clearShared,clearLocal,noteShared,startLocal,sourcesLocal,finishLocal,traceLocal};
})();
