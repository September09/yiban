// Profile-scoped state and conflict previews. All changes go through the existing confirmation panel.
let personalConflictGeneration=0,personalConflictControls=[];
function personalStateControls(){
  const locked=memoryLocked();
  for(const id of ['memoryState','memoryStateReason','memoryEventDate','memoryStatusFilter','personalConflictRefresh'])$(id).disabled=locked||!activeProfile;
  for(const set of personalConflictControls){set.choice.disabled=locked;set.reason.disabled=locked;set.preview.disabled=locked;}
}
function personalConflictClear(){
  personalConflictGeneration++;personalConflictControls=[];$('personalConflictItems').replaceChildren();
  $('personalConflictInfo').textContent=activeProfile?'正在检查当前档案…':'请选择个人档案。';
  if(memoryReviewPlan?.kind==='conflict')resetMemoryReview();
}
function personalConflictRender(data){
  personalConflictClear();const identity=activeProfile,version=personalConflictGeneration;
  if(!identity)return;
  if(!data){$('personalConflictInfo').textContent='本次没有返回冲突检查结果，可点击刷新；不能据此认定没有冲突。';return;}
  $('personalConflictInfo').textContent='发现 '+data.total+' 组疑似冲突'+(data.limited?'；先显示前 '+data.pairs.length+' 组':'')+'。未过时记录才参与；有限规则可能漏检。';
  for(const pair of data.pairs){
    const box=document.createElement('article');box.className='overview-memory';
    const add=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;box.append(node);return node;};
    add('h4',pair.field+' · 疑似不同说法');add('p',pair.reason);
    for(const [label,row] of [['记录一',pair.left],['记录二',pair.right]]){add('p',label+'：'+row.content);add('small',personalStateText(row));}
    const choice=document.createElement('select');choice.setAttribute('aria-label','选择两条记录的处理方式');
    for(const [value,label] of [['','请选择处理方式'],[pair.left.id,'记录一有效，记录二过时'],[pair.right.id,'记录二有效，记录一过时'],['both','两条均待核实并存（仍会提醒）']]){const option=document.createElement('option');option.value=value;option.textContent=label;choice.append(option);}choice.value='';
    const reason=document.createElement('input');reason.maxLength=500;reason.placeholder='填写核对说明，例如：已与本人确认当前居住城市';reason.setAttribute('aria-label','冲突核对说明');
    const preview=document.createElement('button');preview.className='secondary-button';preview.textContent='预览两条状态变更';
    const status=document.createElement('p');status.setAttribute('role','status');
    const invalidate=()=>{if(memoryReviewPlan?.kind==='conflict')resetMemoryReview();status.textContent='选择有变化，请重新预览；尚未修改。';};choice.onchange=invalidate;reason.oninput=invalidate;
    preview.onclick=async()=>{
      if(memoryLocked()||identity!==activeProfile)return;
      if(!choice.value||!reason.value.trim()){status.textContent='请先选择处理方式并填写核对说明。';return;}
      resetMemoryReview();memoryBusy=true;personalControls();status.textContent='正在生成对照，尚未修改…';
      try{
        const plan=await(await api('/api/memory/conflicts/preview',{profile_id:identity,left_id:pair.left.id,right_id:pair.right.id,action:choice.value==='both'?'keep_both':'prefer',preferred_id:choice.value==='both'?null:choice.value,reason:reason.value})).json();
        if(identity!==activeProfile||version!==personalConflictGeneration){api('/api/memory/review/cancel',{profile_id:identity,token:plan.token}).catch(()=>{});return;}
        showMemoryReview(plan);status.textContent='请在上方核对预览，勾选确认后才修改。';
      }catch(error){if(identity===activeProfile&&version===personalConflictGeneration)status.textContent=error.message;}
      finally{memoryBusy=false;personalControls();}
    };
    box.append(choice,reason,preview,status);$('personalConflictItems').append(box);personalConflictControls.push({choice,reason,preview});
  }
  personalStateControls();
}
$('personalConflictRefresh').onclick=()=>{if(!memoryLocked()){resetMemoryReview();refreshMemories();}};
$('memoryStatusFilter').onchange=renderMemories;
for(const id of ['memoryState','memoryStateReason'])$(id).oninput=()=>{if(memoryReviewPlan)resetMemoryReview();};
$('memoryEventDate').oninput=()=>{if(memoryEdit){$('memoryState').value='pending';$('memoryStateReason').value='';}if(memoryReviewPlan)resetMemoryReview();};
$('personalIncludeOutdated').onchange=()=>{if(!personalQueryState.running){personalMemoryCancel();personalMemoryControls();}};
if(!$('memoryState').value)$('memoryState').value='pending';if(!$('memoryStatusFilter').value)$('memoryStatusFilter').value='all';
personalConflictClear();personalStateControls();
