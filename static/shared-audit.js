// Own-account operation metadata only. Never persist it in browser storage.
const sharedAuditState={records:[],version:0,loading:false,refreshPending:false,timer:null};
const auditStatusNames={running:'处理中',done:'已完成',needs_confirmation:'等待核对确认',needs_selection:'等待选择记忆',needs_clarification:'需要澄清',needs_details:'需要补充内容',saved:'已保存',cancelled:'已取消',failed:'处理失败',timeout:'已超时',rejected:'请求未通过',interrupted:'服务重启中断'};
const auditActionNames={ask:'提出请求',read:'只读查询',select:'选择修改目标',confirm:'确认保存',cancel:'取消草稿'};
const auditStageNames={routing:'理解操作意图',memory:'检索记忆',access:'检查访问权限',model_wait:'等待模型首段输出',model:'生成模型结果',draft:'生成保存预览',commit:'核对并保存',cancel:'撤销草稿'};
const auditToolNames={search_memory:'查询记忆',read_material:'读取资料',get_current_time:'获取当前时间',save_memory_preview:'新增记忆预览',replace_memory_preview:'替代安排预览',update_memory_preview:'修改记忆预览',invalid_request:'校验工具请求'};
const auditReasonNames={ok:'本次请求已处理。',waiting_confirmation:'当时已生成预览，等待确认；后续结果请查看相同关联编号的记录。',choose_target:'请按回答中的提示选择目标或补充信息。',need_details:'请补充需要记录的事实或原记忆关键词。',model_or_tool:'请查看失败步骤，检查模型服务后重试。',timeout:'本轮已停止，请缩短问题后重试。',cancelled:'本次操作已取消。',permission:'当前账号或权限已变化，请重新登录或选择空间。',stale:'草稿、版本或权限已变化，请重新生成预览并核对。',invalid_request:'请求内容不完整或尚未确认，请核对后重试。',unexpected:'处理发生异常，请检查服务状态后重试。',result_unrecorded:'未能完整记录本次结果；请以操作提示、记忆列表及修改历史为准。',service_restarted:'服务重启前未记录最终结果；请先核对记忆列表及修改历史，避免重复保存。'};
const auditFailureStates=new Set(['failed','timeout','rejected','interrupted']);
function auditDuration(value){return Number.isInteger(value)&&value>=0?(value/1000).toFixed(2)+' 秒':'未知';}
function auditUsage(usage={}){
  if(!usage.model_calls)return '模型调用：0 次（本次无需模型）；费用未计算。';
  if(!usage.reported_calls)return `模型调用：${usage.model_calls} 次；Token 用量未知（服务未返回）；费用未知。`;
  return `模型调用：${usage.model_calls} 次；${usage.complete?'完整':'部分'}用量（${usage.reported_calls}/${usage.model_calls} 次已返回）：输入 ${usage.input_tokens}、输出 ${usage.output_tokens} Token；费用未计算。`;
}
function sharedAuditClear(){
  sharedAuditState.version++;sharedAuditState.records=[];sharedAuditState.loading=false;sharedAuditState.refreshPending=false;
  clearTimeout(sharedAuditState.timer);sharedAuditState.timer=null;
  $('sharedAuditList').replaceChildren();$('sharedAuditInfo').textContent='请选择空间后刷新。';$('sharedAuditFailures').checked=false;
}
function sharedAuditControls(){
  const allowed=!!(sharedState.token&&sharedState.space);
  $('sharedAuditRefresh').disabled=!allowed||sharedAuditState.loading;
  $('sharedAuditExport').disabled=!allowed||sharedAuditState.loading;
  $('sharedAuditFailures').disabled=!allowed;
  if(!allowed){clearTimeout(sharedAuditState.timer);sharedAuditState.timer=null;}
}
function sharedAuditRender(){
  const opened=new Set([...$('sharedAuditList').children].filter(item=>item.open).map(item=>item.dataset.id));
  const rows=sharedAuditState.records.filter(row=>!$('sharedAuditFailures').checked||auditFailureStates.has(row.status));
  $('sharedAuditList').replaceChildren();
  const total=sharedAuditState.records.length,failed=sharedAuditState.records.filter(row=>auditFailureStates.has(row.status)).length;
  $('sharedAuditInfo').textContent=`当前空间 · 本账号最近 ${total} 次请求，其中异常 ${failed} 次。最多展示50次；这是处理记录，不代表回答准确率。`;
  if(!rows.length){const empty=document.createElement('p');empty.textContent=total?'当前筛选下没有记录。':'还没有工具操作记录。开启“自动选择记忆工具”并发送文字后可查看。';$('sharedAuditList').append(empty);return;}
  for(const row of rows){
    const item=document.createElement('details'),title=document.createElement('summary');item.className='agent-audit-row';item.dataset.id=row.id;item.open=opened.has(row.id);
    title.textContent=`${new Date(row.started*1000).toLocaleString('zh-CN')} · ${auditActionNames[row.action]||'操作'} · ${auditStatusNames[row.status]||'未知状态'} · ${auditDuration(row.elapsed_ms)}`;
    item.append(title);
    const info=document.createElement('p');info.className='feature-help';info.textContent=`关联编号：${String(row.operation||'').slice(0,8)}（相同编号对应同一组预览、选择和确认操作）`;
    const reason=document.createElement('p');reason.textContent=row.status==='running'?'正在处理，可刷新查看当前步骤。':auditReasonNames[row.reason]||'请查看步骤记录。';
    const usage=document.createElement('p');usage.className='feature-help';usage.textContent=auditUsage(row.usage);
    item.append(info,reason,usage);
    if(row.source_count){const sources=document.createElement('p');sources.className='feature-help';sources.textContent=`返回资料 ${row.source_count} 条；${row.citation_status==='model_reported'?'已收到模型声明的引用，正确性仍需核对':row.citation_status==='unavailable'?'引用情况未知':'请在原回答查看引用分组'}。`;item.append(sources);}
    const steps=document.createElement('ol');
    for(const step of row.steps||[]){const line=document.createElement('li');line.textContent=`${auditStageNames[step.stage]||'处理步骤'} · ${{running:'进行中',done:'完成',error:'失败',cancelled:'取消'}[step.status]||'未知'} · ${auditDuration(step.elapsed_ms)}`;steps.append(line);}
    if(steps.children.length)item.append(steps);
    if(row.tools?.length){const tools=document.createElement('p');tools.className='feature-help';tools.textContent='工具：'+row.tools.map(tool=>`${auditToolNames[tool.name]||'已校验工具'}（${{done:'完成',failed:'失败',blocked:'已拦截',cancelled:'取消',running:'处理中'}[tool.status]||'未知'}，${auditDuration(tool.elapsed_ms)}）`).join(' → ');item.append(tools);}
    $('sharedAuditList').append(item);
  }
}
function sharedAuditSchedule(){
  clearTimeout(sharedAuditState.timer);sharedAuditState.timer=null;
  if($('sharedAuditPanel').open&&sharedState.token&&sharedState.space&&sharedState.asking)sharedAuditState.timer=setTimeout(()=>sharedAuditLoad(),1000);
}
async function sharedAuditLoad(download=false){
  if(!sharedState.token||!sharedState.space||sharedAuditState.loading)return;
  const version=++sharedAuditState.version,epoch=sharedState.epoch,space=sharedState.space.id,token=sharedState.token;
  const current=()=>version===sharedAuditState.version&&epoch===sharedState.epoch&&space===sharedState.space?.id&&token===sharedState.token;
  sharedAuditState.loading=true;sharedAuditControls();
  try{
    const data=await sharedApi('/agent/records',{space_id:space});if(!current())return;
    sharedAuditState.records=data.available?data.records:[];
    if(!data.available){$('sharedAuditList').replaceChildren();$('sharedAuditInfo').textContent='工具记录暂不可用；记忆保存结果请以原操作提示和修改历史为准。';return;}
    sharedAuditRender();
    if(download){
      const blob=new Blob([JSON.stringify({exported_at:new Date().toISOString(),retention_days:data.retention_days,records:data.records},null,2)],{type:'application/json'});
      const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='yiban-my-tool-records.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  }catch(error){if(current()){sharedAuditState.records=[];$('sharedAuditList').replaceChildren();$('sharedAuditInfo').textContent='读取失败，请确认登录和空间权限后刷新。';}}
  finally{if(current()){sharedAuditState.loading=false;sharedAuditControls();if(sharedAuditState.refreshPending&&$('sharedAuditPanel').open){sharedAuditState.refreshPending=false;sharedAuditLoad();}else sharedAuditSchedule();}}
}
function sharedAuditObserve(diagnostic){
  if(!diagnostic.available){$('sharedAuditInfo').textContent='本次操作记录未能保存；请以操作结果和记忆修改历史为准。';return;}
  if($('sharedAuditPanel').open){if(sharedAuditState.loading)sharedAuditState.refreshPending=true;else sharedAuditLoad();}
}
$('sharedAuditPanel').ontoggle=()=>{if($('sharedAuditPanel').open)sharedAuditLoad();else{clearTimeout(sharedAuditState.timer);sharedAuditState.timer=null;}};
$('sharedAuditRefresh').onclick=()=>sharedAuditLoad();
$('sharedAuditExport').onclick=()=>sharedAuditLoad(true);
$('sharedAuditFailures').onchange=sharedAuditRender;
sharedAuditControls();
