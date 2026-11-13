// Archives stay in this page's RAM until the operator confirms a new-space restore.
const backupRoles={owner:'管理员',editor:'编辑成员',reader:'只读成员',historical:'历史账号（不加入成员）'};
const backupState={document:null,accounts:[],plan:null,running:false,version:0};
function backupMessage(text){$('sharedBackupStatus').textContent=text;}
function backupDiscardPlan(){
  const token=backupState.plan?.token,auth=sharedState.token;
  if(token&&auth)fetch('/api/shared/backup/cancel',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+auth},body:JSON.stringify({token})}).catch(()=>{});
  backupState.plan=null;
  $('sharedBackupReview').hidden=true;
  $('sharedBackupReviewBody').replaceChildren();
  $('sharedBackupConfirmData').checked=false;$('sharedBackupConfirmPermissions').checked=false;
}
function sharedBackupClear(clearFile=true){
  backupDiscardPlan();backupState.version++;backupState.document=null;backupState.accounts=[];backupState.running=false;
  if(clearFile)$('sharedBackupFile').value='';
  $('sharedBackupMapping').replaceChildren();$('sharedBackupName').value='';
  $('sharedBackupExportConsent').checked=false;
  backupMessage('管理员可导出当前空间。恢复会创建新空间，请核对账号对应及权限。');
}
function backupLocked(){
  return sharedState.busy||sharedState.asking||backupState.running||
    (typeof sharedVoiceActive==='function'&&sharedVoiceActive());
}
function sharedBackupControls(){
  const logged=!!sharedState.token,locked=backupLocked();
  $('sharedBackupExportConsent').disabled=!logged||sharedState.space?.role!=='owner'||locked;
  $('sharedBackupExport').disabled=$('sharedBackupExportConsent').disabled||!$('sharedBackupExportConsent').checked;
  $('sharedBackupFile').disabled=!logged||locked;
  $('sharedBackupInspect').disabled=!logged||locked||!$('sharedBackupFile').files?.length;
  $('sharedBackupName').disabled=!backupState.document||locked;
  $('sharedBackupPreview').disabled=!backupState.document||!logged||locked;
  $('sharedBackupConfirmData').disabled=locked||!backupState.plan?.token;
  $('sharedBackupConfirmPermissions').disabled=locked||!backupState.plan?.token;
  $('sharedBackupConfirm').disabled=locked||!backupState.plan?.token||!$('sharedBackupConfirmData').checked||!$('sharedBackupConfirmPermissions').checked;
  $('sharedBackupCancel').disabled=locked;
  for(const account of backupState.accounts)account.input.disabled=locked||account.owner;
}
async function backupRun(work){
  if(!sharedState.token||backupLocked())return;
  const epoch=sharedState.epoch,version=backupState.version,auth=sharedState.token;
  const current=()=>epoch===sharedState.epoch&&version===backupState.version&&auth===sharedState.token;
  backupState.running=true;sharedState.busy=true;sharedControls();
  try{await work(current);}
  catch(error){if(current())backupMessage(error.message);}
  finally{if(current()){backupState.running=false;sharedState.busy=false;sharedControls();}}
}
function backupCounts(counts){
  return `记忆 ${counts.memories} 条，历史 ${counts.history} 条，原文 ${counts.sources} 份，依据 ${counts.evidence} 条，替代关联 ${counts.replacements} 条，成员 ${counts.members} 人，导入批次 ${counts.batches||0} 个。`;
}
$('sharedBackupExport').onclick=()=>backupRun(async current=>{
  if(!sharedState.space||sharedState.space.role!=='owner'||!$('sharedBackupExportConsent').checked)return;
  const result=await sharedApi('/backup/export',{space_id:sharedState.space.id,confirmed:true});
  if(!current())return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(result.document)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='yiban-shared-backup-'+new Date().toISOString().slice(0,10)+'.json';link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  backupMessage('备份文件已生成，请妥善保存。文件包含完整原文、私密记忆和历史。');
});
$('sharedBackupInspect').onclick=()=>backupRun(async current=>{
  const file=$('sharedBackupFile').files?.[0];
  if(!file||file.size>24000000)throw new Error('请选择不超过24 MB的共同记忆JSON备份。');
  let archive;
  try{archive=JSON.parse(await file.text());}catch{throw new Error('备份不是有效的JSON文件。');}
  if(!current())return;
  const result=await sharedApi('/backup/inspect',{document:archive});
  if(!current())return;
  backupDiscardPlan();backupState.document=archive;backupState.accounts=[];$('sharedBackupMapping').replaceChildren();
  $('sharedBackupName').value=(result.name+'（恢复）').slice(0,60);
  for(const account of result.accounts){
    const label=document.createElement('label'),input=document.createElement('input');
    label.textContent=account.name+' · '+backupRoles[account.role]+' → 本机账号';
    input.value=account.target_name;input.maxLength=40;input.setAttribute('aria-label','备份账号 '+account.name+' 对应的本机账号');
    input.oninput=()=>{backupDiscardPlan();sharedBackupControls();};label.append(input);$('sharedBackupMapping').append(label);
    backupState.accounts.push({...account,input});
  }
  backupMessage('文件读取完成。'+backupCounts(result.counts)+'原管理员对应当前账号，其他账号需已在本机注册，历史账号不会自动加入空间。');
});
$('sharedBackupPreview').onclick=()=>backupRun(async current=>{
  backupDiscardPlan();
  const mapping=Object.fromEntries(backupState.accounts.map(account=>[account.id,account.input.value]));
  const result=await sharedApi('/backup/preview',{document:backupState.document,name:$('sharedBackupName').value,mapping});
  if(!current())return;
  if(result.duplicate){backupMessage('这份备份及账号对应已恢复到「'+result.duplicate.name+'」，无需重复恢复。可在空间列表打开。');return;}
  backupState.plan=result;$('sharedBackupReview').hidden=false;
  const panel=$('sharedBackupReviewBody');
  function line(text){const row=document.createElement('p');row.textContent=text;panel.append(row);}
  line('将创建'+(result.kind==='family'?'家庭':'团队')+'空间「'+result.name+'」。'+backupCounts(result.counts));
  for(const account of result.mapping)line(account.source+' → '+account.target+' · '+backupRoles[account.role]);
  for(const memory of result.memories){
    line(memory.content);
    line('作者：'+memory.author+'；状态：'+sharedStatusNames[memory.status]+'；事件日期：'+(memory.event_date||'未填写'));
    if(memory.status_reason)line('状态说明：'+memory.status_reason);
    line('可见：'+(memory.visibility==='all'?'全体成员（含以后加入的成员）':memory.viewers.join('、'))+'；修改权限：'+(memory.edit_policy==='author'?'作者和管理员':'可见的编辑成员'));
  }
  function disclosure(title,text){
    const details=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('pre');
    summary.textContent=title;body.textContent=text;body.style.whiteSpace='pre-wrap';details.append(summary,body);panel.append(details);
  }
  for(const source of backupState.document.sources)disclosure('原文 · '+source.name,source.text);
  for(const batch of backupState.document.batches||[])disclosure('导入批次 · '+batch.name,'导入时间：'+batch.created+'；在库记忆 '+batch.items.length+' 条。\n'+batch.items.map(i=>'来源：'+(i.source_name||'未提供')+'；原记忆编号：'+i.memory).join('\n'));
  const names=Object.fromEntries(backupState.document.accounts.map(a=>[a.id,a.name]));
  for(const h of backupState.document.history)disclosure('修改历史 · '+h.changed+' · '+names[h.actor],
    '修改前：'+(h.before?.content||'首次创建')+'\n修改后：'+h.after.content+'\n状态：'+sharedStatusNames[h.after.status||'pending']);
  backupMessage('尚未恢复。请核对新空间、账号对应、角色和资料可见范围。预览10分钟有效。');
});
$('sharedBackupConfirm').onclick=()=>backupRun(async current=>{
  const plan=backupState.plan;
  if(!plan?.token||!$('sharedBackupConfirmData').checked||!$('sharedBackupConfirmPermissions').checked)return;
  const result=await sharedApi('/backup/confirm',{token:plan.token,confirmed:true,permissions_confirmed:true});
  if(!current())return;
  await sharedRefresh();
  if(!current())return;
  const target=sharedState.spaces.find(space=>space.id===result.space_id);
  if(!target){backupMessage('恢复已完成，请刷新空间列表。');return;}
  sharedClearSpace();sharedState.space=target;sharedRenderSpaces();await sharedLoadCurrent();
  showWorkspacePage('shared');showSharedPage('library');
  sharedMessage('已恢复到新空间「'+target.name+'」。可查看记忆、历史、原文和新旧安排追溯。');
});
$('sharedBackupFile').onchange=()=>{sharedBackupClear(false);sharedBackupControls();};
$('sharedBackupName').oninput=()=>{backupDiscardPlan();sharedBackupControls();};
$('sharedBackupCancel').onclick=()=>{if(!backupLocked()){sharedBackupClear();sharedBackupControls();}};
$('sharedBackupExportConsent').onchange=sharedBackupControls;
$('sharedBackupConfirmData').onchange=sharedBackupControls;
$('sharedBackupConfirmPermissions').onchange=sharedBackupControls;
sharedBackupClear();sharedBackupControls();
