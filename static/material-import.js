// Material drafts never enter personal conversation history or browser storage.
const materialState={sources:[],candidates:[],plan:null,generation:0,controller:null,turn:null,running:false};
// 表单很长，提示必须同时出现在按钮旁（#materialStatus / #materialConfirmStatus），否则用户会以为“点了没反应”。
function materialMessage(text,error=false){
  const node=$('materialStatus');
  if(node){node.textContent=text||'';if(node.classList&&node.classList.toggle)node.classList.toggle('material-notice-error',!!error&&!!text);}
  const extra=$('materialConfirmStatus');
  if(extra){
    extra.textContent=text||'';extra.hidden=!text||$('materialReview').hidden;
    if(extra.classList&&extra.classList.toggle)extra.classList.toggle('material-notice-error',!!error&&!!text);
  }
  const target=error&&!$('materialReview').hidden?extra:node;
  if(text&&error&&target&&typeof target.scrollIntoView==='function')target.scrollIntoView({block:'nearest'});
}
function materialInvalidate(){
  const old=materialState.plan;materialState.plan=null;
  if(old&&sharedState.token&&sharedState.space)sharedApi('/import/discard',{space_id:sharedState.space.id,token:old.token}).catch(()=>{});
  $('materialReview').hidden=true;$('materialReviewItems').replaceChildren();$('materialReviewSummary').textContent='';
  $('materialConsent').checked=false;$('materialConflictConsent').checked=false;$('materialConfirmStatus').hidden=true;
}
function materialStop(){
  materialState.generation++;
  materialState.controller?.abort();materialState.controller=null;
  if(materialState.turn)fetch('/api/turn/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({turn_id:materialState.turn})}).catch(()=>{});
  materialState.turn=null;
  if(materialState.running)sharedState.busy=false;
  materialState.running=false;materialControls();
}
function materialDiscardDrafts(){
  if(!sharedState.token||!sharedState.space)return;
  const drafts=new Set(materialState.candidates.map(c=>c.evidence_ref?.draft).filter(Boolean));
  for(const draft of drafts)sharedApi('/import/discard',{space_id:sharedState.space.id,draft}).catch(()=>{});
}
function materialReset(){
  materialDiscardDrafts();$('materialKeepEvidence').checked=false;
  materialStop();materialInvalidate();materialState.sources=[];materialState.candidates=[];
  $('materialSources').replaceChildren();$('materialCandidates').replaceChildren();$('materialAudience').replaceChildren();
  $('materialBatchName').value='';$('materialFiles').value='';$('materialMode').value='paragraphs';$('materialVisibility').value='selected';$('materialPolicy').value='author';
  $('materialRemoteConsent').checked=false;$('materialModelInfo').textContent='选择 AI 时会检查当前模型。';
  materialMessage('');materialControls();
}
function materialControls(){
  const space=sharedState.space,locked=sharedState.busy||sharedState.asking||(typeof sharedVoiceActive==='function'&&sharedVoiceActive());
  $('materialEditor').disabled=!space||space.role==='reader'||locked||!!materialState.plan;
  $('materialDestination').textContent=space?'本批归属：'+(space.kind==='team'?'团队':'家庭')+'「'+space.name+'」；导入者：'+(sharedState.account?.name||'')+'。':'请先登录空间账号并选择空间。';
  $('materialStop').hidden=!materialState.running;
  $('materialClear').disabled=locked;
  $('materialConfirm').disabled=locked||!materialState.plan||!$('materialConsent').checked||(!!materialState.plan?.conflicts.length&&!$('materialConflictConsent').checked);
  $('materialConsent').disabled=locked;$('materialConflictConsent').disabled=locked;$('materialBack').disabled=locked;
  $('materialOrganize').disabled=!materialState.sources.length;$('materialPreview').disabled=!materialState.candidates.some(x=>x.selected);
  // 按钮为什么点不了，要在按钮上方直接写清楚，不能只靠禁用状态。
  const selected=materialState.candidates.filter(x=>x.selected).length;let hint='';
  if(!space)hint='请先登录空间账号并选择空间。';
  else if(space.role==='reader')hint='当前是只读成员，不能导入资料。';
  else if(!materialState.sources.length)hint='先添加或读取至少一份资料。';
  else if(!selected)hint='点「生成候选记忆」，再勾选要导入的条目。';
  else if(!materialState.plan)hint='已勾选 '+selected+' 条：点「查看最终导入预览」核对后再确认导入。';
  else if(!$('materialConsent').checked)hint='预览已生成：勾选下方「我已核对…」再点「确认导入」。';
  else if(materialState.plan.conflicts.length&&!$('materialConflictConsent').checked)hint='本批有疑似冲突：请同时勾选冲突确认项。';
  else hint='可以点「确认导入」写入共同记忆。';
  $('materialStepHint').textContent=hint;
}
function materialAudienceHint(){
  const node=$('materialAudienceHint');if(!node)return;
  let hint;
  if($('materialVisibility').value==='all')hint='本批记忆全体成员可见，包括以后加入的成员。';
  else {
    const chosen=[...$('materialAudience').querySelectorAll('input')].filter(box=>box.checked&&box.dataset.implicit!=='true').length;
    hint=chosen?('本批已选 '+chosen+' 位成员可见；作者和管理员始终可见。')
      :sharedState.members.length>1
        ?'注意：当前选了 0 位成员，导入后只有你（作者）和空间管理员能看到，其他成员在记忆库和问答里都读不到。要让全班都能问，请改选「全体成员」。'
        :'注意：当前空间只有你一位成员，「指定成员」等于只有你自己能看到。要让以后加入的成员也能看到，请改选「全体成员」。';
  }
  node.textContent=hint;node.classList.toggle('audience-warning',hint.startsWith('注意：'));
}
function materialAudience(){
  const chosen=new Set([...$('materialAudience').querySelectorAll('input:checked')].map(x=>x.value));
  $('materialAudience').replaceChildren();$('materialAudience').hidden=$('materialVisibility').value!=='selected';
  for(const member of sharedState.members){
    const label=document.createElement('label'),box=document.createElement('input'),span=document.createElement('span');
    box.type='checkbox';box.value=member.id;
    const implicit=member.role==='owner'||member.id===sharedState.account?.id;
    box.checked=implicit||chosen.has(member.id);box.disabled=implicit;box.dataset.implicit=implicit?'true':'false';
    span.textContent=member.name+(implicit?'（始终可见）':'');box.onchange=()=>{materialInvalidate();materialAudienceHint();materialControls();};
    label.append(box,span);$('materialAudience').append(label);
  }
  materialAudienceHint();
}
function materialPermissionsChanged(){
  const hadPlan=!!materialState.plan;
  materialStop();materialInvalidate();
  if(sharedState.space?.role==='reader'){materialReset();materialMessage('当前为只读成员，未导入的资料已清空。',true);}
  else if(hadPlan)materialMessage('空间内容或权限已更新，之前的导入预览已失效：请重新点「查看最终导入预览」再确认导入。本批资料仍在页面上。',true);
  materialAudience();materialControls();
}
async function materialRun(work){
  if(!sharedState.space||sharedState.space.role==='reader'||sharedState.busy||sharedState.asking||sharedVoiceActive())return;
  const epoch=sharedState.epoch,generation=++materialState.generation,space=sharedState.space.id;
  materialState.running=true;sharedState.busy=true;materialState.controller=new AbortController();materialState.turn=crypto.randomUUID();sharedControls();
  const active=()=>epoch===sharedState.epoch&&generation===materialState.generation&&space===sharedState.space?.id;
  const request=async(path,data={})=>{
    if(!active())throw new Error('操作已取消。');
    const result=await sharedApi('/import/'+path,{...data,space_id:space,turn_id:materialState.turn},materialState.controller.signal);
    if(!active())throw new Error('操作已取消。');
    return result;
  };
  try{await work(request,active);}
  catch(error){if(active())materialMessage(error.name==='AbortError'?'已停止，未完成的处理结果已丢弃。':error.message,error.name!=='AbortError');}
  finally{
    materialState.running=false;materialState.controller=null;materialState.turn=null;
    if(active()){sharedState.busy=false;sharedControls();}
  }
}
function materialSourcesChanged(){
  materialDiscardDrafts();materialInvalidate();materialState.candidates=[];$('materialCandidates').replaceChildren();materialControls();
}
function materialRenderSources(){
  $('materialSources').replaceChildren();
  materialState.sources.forEach((source,index)=>{
    const card=document.createElement('section');card.className='material-card';
    const name=document.createElement('input');name.value=source.name;name.maxLength=160;name.setAttribute('aria-label','资料 '+(index+1)+' 名称');
    name.oninput=()=>{source.name=name.value;materialSourcesChanged();};
    const text=document.createElement('textarea');text.value=source.text;text.rows=7;text.maxLength=30000;text.setAttribute('aria-label','资料 '+(index+1)+' 原文');
    text.oninput=()=>{source.text=text.value;materialSourcesChanged();};
    const warnings=document.createElement('p');warnings.className='feature-help';warnings.textContent=source.warnings.join(' ');
    const remove=document.createElement('button');remove.type='button';remove.textContent='移除这份资料';remove.className='text-button';
    remove.onclick=()=>{materialState.sources.splice(index,1);materialSourcesChanged();materialRenderSources();};
    card.append(name,text,warnings,remove);$('materialSources').append(card);
  });
}
function materialRenderCandidates(){
  $('materialCandidates').replaceChildren();
  materialState.candidates.forEach((candidate,index)=>{
    const card=document.createElement('section');card.className='material-card';
    const label=document.createElement('label'),box=document.createElement('input'),title=document.createElement('span');
    box.type='checkbox';box.checked=candidate.selected;title.textContent='导入第 '+(index+1)+' 条';
    box.onchange=()=>{candidate.selected=box.checked;materialInvalidate();materialControls();};label.append(box,title);
    const text=document.createElement('textarea');text.rows=3;text.maxLength=1200;text.value=candidate.content;text.setAttribute('aria-label','候选 '+(index+1)+' 内容');
    text.oninput=()=>{candidate.content=text.value;materialInvalidate();};
    const evidence=document.createElement('details'),summary=document.createElement('summary'),quote=document.createElement('p');
    summary.textContent='核对原文摘录 · '+candidate.source_name;quote.textContent=candidate.evidence;quote.className='material-evidence';evidence.append(summary,quote);
    const date=document.createElement('input');date.type='date';date.value=candidate.event_date;date.setAttribute('aria-label','候选 '+(index+1)+' 事件日期（可选）');
    date.oninput=()=>{candidate.event_date=date.value;materialInvalidate();};
    const source=document.createElement('input');source.value=candidate.source_note;source.maxLength=200;source.setAttribute('aria-label','候选 '+(index+1)+' 来源说明');
    source.oninput=()=>{candidate.source_note=source.value;materialInvalidate();};
    const status=document.createElement('select');status.setAttribute('aria-label','候选 '+(index+1)+' 记忆状态');
    for(const [value,name] of Object.entries(sharedStatusNames)){const option=document.createElement('option');option.value=value;option.textContent=name;status.append(option);}
    status.value=candidate.status||'pending';status.onchange=()=>{candidate.status=status.value;materialInvalidate();};
    const reason=document.createElement('input');reason.value=candidate.status_reason||'';reason.maxLength=200;reason.placeholder='标记有效或过时时，请填写依据或原因';reason.setAttribute('aria-label','候选 '+(index+1)+' 状态说明');reason.oninput=()=>{candidate.status_reason=reason.value;materialInvalidate();};
    card.append(label,text,status,reason,evidence,document.createTextNode('事件日期（可选）'),date,document.createTextNode('保存的来源说明'),source);$('materialCandidates').append(card);
  });
  materialControls();
}
$('materialAddText').onclick=()=>{
  if(materialState.sources.length>=60){materialMessage('本批最多 60 份资料（含大文件自动分块）。',true);return;}
  materialState.sources.push({name:'粘贴资料 '+(materialState.sources.length+1),text:'',warnings:[]});
  materialSourcesChanged();materialRenderSources();
};
$('materialFiles').onchange=()=>materialRun(async(request,active)=>{
  const files=Array.from($('materialFiles').files||[]);$('materialFiles').value='';
  if(files.length+materialState.sources.length>60)throw new Error('本批最多 60 份资料（含大文件自动分块），请减少文件数量。');
  materialSourcesChanged();
  for(const file of files){
    materialMessage('正在读取「'+file.name+'」…首次图片识别可能需要稍等。');
    if(file.size>4_000_000)throw new Error(file.name+' 超过 4 MB：请在「预览」里导出更小的 PDF／图片，或直接把文字粘贴进来。此前成功读取的资料已保留。');
    const encoded=await new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败。'));reader.readAsDataURL(file);
    });
    if(!active())return;
    const result=await request('read',{name:file.name,base64:encoded});
    // 大文件由服务端按行边界自动分块；每块都是一份普通资料，整理时会自动分批。
    if(result.parts&&result.parts.length>1){
      for(const part of result.parts)materialState.sources.push({name:part.name,text:part.text,warnings:[]});
      materialRenderSources();
      materialMessage('「'+file.name+'」共 '+result.total_chars+' 字，已自动分成 '+result.parts.length+' 块（每块最多 12000 字）。整理时会按每批 30000 字自动分批，请逐块核对原文。');
    }else{
      materialState.sources.push(result);materialRenderSources();
      materialMessage('读取完成。请对照原件核对原文，再生成候选记忆。');
    }
  }
  materialControls();
});
$('materialMode').onchange=()=>{materialInvalidate();$('materialRemoteConsent').checked=false;materialControls();};
// 大文件已按块加入列表：这里按「每批 ≤10 份且合计 ≤30000 字」分轮，交给 organize 逐轮执行。
function materialOrganizeRounds(sources){
  const rounds=[];let batch=[],size=0;
  for(const source of sources){
    if(source.text.length>30000)throw new Error('「'+source.name+'」超过 30000 字：请删除后重新读取（会自动分块），或手动拆分。');
    if(batch.length&&(batch.length>=10||size+source.text.length>30000)){rounds.push(batch);batch=[];size=0;}
    batch.push(source);size+=source.text.length;
  }
  if(batch.length)rounds.push(batch);
  return rounds;
}
$('materialOrganize').onclick=()=>materialRun(async request=>{
  materialInvalidate();
  const mode=$('materialMode').value;
  if(mode==='ai'){
    const info=await request('capabilities');
    $('materialModelInfo').textContent='当前整理模型：'+info.model+'（'+(info.remote?'远端服务，原文会发送到该服务':'本机服务')+'）。';
    if(info.remote&&!$('materialRemoteConsent').checked)throw new Error('请先勾选同意发送原文到远端模型，再点击生成。');
  }
  const rounds=materialOrganizeRounds(materialState.sources);
  materialDiscardDrafts();
  const collected=[];let truncated=false;
  materialState.candidates=collected;
  for(let i=0;i<rounds.length;i++){
    materialMessage(rounds.length>1
      ?`正在整理第 ${i+1}/${rounds.length} 批（每批最多 30000 字）…${mode==='ai'?'长资料可能需要几分钟，可随时点「停止读取／整理」。':''}`
      :(mode==='ai'?'AI 正在逐段整理，长资料可能需要几分钟…':'正在按原文段落生成…'));
    try{
      const result=await request('organize',{mode,sources:rounds[i].map(({name,text})=>({name,text})),allow_remote:$('materialRemoteConsent').checked});
      for(const candidate of result.candidates){
        if(collected.length>=200){truncated=true;break;}
        collected.push({...candidate,selected:true,status:'pending',status_reason:'',source_note:'资料导入 · '+candidate.source_name});
      }
    }catch(error){
      // 中途某批失败：已生成的候选保留下来，仍然可以勾选导入，不必从头再来。
      materialRenderCandidates();materialAudience();
      if(collected.length){materialMessage('第 '+(i+1)+' 批整理失败：'+error.message+' 已生成的 '+collected.length+' 条仍可勾选导入；其余资料仍在列表，可稍后重试。',true);}
      else materialMessage(error.name==='AbortError'?'已停止，未完成的处理结果已丢弃。':error.message,error.name!=='AbortError');
      return;
    }
    if(truncated)break;
  }
  materialRenderCandidates();materialAudience();
  if(!collected.length){materialMessage('没有提炼到候选事实，请检查原文或改用按段落整理。',true);return;}
  materialMessage(truncated
    ?'候选已达 200 条上限：本批只保留前 200 条，剩余资料未整理。建议先核对导入这 200 条；导入成功后本页资料会清空，请再重新读取剩余部分整理。'
    :'已生成 '+collected.length+' 条候选'+(rounds.length>1?'（'+rounds.length+' 批资料全部整理完成）':'')+'。请逐条核对原文摘录，再勾选要导入的条目。');
});
$('materialVisibility').onchange=()=>{materialInvalidate();materialAudience();materialControls();};
$('materialKeepEvidence').onchange=()=>{materialInvalidate();materialControls();};
$('materialPolicy').onchange=()=>{materialInvalidate();materialControls();};
$('materialPreview').onclick=()=>materialRun(async request=>{
  materialInvalidate();
  const viewers=[...$('materialAudience').querySelectorAll('input:checked')].filter(x=>x.dataset.implicit!=='true').map(x=>x.value);
  const items=materialState.candidates.filter(c=>c.selected).map(c=>({content:c.content,event_date:c.event_date,source_note:c.source_note,status:c.status||'pending',status_reason:c.status_reason||'',evidence_ref:c.evidence_ref,
    visibility:$('materialVisibility').value,edit_policy:$('materialPolicy').value,viewers}));
  const plan=await request('preview',{items,keep_evidence:$('materialKeepEvidence').checked,batch_name:$('materialBatchName').value,mode:$('materialMode').value});materialState.plan=plan;
  $('materialReview').hidden=false;$('materialConflictLabel').hidden=!plan.conflicts.length;
  $('materialReviewSummary').textContent='批次：'+plan.batch_name+'；归属：「'+plan.space_name+'」；导入者：'+plan.actor_name+'。将新增 '+plan.items.length+' 条，跳过 '+plan.skipped+' 条重复内容。预览 10 分钟内有效。';
  for(const item of plan.items){
    const p=item.payload,row=document.createElement('li');
    const none=item.payload.visibility==='selected'&&!(item.payload.viewers||[]).length;
    row.textContent=p.content+'\n可见：'+(p.visibility==='all'?'全体成员（含未来成员）；当前：':'')+item.audience.join('、')+(none?'（未选择其他成员：导入后只有作者和管理员能看到）':'')+
      '\n修改权限：'+(p.edit_policy==='author'?'作者和管理员':'可见的编辑成员')+'\n日期：'+(p.event_date||'未指定')+'\n来源：'+(p.source_note||'未指定');
    row.textContent+='\n状态：'+sharedStatusNames[p.status]+' · '+(p.status_reason||'未填写说明');
    row.textContent+=item.evidence?'\n保留完整文字：仅作者和管理员可见。以下摘录随记忆可见范围开放：\n'+item.evidence.quotes.map(q=>q.location+'：'+q.text).join('\n'):'\n不保留原文依据。';
    $('materialReviewItems').append(row);
  }
  for(const conflict of plan.conflicts){
    const row=document.createElement('li');row.textContent='可能冲突：'+conflict.content+'\n请对照：'+conflict.matches.map(x=>x.content).join('；');$('materialReviewItems').append(row);
  }
  materialMessage('尚未写入。请核对最终预览并勾选确认。');
});
$('materialConfirm').onclick=()=>materialRun(async request=>{
  const plan=materialState.plan;
  if(!plan){materialMessage('还没有可导入的预览：请先点「查看最终导入预览」核对内容。',true);return;}
  if(!$('materialConsent').checked||(plan.conflicts.length&&!$('materialConflictConsent').checked)){materialMessage('请先勾选下方的确认项，再点「确认导入」。',true);return;}
  // A confirmation already accepted by the server cannot be undone by cancelling the response.
  $('materialStop').hidden=true;
  const result=await request('confirm',{token:plan.token,confirmed:true,acknowledge_conflicts:$('materialConflictConsent').checked});
  materialDiscardDrafts();materialState.plan=null;materialState.sources=[];materialState.candidates=[];
  materialInvalidate();materialRenderSources();materialRenderCandidates();
  await sharedRefresh();$('materialBatchName').value='';
  materialMessage('成功导入 '+result.imported+' 条共同记忆；批次「'+result.batch_name+'」。本批已列在上方「资料来源与导入批次」，可直接查看本批记忆、来源和撤销预览。');
  // 导入成功后批次列表要自动刷新并展开本批，用户才能马上确认“资料确实在”。
  if(typeof batchState!=='undefined')batchState.stale=true;
  setTimeout(()=>{
    if(typeof sharedBatchesAutoLoad==='function')sharedBatchesAutoLoad();
    if(result.batch_id&&typeof batchOpen==='function')batchOpen(result.batch_id);
  },0);
});
$('materialBatchName').oninput=()=>{materialInvalidate();materialControls();};
$('materialBack').onclick=()=>{materialInvalidate();materialControls();};
$('materialConsent').onchange=materialControls;$('materialConflictConsent').onchange=materialControls;
$('materialStop').onclick=()=>{materialStop();sharedControls();materialMessage('已停止。当前读取／整理结果不会自动导入。');};
$('materialClear').onclick=()=>{materialReset();materialAudience();materialMessage('本批资料已清空，正式记忆未改变。');};
materialControls();
