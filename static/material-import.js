// Material drafts never enter personal conversation history or browser storage.
const materialState={sources:[],candidates:[],plan:null,generation:0,controller:null,turn:null,running:false};
function materialMessage(text){$('materialStatus').textContent=text;}
function materialInvalidate(){
  const old=materialState.plan;materialState.plan=null;
  if(old&&sharedState.token&&sharedState.space)sharedApi('/import/discard',{space_id:sharedState.space.id,token:old.token}).catch(()=>{});
  $('materialReview').hidden=true;$('materialReviewItems').replaceChildren();$('materialReviewSummary').textContent='';
  $('materialConsent').checked=false;$('materialConflictConsent').checked=false;
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
}
function materialAudience(){
  const chosen=new Set([...$('materialAudience').querySelectorAll('input:checked')].map(x=>x.value));
  $('materialAudience').replaceChildren();$('materialAudience').hidden=$('materialVisibility').value!=='selected';
  for(const member of sharedState.members){
    const label=document.createElement('label'),box=document.createElement('input'),span=document.createElement('span');
    box.type='checkbox';box.value=member.id;
    const implicit=member.role==='owner'||member.id===sharedState.account?.id;
    box.checked=implicit||chosen.has(member.id);box.disabled=implicit;box.dataset.implicit=implicit?'true':'false';
    span.textContent=member.name+(implicit?'（始终可见）':'');box.onchange=()=>{materialInvalidate();materialControls();};
    label.append(box,span);$('materialAudience').append(label);
  }
}
function materialPermissionsChanged(){
  materialStop();materialInvalidate();
  if(sharedState.space?.role==='reader'){materialReset();materialMessage('当前为只读成员，未导入的资料已清空。');}
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
  catch(error){if(active())materialMessage(error.name==='AbortError'?'已停止，未完成的处理结果已丢弃。':error.message);}
  finally{if(active()){materialState.running=false;sharedState.busy=false;materialState.controller=null;materialState.turn=null;sharedControls();}}
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
  if(materialState.sources.length>=10){materialMessage('每批最多 10 份资料。');return;}
  materialState.sources.push({name:'粘贴资料 '+(materialState.sources.length+1),text:'',warnings:[]});
  materialSourcesChanged();materialRenderSources();
};
$('materialFiles').onchange=()=>materialRun(async(request,active)=>{
  const files=Array.from($('materialFiles').files||[]);$('materialFiles').value='';
  if(files.length+materialState.sources.length>10)throw new Error('每批最多 10 份资料，请减少文件数量。');
  materialSourcesChanged();
  for(const file of files){
    materialMessage('正在读取「'+file.name+'」…首次图片识别可能需要稍等。');
    if(file.size>4_000_000)throw new Error(file.name+' 超过 4 MB。此前成功读取的资料已保留。');
    const encoded=await new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败。'));reader.readAsDataURL(file);
    });
    if(!active())return;
    const result=await request('read',{name:file.name,base64:encoded});
    if(materialState.sources.reduce((n,s)=>n+s.text.length,0)+result.text.length>30000)throw new Error('本批原文超过 30000 字；这份未加入，请分批处理。');
    materialState.sources.push(result);materialRenderSources();
  }
  materialMessage('读取完成。请对照原件核对原文，再生成候选记忆。');materialControls();
});
$('materialMode').onchange=()=>{materialInvalidate();$('materialRemoteConsent').checked=false;materialControls();};
$('materialOrganize').onclick=()=>materialRun(async request=>{
  materialInvalidate();
  const mode=$('materialMode').value;
  if(mode==='ai'){
    const info=await request('capabilities');
    $('materialModelInfo').textContent='当前整理模型：'+info.model+'（'+(info.remote?'远端服务，原文会发送到该服务':'本机服务')+'）。';
    if(info.remote&&!$('materialRemoteConsent').checked)throw new Error('请先勾选同意发送原文到远端模型，再点击生成。');
  }
  materialMessage(mode==='ai'?'AI 正在逐段整理，长资料可能需要几分钟…':'正在按原文段落生成…');
  const result=await request('organize',{mode,sources:materialState.sources.map(({name,text})=>({name,text})),allow_remote:$('materialRemoteConsent').checked});
  materialDiscardDrafts();
  materialState.candidates=result.candidates.map(c=>({...c,selected:true,status:'pending',status_reason:'',source_note:'资料导入 · '+c.source_name}));
  materialRenderCandidates();materialAudience();materialMessage(result.candidates.length?result.notice:'没有提炼到候选事实，请检查原文或改用按段落整理。');
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
    row.textContent=p.content+'\n可见：'+(p.visibility==='all'?'全体成员（含未来成员）；当前：':'')+item.audience.join('、')+
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
  if(!plan||!$('materialConsent').checked||(plan.conflicts.length&&!$('materialConflictConsent').checked))return;
  // A confirmation already accepted by the server cannot be undone by cancelling the response.
  $('materialStop').hidden=true;
  const result=await request('confirm',{token:plan.token,confirmed:true,acknowledge_conflicts:$('materialConflictConsent').checked});
  materialDiscardDrafts();materialState.plan=null;materialState.sources=[];materialState.candidates=[];
  materialInvalidate();materialRenderSources();materialRenderCandidates();
  await sharedRefresh();$('materialBatchName').value='';materialMessage('成功导入 '+result.imported+' 条共同记忆；批次「'+result.batch_name+'」。可在上方刷新导入批次，查看本批记忆、来源和撤销预览。');
});
$('materialBatchName').oninput=()=>{materialInvalidate();materialControls();};
$('materialBack').onclick=()=>{materialInvalidate();materialControls();};
$('materialConsent').onchange=materialControls;$('materialConflictConsent').onchange=materialControls;
$('materialStop').onclick=()=>{materialStop();sharedControls();materialMessage('已停止。当前读取／整理结果不会自动导入。');};
$('materialClear').onclick=()=>{materialReset();materialAudience();materialMessage('本批资料已清空，正式记忆未改变。');};
materialControls();
