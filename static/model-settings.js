// Model controls are device-local; comparison sources/answers never enter browser storage.
const modelSettingsUI=(()=>{
  let settings=null,preview=null,run=null,generation=0,working=false;
  const names={local:'本机',cloud:'云端'};
  const checkNames={credentials:'密钥配置',catalog:'模型目录',completion:'实际文字回复'};
  const checkStates={available:'已读取',not_required:'无需密钥',listed:'目录中存在',not_listed:'目录未列出',unavailable:'无法确认',passed:'通过',failed:'失败'};
  const editing=()=>['modelsProfile','modelsBackend','modelsURL','modelsName','modelsVision','modelsThinking','modelsKeyEnv','modelsActiveSelect','modelsRemoteConsent','modelsSave','modelsProbe','modelsReload','modelsCompareScope','modelsCompareQuery','modelsIncludeOutdated','modelsComparePreview'];
  function controls(){for(const id of editing())$(id).disabled=working||!settings;$('modelsCompareConfirm').disabled=working||!preview||!$('modelsCompareConsent').checked;$('modelsCompareCancel').disabled=working;$('modelsCompareStop').hidden=!run;}
  function clear(){preview=null;$('modelsCompareConsent').checked=false;$('modelsCompareReview').hidden=true;$('modelsComparePreviewText').replaceChildren();$('modelsCompareResult').replaceChildren();$('modelsCompareMessage').textContent='';}
  function invalidate(){if(preview)api('compare/cancel',{preview_token:preview.preview_token}).catch(()=>{});generation++;clear();if(run){fetch('/api/turn/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({turn_id:run.id})}).catch(()=>{});run.abort.abort();run=null;}working=false;controls();}
  function leave(){invalidate();$('modelsRemoteConsent').checked=false;}
  function context(){const token=sharedState.token,epoch=sharedState.epoch,space=sharedState.space?.id;return()=>token===sharedState.token&&epoch===sharedState.epoch&&space===sharedState.space?.id;}
  async function api(path,data,signal){
    const headers={'Content-Type':'application/json'};if(sharedState.token)headers.Authorization='Bearer '+sharedState.token;
    const response=await fetch('/api/models/'+path,{method:'POST',headers,body:JSON.stringify(data),signal});const result=await response.json();
    if(!response.ok)throw new Error(result.error||'模型操作失败，请重试。');return result;
  }
  function fill(){
    if(!settings)return;
    const cfg=settings.profiles[$('modelsProfile').value];
    $('modelsBackend').value=cfg.backend;$('modelsURL').value=cfg.base_url;$('modelsName').value=cfg.model;
    $('modelsVision').value=cfg.vision_model;$('modelsKeyEnv').value=cfg.api_key_env;
    $('modelsThinking').value=cfg.thinking_type==='disabled'?'zhipu':cfg.enable_thinking===false?'bailian':cfg.thinking_type||cfg.enable_thinking!==null&&cfg.enable_thinking!==undefined?'keep':'none';
    $('modelsKeyInfo').textContent=cfg.key_available?'服务进程已读取此环境变量；是否有效需检测实际回复。':'服务进程尚未读取此环境变量。本机 Ollama 通常无需密钥；云端需配置后重启助手。';
    $('modelsActiveSelect').value=settings.active;
    $('modelsActive').textContent='当前：'+names[settings.active]+' · '+settings.profiles[settings.active].model+'。云端发送目标：'+settings.profiles.cloud.base_url+(settings.warning?'；'+settings.warning:'');
    $('modelsSpaceInfo').textContent=sharedState.token&&sharedState.space?'当前空间：'+sharedState.space.name+'；只检索本账号可见且全体成员可见的记录。':'共同记忆对比需先到「共同记忆」登录并选择空间。';controls();
  }
  async function load(){
    if(working)return;
    const version=++generation;
    try{const response=await fetch('/api/models/settings');if(!response.ok)throw new Error('无法加载模型设置。');const result=await response.json();if(version!==generation)return;settings=result;clear();fill();}
    catch(error){if(version===generation)$('modelsMessage').textContent=error.message;}
  }
  async function work(target,task){
    if(working||!settings)return;
    const version=++generation,current=context();working=true;const id=crypto.randomUUID(),abort=new AbortController();run={id,abort};controls();$(target).textContent='正在处理，请稍候…';
    try{const result=await task(id,abort.signal);if(version===generation&&current())return result;}
    catch(error){if(version===generation)$(target).textContent=error.name==='AbortError'?'本次测试已停止。':error.message;}
    finally{if(version===generation){run=null;working=false;controls();}}
  }
  function text(parent,tag,value){const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;}
  function materials(parent,sources){
    if(!sources.length){text(parent,'p','没有附带记忆资料。');return;}
    for(const source of sources){const item=text(parent,'article','');item.className='overview-memory';text(item,'p','资料 '+source.reference+'：'+source.content);text(item,'small','状态：'+(sharedStatusNames[source.status]||'未知')+'；日期：'+(source.event_date||'未填写')+'；来源：'+(source.source_note||source.evidence_source_name||'未记录'));}
  }
  $('modelsReload').onclick=load;
  $('modelsProfile').onchange=()=>{invalidate();$('modelsRemoteConsent').checked=false;fill();};
  for(const id of ['modelsCompareScope','modelsCompareQuery','modelsIncludeOutdated'])$(id).oninput=()=>{if(!working)clear();};
  $('modelsCompareConsent').onchange=controls;
  $('modelsCompareCancel').onclick=()=>{invalidate();$('modelsCompareMessage').textContent='已取消预览，没有调用模型。';};
  $('modelsCompareStop').onclick=()=>{invalidate();$('modelsCompareMessage').textContent='已停止本次测试，迟到结果不会显示。';};
  $('modelsSave').onclick=async()=>{
    const profile=$('modelsProfile').value,cfg={...settings.profiles[profile]};delete cfg.key_available;
    Object.assign(cfg,{backend:$('modelsBackend').value,base_url:$('modelsURL').value.trim(),model:$('modelsName').value.trim(),vision_model:$('modelsVision').value.trim(),api_key_env:$('modelsKeyEnv').value.trim()});
    const thinking=$('modelsThinking').value;if(thinking!=='keep'){cfg.enable_thinking=thinking==='bailian'?false:null;cfg.thinking_type=thinking==='zhipu'?'disabled':null;}if(cfg.backend==='ollama'||cfg.vision_model)cfg.status_check='models';
    const result=await work('modelsMessage',(id,signal)=>api('save',{profile,config:cfg,active:$('modelsActiveSelect').value,revision:settings.revision,allow_remote:$('modelsRemoteConsent').checked},signal));
    if(result){settings=result;clear();fill();$('modelsRemoteConsent').checked=false;$('modelsMessage').textContent='已保存。新请求使用新设置，正在进行的对话保持原模型。';if(typeof status==='function')status();}
  };
  $('modelsProbe').onclick=async()=>{
    $('modelsProbeResult').replaceChildren();
    const result=await work('modelsMessage',(id,signal)=>api('probe',{profile:$('modelsProfile').value,allow_remote:$('modelsRemoteConsent').checked,turn_id:id},signal));
    if(!result)return;
    $('modelsMessage').textContent=(result.ok?'实际文字回复检测通过。':'检测未通过，请查看下方原因。')+(result.stale?' 设置已变化，请重新检测。':'');
    for(const check of result.checks)text($('modelsProbeResult'),'p',(checkNames[check.name]||check.name)+'：'+(checkStates[check.status]||check.status)+(check.http_status?' · HTTP '+check.http_status:'')+(check.message?' · '+check.message:''));
    if(result.elapsed_ms!==undefined)text($('modelsProbeResult'),'p','总耗时：'+result.elapsed_ms+' 毫秒；模型：'+result.model);
  };
  $('modelsComparePreview').onclick=async()=>{
    clear();const result=await work('modelsCompareMessage',(id,signal)=>api('compare/preview',{scope:$('modelsCompareScope').value,space_id:sharedState.space?.id,query:$('modelsCompareQuery').value,include_outdated:$('modelsIncludeOutdated').checked,turn_id:id},signal));
    if(!result)return;preview=result;const box=$('modelsComparePreviewText');text(box,'h3','本次将发送的内容');text(box,'p',result.notice);text(box,'p','问题：'+result.query);text(box,'p','本机：'+result.models.local+' · '+result.destinations.local);text(box,'p','云端：'+result.models.cloud+' · '+result.destinations.cloud);materials(box,result.sources);$('modelsCompareReview').hidden=false;$('modelsCompareMessage').textContent='预览有效10分钟，尚未调用回答模型；核对后勾选确认。';controls();
  };
  $('modelsCompareConfirm').onclick=async()=>{
    if(!preview||!$('modelsCompareConsent').checked)return;
    const plan=preview;preview=null;$('modelsCompareReview').hidden=true;
    const result=await work('modelsCompareMessage',(id,signal)=>api('compare/confirm',{preview_token:plan.preview_token,confirmed:true,allow_remote:true,turn_id:id},signal));
    if(!result)return;const box=$('modelsCompareResult');box.replaceChildren();$('modelsCompareMessage').textContent=result.notice;materials(box,result.sources);
    for(const item of result.results){const card=text(box,'article','');card.className='overview-memory';text(card,'h3',names[item.profile]+' · '+item.model);text(card,'p',item.ok?item.answer:(item.error?.message||'未完成回答'));text(card,'small','耗时：'+item.elapsed_ms+' 毫秒；首段文字：'+(item.first_text_ms===undefined?'未知':item.first_text_ms+' 毫秒')+(item.usage?'；输出Token：'+item.usage.output_tokens:'；Token用量／费用未知'));
      if(result.scope==='shared'&&item.ok){const ids=new Set(item.answer_source_ids||[]);text(card,'p','模型声明且编号已校验的引用：'+result.sources.filter(s=>ids.has(s.id)).map(s=>'资料 '+s.reference).join('、')+'；仍需人工核对原文是否支持结论。');}
      if(item.error?.http_status)text(card,'p','HTTP '+item.error.http_status);
    }
  };
  controls();return {load,leave,invalidate};
})();
