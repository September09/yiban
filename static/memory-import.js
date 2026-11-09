// Imported text is previewed as data. Only explicit confirmation writes to SQLite.
let memoryImportPlan=null, memoryImportVersion=0;
function memoryImportControls(){
  const locked=memoryLocked();
  $('memoryImportFile').disabled=!activeProfile||locked;
  $('previewMemoryImport').disabled=!activeProfile||locked||!$('memoryImportFile').files?.length;
  $('cancelMemoryImport').disabled=locked;
  $('memoryImportConflictConsent').disabled=locked||!memoryImportPlan?.requires_conflict_confirmation;
  $('memoryImportConsent').disabled=locked||!memoryImportPlan?.can_import;
  $('confirmMemoryImport').disabled=locked||!memoryImportPlan?.can_import||memoryImportPlan?.target.id!==activeProfile||!$('memoryImportConsent').checked||(!!memoryImportPlan?.requires_conflict_confirmation&&!$('memoryImportConflictConsent').checked);
}
function resetMemoryImport(clearFile=true){
  memoryImportVersion++;memoryImportPlan=null;
  if(clearFile)$('memoryImportFile').value='';
  $('memoryImportConflictConsent').checked=false;$('memoryImportConflictLabel').hidden=true;
  $('memoryImportConsent').checked=false;$('memoryImportPreview').hidden=true;
  $('memoryImportItems').replaceChildren();$('memoryImportSummary').textContent='';
  $('memoryImportStatus').textContent=activeProfile?'请选择文件，然后预览。':'请选择目标用户和文件。';
  memoryImportControls();
}
async function previewMemoryImport(){
  if(!activeProfile||memoryLocked())return;
  const file=$('memoryImportFile').files?.[0];if(!file)return;
  resetMemoryImport(false);const identity=activeProfile,version=memoryImportVersion;
  memoryBusy=true;personalControls();$('memoryImportStatus').textContent='正在校验文件并检查重复…';
  try{
    if(file.size>2*1024*1024)throw new Error('文件不能超过 2 MB。');
    let importDocument;
    try{importDocument=JSON.parse(await file.text());}catch{throw new Error('无法读取 JSON，请选择忆伴导出的文字记忆文件。');}
    if(version!==memoryImportVersion||identity!==activeProfile)return;
    const plan=await(await api('/api/memory/import/preview',{profile_id:identity,document:importDocument})).json();
    if(version!==memoryImportVersion||identity!==activeProfile)return;
    memoryImportPlan=plan;$('memoryImportPreview').hidden=false;$('memoryImportConflictLabel').hidden=!plan.requires_conflict_confirmation;
    $('memoryImportSummary').textContent=`文件原档案：${plan.source_name} → 恢复到：${plan.target.name}。共 ${plan.total} 条：新增 ${plan.counts.new} 条，已有重复 ${plan.counts.existing} 条，文件内部重复 ${plan.counts.file_duplicate} 条。合并后 ${plan.result_count} / 200 条；可能冲突 ${plan.conflict_count||0} 条。`;
    const labels={new:'将新增',existing:'跳过：档案已有',file_duplicate:'跳过：文件内重复'};
    for(const item of plan.items){
      const row=document.createElement('li'),label=document.createElement('small'),content=document.createElement('span');
      label.textContent=labels[item.status];label.className='memory-meta';content.textContent=item.content;
      row.append(label,content);
      for(const conflict of item.conflicts||[]){const warning=document.createElement('small');warning.className='memory-meta';warning.textContent='可能冲突：'+conflict.content;row.append(warning);}$('memoryImportItems').append(row);
    }
    $('memoryImportStatus').textContent=plan.can_import?'尚未保存。请核对目标档案和内容，勾选确认后恢复；预览 10 分钟内有效。':plan.reason;
  }catch(error){if(version===memoryImportVersion)$('memoryImportStatus').textContent=error.message;}
  finally{memoryBusy=false;personalControls();}
}
async function confirmMemoryImport(){
  const plan=memoryImportPlan;
  if(memoryLocked()||!plan?.can_import||plan.target.id!==activeProfile||!$('memoryImportConsent').checked||(plan.requires_conflict_confirmation&&!$('memoryImportConflictConsent').checked))return;
  const identity=activeProfile,version=memoryImportVersion;
  memoryBusy=true;personalControls();$('memoryImportStatus').textContent='正在恢复…';
  try{
    const result=await(await api('/api/memory/import/confirm',{profile_id:identity,token:plan.token,confirmed:true,acknowledge_conflicts:$('memoryImportConflictConsent').checked})).json();
    if(version!==memoryImportVersion||identity!==activeProfile)return;
    resetMemoryImport();await refreshMemories();
    if(identity===activeProfile){$('memoryImportStatus').textContent=`恢复完成，新增 ${result.imported} 条，当前共 ${result.total} 条。`;notice('文字记忆已合并保存，已有记忆和声纹保留。');}
  }catch(error){
    if(version===memoryImportVersion){memoryImportPlan=null;$('memoryImportConsent').checked=false;$('memoryImportStatus').textContent=error.message+' 请重新预览，确认最新结果后再恢复。';}
  }finally{memoryBusy=false;personalControls();}
}
$('memoryImportFile').onchange=()=>resetMemoryImport(false);
$('previewMemoryImport').onclick=previewMemoryImport;
$('confirmMemoryImport').onclick=confirmMemoryImport;
$('cancelMemoryImport').onclick=()=>{if(!memoryLocked())resetMemoryImport();};
$('memoryImportConsent').onchange=memoryImportControls;
$('memoryImportConflictConsent').onchange=memoryImportControls;
memoryImportControls();
