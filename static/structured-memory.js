// New memories use the authenticated space account; files and previews stay in this page.
// Review hints are derived from the current authorized record, never class-specific files.
function structuredReviewReasons(row){
  const result=[],f=row.fact||{},time=row.event?.time,reason=row.verification_reason||'';
  const add=(id,label,priority)=>result.push({id,label,priority});
  if(row.verification==='disputed')add('other','已有争议说明',110);
  if(f.predicate==='quantity')add('quantity','核对数量、单位与统计口径',100);
  if(f.predicate==='holds_role')add('role','核对届次、职务与有效时间',90);
  if(f.predicate==='score')add('score','核对对手、最终比分及胜负',85);
  if(/姓名|异字|署名|简称|同名|主持身份|人物身份/.test(reason))add('identity','核对姓名写法与人物归属',95);
  if(time&&row.event.kind!=='project'&&(row.type==='event'||f.predicate==='score')&&(time.basis==='inferred'||time.precision==='unknown'))add('date',time.basis==='inferred'?'活动日期含推定':'活动日期未载',60);
  if(/预告|阶段|计数|不另|次数|总决赛|分日/.test(reason))add('boundary','核对活动边界，避免重复计数',50);
  if(!result.length&&reason.includes('需核对：'))add('other','有单独核对提示',40);
  return result.sort((a,b)=>b.priority-a.priority);
}
const structuredMemory=(()=>{
  let mdImporter=null;
  const state={binding:null,document:null,plan:null,epoch:0,working:false,abort:null,rows:[],edit:null,focus:null,evidence:new Map(),selection:0,evidenceReady:false,evidenceAbort:null,editing:false,factInputs:{},eventInputs:{}};
  const names={event:'事件',fact:'事实',quote:'引语',reflection:'感受／总结',pending:'待核实',confirmed:'已确认',disputed:'有争议'};
  const scope=()=>$('v2Scope').value==='personal'?{scope_kind:'personal'}:{scope_kind:'team',space_id:$('v2Scope').value};
  const message=t=>{$('v2Status').textContent=t;$('v2DraftStatus').textContent=t;};
  function node(parent,tag,value){const e=document.createElement(tag);if(value!==undefined)e.textContent=value;parent.append(e);return e;}
  function invalidate(){
    const plan=state.plan;state.plan=null;$('v2Review').hidden=true;$('v2Consent').checked=false;$('v2WarningsConsent').checked=false;
    if(plan&&sharedState.token)request('discard',{token:plan.token}).catch(()=>{});
    controls();
  }
  function clear(){
    mdImporter?.clear();
    state.epoch++;state.abort?.abort();state.abort=null;state.working=false;state.document=null;state.rows=[];state.edit=null;state.plan=null;
    state.focus=null;state.selection++;state.evidenceAbort?.abort();state.evidenceAbort=null;state.evidence.clear();state.evidenceReady=false;state.editing=false;
    $('v2FocusList').replaceChildren();$('v2FocusEvidence').replaceChildren();$('v2FocusDetail').hidden=true;$('v2FocusCount').textContent='登录并选择范围后显示待核验问题。';
    $('v2FocusPanel').append($('v2Edit'));$('v2FactFields').replaceChildren();$('v2EventFields').replaceChildren();state.factInputs={};state.eventInputs={};
    for(const id of ['v2EditTitle','v2EditSummary','v2EditDetail','v2EditReason','v2EditFact','v2EventChanges','v2FocusTitle','v2FocusAnswer','v2FocusReason','v2FocusPosition']){$(id).value='';$(id).textContent='';}
    $('v2EditStatus').value='pending';$('v2EditArchived').checked=false;$('v2UseAdvancedFact').checked=false;$('v2UseAdvancedEvent').checked=false;
    $('v2FocusSearch').value='';$('v2FocusStatus').value='open';$('v2FocusCategory').value='all';
    $('v2Candidates').replaceChildren();$('v2Records').replaceChildren();$('v2Packet').value='';$('v2Original').value='';$('v2File').value='';$('v2DraftTitle').value='';$('v2DraftText').value='';
    $('v2ReviewItems').replaceChildren();$('v2Review').hidden=true;$('v2Edit').hidden=true;$('v2EventEdit').hidden=true;$('v2Consent').checked=false;$('v2WarningsConsent').checked=false;message('');
  }
  function controls(){
    const logged=!!sharedState.token,team=scope().scope_kind==='team',space=sharedState.spaces.find(s=>s.id===scope().space_id);
    $('v2Controls').disabled=!logged||state.working;
    $('v2Login').hidden=logged;$('v2Identity').textContent=logged?'当前空间账号：'+sharedState.account.name+'。个人范围只属于这个账号；团队范围沿用成员权限。':'请先登录空间账号；个人档案的名字不能代替登录。';
    $('v2Visibility').disabled=!team||state.working;
    const draftLocked=!logged||state.working||(team&&space?.role==='reader');
    mdImporter?.controls(draftLocked);
    for(const id of ['v2DraftTitle','v2DraftText','v2Draft'])$(id).disabled=draftLocked;
    $('v2Preview').disabled=!state.document||state.working||(team&&space?.role==='reader');
    for(const id of ['v2FocusConfirm','v2FocusWrong'])$(id).disabled=!state.edit?.can_edit||!state.evidenceReady||state.working||state.editing||state.edit?.lifecycle==='archived';
    $('v2FocusPending').disabled=state.working;$('v2FocusModify').disabled=!state.edit?.can_edit||state.working;
    $('v2FocusNext').disabled=state.working;
    $('v2FocusRestore').hidden=state.edit?.lifecycle!=='archived';$('v2FocusRestore').disabled=!state.edit?.can_edit||state.working;
    $('v2FocusEvent').hidden=!state.edit?.event;$('v2FocusEvent').disabled=!state.edit?.can_edit||state.working;
    $('v2EventPreview').disabled=!state.edit?.can_edit||state.working;
    $('v2EditPreview').disabled=!state.edit?.can_edit||!state.evidenceReady||state.working;
    for(const id of ['v2EditTitle','v2EditSummary','v2EditDetail','v2EditStatus','v2EditReason','v2EditFact','v2EditArchived','v2UseAdvancedFact'])$(id).disabled=!state.edit?.can_edit||state.working;
    for(const input of Object.values(state.factInputs))input.disabled=!state.edit?.can_edit||state.working;
    $('v2Confirm').disabled=!state.plan||state.working||!$('v2Consent').checked||((state.plan.warnings||[]).length>0&&!$('v2WarningsConsent').checked);
  }
  function sync(){
    const old=$('v2Scope').value||'personal';
    const binding=[sharedState.token||'',sharedState.epoch,...sharedState.spaces.map(s=>s.id+':'+s.version+':'+s.role).sort()].join('|');
    if(state.binding!==binding){clear();state.binding=binding;
      $('v2Scope').replaceChildren();const p=node($('v2Scope'),'option','个人记忆'+(sharedState.account?' · '+sharedState.account.name:''));p.value='personal';
      for(const s of sharedState.spaces){const option=node($('v2Scope'),'option',s.name+'（团队）');option.value=s.id;}
      $('v2Scope').value=old==='personal'||sharedState.spaces.some(s=>s.id===old)?old:'personal';
    }
    controls();
  }
  async function request(path,data={},signal=null){
    const token=sharedState.token;
    const response=await fetch('/api/memories/'+path,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({...scope(),...data}),signal});
    const result=await response.json();
    if(!response.ok){if(response.status===401&&token===sharedState.token)sharedLock('登录已失效，请重新登录。');throw new Error(result.error||'记忆操作未完成。');}
    return result;
  }
  async function work(task){
    if(state.working||!sharedState.token)return;
    const epoch=state.epoch;state.working=true;state.abort=new AbortController();controls();
    const active=()=>epoch===state.epoch;
    try{await task(active,state.abort.signal);}catch(error){if(active()&&error.name!=='AbortError')message(error.message);}
    finally{if(active()){state.working=false;state.abort=null;controls();}}
  }
  function factSummary(f,rows=[]){
    const names=Object.fromEntries(rows.map(e=>[e.key,e.name]));
    let value=f.value;
    if(f.predicate==='score')value=(f.value.own??'未记载')+' 比 '+(f.value.opponent??'未记载')+'；'+({win:'获胜',loss:'失利',draw:'平局',unknown:'胜负待核对'}[f.value.result]);
    return (names[f.subject_key]?'主体：'+names[f.subject_key]+'；':'')+'记载：'+value+(f.unit?' '+f.unit:'')+
      (f.metric?'；口径：'+f.metric:'')+(f.term?'；届次：'+f.term:'')+(names[f.object_key]?'；相关方：'+names[f.object_key]:'')+
      (f.effective?.raw?'；有效时间：'+f.effective.raw:'');
  }
  function candidates(){
    $('v2Candidates').replaceChildren();
    if(!state.document)return;
    for(const r of state.document.records){
      const article=node($('v2Candidates'),'article');article.className='v2-memory-card';
      const label=node(article,'label'),box=node(label,'input');box.type='checkbox';box.value=r.key;box.checked=!(scope().scope_kind==='team'&&r.sensitivity==='personal');box.onchange=()=>{invalidate();controls();};
      node(label,'strong',r.title);node(article,'p',r.summary);if(r.detail)node(article,'p',r.detail);
      node(article,'small',names[r.type]+' · '+names[r.verification]+(r.sensitivity==='personal'?' · 私人内容，仅导入个人范围':'')+(r.participants.length?' · 参与者 '+r.participants.map(k=>state.document.entities.find(e=>e.key===k)?.name||k).join('、'):''));
      if(r.event_key){const event=state.document.events.find(e=>e.key===r.event_key);node(article,'p','事件：'+event.title+' · '+(event.time.raw||event.time.start||'日期未知')+' · '+({held:'已发生',planned:'计划',cancelled:'取消',unknown:'发生情况待核对'}[event.state]));}
      if(r.fact)node(article,'p',factSummary(r.fact,state.document.entities));
      const details=node(article,'details');node(details,'summary','查看依据');for(const e of r.evidence)node(details,'blockquote',e.text);
    }
    controls();
  }
  function setDocument(result){
    invalidate();state.document=result.document;$('v2Packet').value=JSON.stringify(result.document,null,2);candidates();
    message((result.warnings||[]).join('\n')||'候选已校验，尚未入库。请逐条核对，必要时展开下方候选文件调整后重新校验。');
  }
  async function loadFile(){
    const file=$('v2File').files?.[0];if(!file)return;
    await work(async(active,signal)=>{
      if(file.size>4_000_000)throw new Error('候选文件超过4MB，请分包。');
      const document=JSON.parse(await file.text());if(!active())return;
      const result=await request(document.schema==='yiban.memory-candidates.v2'?'validate':'convert',{document,original_text:$('v2Original').value||null},signal);
      if(active())setDocument(result);
    });
  }
  function showRevision(host,label,item,lifecycle){
    node(host,'h4',label);const card=node(host,'div');card.className='v2-revision-summary';
    node(card,'strong',item.title||'');
    if('summary' in item){
      node(card,'p',item.summary);if(item.detail)node(card,'p',item.detail);node(card,'p','核实状态：'+names[item.verification]);
      if(item.verification_reason)node(card,'p','核实说明：'+item.verification_reason);
      const f=item.fact,entities=state.edit?.entities||{};
      if(f){let value=f.value;
        if(f.predicate==='score')value='本方 '+(f.value.own??'未记载')+'：对手 '+(f.value.opponent??'未记载')+'；'+({win:'获胜',loss:'失利',draw:'平局',unknown:'胜负未记载'}[f.value.result])+'；对手：'+(entities[f.object_key]||'沿用原记录');
        node(card,'p','事实：'+value+(f.unit?' '+f.unit:'')+(f.metric?'；口径：'+f.metric:'')+(f.term?'；届次：'+f.term:''));
        if(f.effective?.raw)node(card,'p','有效时间：'+f.effective.raw);
      }
    }else{node(card,'p','活动时间：'+(item.time?.raw||item.time?.start||'未记载'));node(card,'p','时间范围：'+(item.time?.start||'未记载')+' 至 '+(item.time?.end||'未记载'));node(card,'p','发生情况：'+({held:'已发生',planned:'计划',cancelled:'取消',unknown:'待核对'}[item.state]||'待核对'));if(item.location)node(card,'p','地点：'+item.location);}
    if(lifecycle)node(card,'p','使用状态：'+(lifecycle==='archived'?'归档，退出当前查询':'使用中'));
  }
  function review(plan){
    $('v2ReviewHome').append($('v2Review'));state.plan=plan;$('v2Review').hidden=false;$('v2Consent').checked=false;$('v2WarningsConsent').checked=false;
    $('v2ReviewItems').replaceChildren();node($('v2ReviewItems'),'p','归属：'+plan.scope_name);
    if(plan.options)node($('v2ReviewItems'),'p','查看范围：'+(plan.scope_kind==='personal'?'仅自己':plan.options.visibility==='all'?'团队全体成员（包括以后加入的成员）':'作者与团队管理员')+'；修改：'+(plan.scope_kind==='personal'?'仅自己':'作者与管理员')+'。');
    if(plan.before){showRevision($('v2ReviewItems'),'修改前',plan.before,state.edit?.lifecycle);showRevision($('v2ReviewItems'),'修改后',plan.after,plan.lifecycle||state.edit?.lifecycle);}
    else for(const item of plan.items){
      node($('v2ReviewItems'),'h4',item.title+' · '+names[item.verification]);node($('v2ReviewItems'),'p',item.summary);if(item.detail)node($('v2ReviewItems'),'p',item.detail);
      if(item.participants.length)node($('v2ReviewItems'),'p','参与者：'+item.participants.map(k=>(plan.entities||[]).find(e=>e.key===k)?.name||k).join('、'));
      const event=(plan.events||[]).find(e=>e.key===item.event_key);if(event)showRevision($('v2ReviewItems'),'关联事件',event);
      if(item.fact)node($('v2ReviewItems'),'p',factSummary(item.fact,plan.entities||[]));
      if(item.verification_reason)node($('v2ReviewItems'),'p','核实说明：'+item.verification_reason);
      for(const e of item.evidence)node($('v2ReviewItems'),'blockquote',e.text);
    }
    if(plan.before&&plan.items)node($('v2ReviewItems'),'p','将影响 '+plan.items.length+' 条关联记忆：'+plan.items.map(r=>r.title).join('、'));
    for(const warning of plan.warnings||[])node($('v2ReviewItems'),'p','待核对：'+warning);
    $('v2WarningsLabel').hidden=!(plan.warnings||[]).length;controls();
  }
  async function refresh(){
    await work(async(active,signal)=>{const result=await request('list',{include_archived:true},signal);if(!active())return;state.rows=result.records;renderRecords();message(result.scope_name+'：'+result.records.length+'条可见记忆。');});
  }
  function renderRecords(){
    renderFocus();$('v2Records').replaceChildren();
    for(const row of state.rows.filter(r=>$('v2Archived').checked||r.lifecycle!=='archived')){
      const article=node($('v2Records'),'article');article.className='v2-memory-card';node(article,'h3',row.title);node(article,'p',row.content);
      node(article,'small',names[row.type]+' · '+names[row.verification]+' · '+(row.lifecycle==='archived'?'已归档':'使用中')+' · 版本'+row.version);
      if(row.fact)node(article,'pre',JSON.stringify(row.fact,null,2));
      const quote=node(article,'button','查看原文摘录');quote.type='button';quote.className='text-button';const evidence=node(article,'div');
      quote.onclick=()=>work(async(active,signal)=>{const result=await request('evidence',{id:row.id,expected_version:row.version},signal);if(!active())return;evidence.replaceChildren();for(const e of result.quotes){node(evidence,'small',e.source_name+(e.source_kind==='candidate'?'（候选文件，非原文）':''));node(evidence,'blockquote',e.text);}});
      const history=node(article,'button','查看修改历史');history.type='button';history.className='text-button';history.onclick=()=>work(async(active,signal)=>{const result=await request('history',{id:row.id},signal);if(active()){evidence.replaceChildren();for(const h of result.history){node(evidence,'small',h.created);const after=JSON.parse(h.after_json);node(evidence,'p',(after.record?.summary||'')+' · '+(after.lifecycle==='archived'?'归档':'保存'));}}});
      if(row.can_edit&&row.event){const eventEdit=node(article,'button','修正关联事件');eventEdit.type='button';eventEdit.className='text-button';eventEdit.onclick=()=>{selectFocus(row);openEventEditor(row);};}
      if(row.can_edit){const edit=node(article,'button','修改／核实');edit.type='button';edit.className='text-button';edit.onclick=()=>{selectFocus(row);$('v2FocusPanel').scrollIntoView?.({block:'start'});};}

    }
  }
  function focusRows(){
    const status=$('v2FocusStatus').value||'open',category=$('v2FocusCategory').value||'all',q=$('v2FocusSearch').value.trim().toLocaleLowerCase();
    return state.rows.map(row=>({row,reasons:structuredReviewReasons(row)})).filter(x=>(status==='archived'?x.row.lifecycle==='archived':x.row.lifecycle!=='archived'))
      .filter(x=>status==='archived'||status==='all'||(status==='confirmed'?x.row.verification==='confirmed':status==='disputed'?x.row.verification==='disputed':x.row.verification==='pending'))
      .filter(x=>category==='all'||x.reasons.some(r=>r.id===category))
      .filter(x=>!q||[x.row.title,x.row.summary,x.row.verification_reason,...Object.values(x.row.entities||{})].join(' ').toLocaleLowerCase().includes(q))
      .sort((a,b)=>(b.reasons[0]?.priority||0)-(a.reasons[0]?.priority||0)||a.row.title.localeCompare(b.row.title,'zh-CN'));
  }
  function renderFocus(){
    const items=focusRows(),all=state.rows.filter(r=>r.lifecycle!=='archived'),open=all.filter(r=>r.verification==='pending');
    $('v2FocusCount').textContent='待核验 '+open.length+' 条；已确认 '+all.filter(r=>r.verification==='confirmed').length+' 条；待修改 '+all.filter(r=>r.verification==='disputed').length+' 条。当前显示 '+items.length+' 条，已归档 '+state.rows.filter(r=>r.lifecycle==='archived').length+' 条。';
    $('v2FocusList').replaceChildren();
    for(const {row,reasons} of items){
      const button=node($('v2FocusList'),'button');button.type='button';button.className='v2-focus-item';button.dataset.id=row.id;
      button.setAttribute('aria-pressed',String(state.focus===row.id));node(button,'strong',reviewQuestion(row));node(button,'small',names[row.verification]+(reasons.length?' · '+reasons.map(r=>r.label).join('；'):''));
      button.onclick=()=>selectFocus(row);
    }
    const selected=items.find(x=>x.row.id===state.focus)||items[0];
    if(selected)selectFocus(selected.row);
    else{invalidate();state.focus=null;state.edit=null;state.selection++;state.evidenceAbort?.abort();state.evidenceReady=false;$('v2FocusDetail').hidden=true;$('v2Edit').hidden=true;$('v2FocusEvidence').replaceChildren();node($('v2FocusList'),'p','当前条件下没有待核验条目。可查看已确认、待修改或已归档的记忆。');controls();}
  }
  function selectFocus(row){
    invalidate();state.evidenceAbort?.abort();state.focus=row.id;state.edit=row;state.evidenceReady=false;const selection=++state.selection;
    $('v2EventEdit').hidden=true;$('v2FocusDetail').hidden=false;$('v2FocusEditorHost').append($('v2Edit'));state.editing=false;$('v2Edit').hidden=true;
    $('v2FocusTitle').textContent=reviewQuestion(row);$('v2FocusAnswer').textContent=reviewAnswer(row);$('v2FocusReason').textContent=(structuredReviewReasons(row).map(r=>r.label).join('；')||'请对照原文核对')+(row.verification_reason?'。原核对提示：'+row.verification_reason:'');
    const items=focusRows(),index=items.findIndex(x=>x.row.id===row.id);$('v2FocusPosition').textContent=index<0?'从全部记忆打开':`当前第 ${index+1} / ${items.length} 条`;
    for(const b of $('v2FocusList').querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.id===row.id));
    $('v2EditTitle').value=row.title;$('v2EditSummary').value=row.summary;$('v2EditDetail').value=row.detail;
    $('v2EditStatus').value=row.verification;$('v2EditReason').value=row.verification_reason;$('v2EditArchived').checked=row.lifecycle==='archived';
    $('v2EditFact').value=row.fact?JSON.stringify(row.fact,null,2):'';$('v2EditFactBox').hidden=!row.fact;$('v2UseAdvancedFact').checked=false;paintFactFields(row);
    $('v2FocusEvidence').replaceChildren();node($('v2FocusEvidence'),'p','正在读取本条原文依据…');
    // Do not enable confirmation until the expected-version evidence has loaded.
    const cached=state.evidence.get(row.id+':'+row.version);
    if(cached){paintEvidence(cached,row,selection);controls();return;}
    const epoch=state.epoch,controller=new AbortController();state.evidenceAbort=controller;
    request('evidence',{id:row.id,expected_version:row.version},controller.signal).then(response=>{
      if(epoch!==state.epoch||selection!==state.selection)return;
      state.evidence.set(row.id+':'+row.version,response.quotes);paintEvidence(response.quotes,row,selection);controls();
    }).catch(error=>{if(epoch!==state.epoch||selection!==state.selection||error.name==='AbortError')return;$('v2FocusEvidence').replaceChildren();node($('v2FocusEvidence'),'p','原文读取未完成：'+error.message+'。请刷新列表后重试。');state.evidenceReady=false;controls();});controls();
  }
  function paintEvidence(quotes,row,selection){
    if(state.focus!==row.id||selection!==state.selection)return;
    const host=$('v2FocusEvidence');host.replaceChildren();state.evidenceReady=!!quotes.length;
    if(!quotes.length)node(host,'p','没有可读取的依据，暂不提供直接确认。');
    for(const e of quotes){node(host,'small',e.source_name+(e.source_kind==='candidate'?'（候选文件，非原始文章）':e.source_kind==='manual'?'（手动提供的资料）':''));node(host,'blockquote',e.text);if(e.location)node(host,'small',e.location);}
    if(!row.can_edit)node(host,'p','你可以查看依据，修改需由作者或管理员处理。');
    const full=node(host,'button','查看本条来源全文（作者／管理员）');full.type='button';full.className='text-button';
    full.onclick=()=>{const epoch=state.epoch;work(async(active,signal)=>{const result=await request('source',{id:row.id},signal);if(!active()||epoch!==state.epoch||selection!==state.selection)return;
      for(const source of result.sources){const details=node(host,'details');node(details,'summary',source.name+' · 来源全文');node(details,'pre',source.text);}full.disabled=true;
    });};
  }
  function openEventEditor(row){
    if(!row?.can_edit||!row.event||state.working)return;invalidate();state.edit=row;$('v2Edit').hidden=true;
    $('v2FocusEditorHost').append($('v2EventEdit'));$('v2EventEdit').hidden=false;$('v2UseAdvancedEvent').checked=false;
    const event=row.event,host=$('v2EventFields');host.replaceChildren();state.eventInputs={};
    const field=(key,label,value,options)=>{const wrap=node(host,'label',label),input=node(wrap,options?'select':'input');
      if(options)for(const [value,title] of options){const option=node(input,'option',title);option.value=value;}
      input.value=value??'';input.oninput=invalidate;input.onchange=invalidate;state.eventInputs[key]=input;
    };
    field('title','活动名称',event.title);field('raw','时间原文／核对说明',event.time.raw);
    field('precision','日期精度',event.time.precision,[['unknown','未记载'],['day','具体到某天'],['month','具体到某月'],['year','具体到某年'],['range','起止范围']]);
    field('start','开始日期（如2026-04-18；按月填2026-04）',event.time.start);field('end','结束日期（仅起止范围填写，可留空）',event.time.end);
    field('basis','日期依据',event.time.basis,[['explicit','原文明示'],['inferred','根据上下文推定'],['unknown','未记载']]);
    field('state','发生情况',event.state,[['held','已发生'],['planned','计划'],['cancelled','取消'],['unknown','待核对']]);field('location','地点',event.location);
    $('v2EventChanges').value=JSON.stringify({title:event.title,kind:event.kind,time:event.time,state:event.state,location:event.location},null,2);
    $('v2EventEdit').scrollIntoView?.({block:'nearest'});controls();
  }
  function readEventFields(){
    if($('v2UseAdvancedEvent').checked)return JSON.parse($('v2EventChanges').value);
    const f=state.eventInputs,precision=f.precision.value;
    return {title:f.title.value,kind:state.edit.event.kind,location:f.location.value,state:f.state.value,
      time:{raw:f.raw.value,precision,basis:f.basis.value,start:precision==='unknown'?null:(f.start.value.trim()||null),end:precision==='range'?(f.end.value.trim()||null):null}};
  }
  function paintFactFields(row){
    const host=$('v2FactFields');host.replaceChildren();state.factInputs={};if(!row.fact)return;
    const f=row.fact;node(host,'h3','供问答使用的事实');node(host,'p','修改这些值会回到待核实。请同时核对摘要；未记载的比分留空。');
    function field(key,label,value,type='text'){
      const wrap=node(host,'label',label),input=node(wrap,'input');input.type=type;input.value=value??'';
      if(type==='number'){input.min='0';input.step='any';}input.oninput=invalidate;state.factInputs[key]=input;
    }
    if(f.predicate==='quantity'){field('value','数量',f.value,'number');field('unit','单位',f.unit);field('metric','统计口径',f.metric);}
    else if(f.predicate==='score'){
      field('own','本方最终得分（未知留空）',f.value.own,'number');field('opponent','对手最终得分（未知留空）',f.value.opponent,'number');
      const label=node(host,'label','比赛结果'),select=node(label,'select');for(const [value,name] of [['win','获胜'],['loss','失利'],['draw','平局'],['unknown','未记载']]){const option=node(select,'option',name);option.value=value;}select.value=f.value.result;select.onchange=invalidate;state.factInputs.result=select;
      node(host,'p','对手：'+(row.entities?.[f.object_key]||'以原记录为准')+'。如需更正关联人物／对手，请在高级事实内容中核对编号。');
    }else{field('value',f.predicate==='holds_role'?'职务':'事实内容',f.value);if(f.predicate==='holds_role')field('term','届次',f.term);}
  }
  function readFactFields(row){
    if($('v2UseAdvancedFact').checked)return JSON.parse($('v2EditFact').value);
    const f=JSON.parse(JSON.stringify(row.fact)),inputs=state.factInputs;
    if(f.predicate==='quantity'){
      if(inputs.value.value.trim()==='')throw new Error('请填写数量；不能把空白当成0。');
      f.value=Number(inputs.value.value);f.unit=inputs.unit.value;f.metric=inputs.metric.value;
    }else if(f.predicate==='score')f.value={own:inputs.own.value.trim()===''?null:Number(inputs.own.value),opponent:inputs.opponent.value.trim()===''?null:Number(inputs.opponent.value),result:inputs.result.value};
    else{f.value=inputs.value.value;if(f.predicate==='holds_role')f.term=inputs.term.value;}
    return f;
  }
  function reviewAnswer(row){
    const f=row.fact,names=row.entities||{};let value='';
    if(f?.predicate==='quantity')value=String(f.value)+(f.unit||'')+(f.metric?'（'+f.metric+'）':'');
    if(f?.predicate==='holds_role')value=(names[f.subject_key]||'主体未匹配')+' · '+(f.term||'')+' '+f.value;
    if(f?.predicate==='score')value=(names[f.subject_key]||'本方')+' 对 '+(names[f.object_key]||'对手')+'：'+(f.value.own===null||f.value.opponent===null?'比分未记载':f.value.own+'：'+f.value.opponent)+'；'+({win:'获胜',loss:'失利',draw:'平局',unknown:'胜负未记载'}[f.value.result]);
    return (value?value+'。\n':'')+row.summary+(row.detail?'\n'+row.detail:'');
  }
  function reviewQuestion(row){
    const f=row.fact;
    if(f?.predicate==='holds_role')return (f.term||'资料记载')+'的'+f.value+'是谁？';
    if(f?.predicate==='quantity')return (f.metric||row.title)+'是多少？';
    if(f?.predicate==='score')return (row.event?.title||row.title)+'的比分和胜负是什么？';
    return row.title.replace(/[。？！?！]$/,'')+'，这条记载对吗？';
  }
  function nextFocus(){
    if(state.working)return;
    const items=focusRows(),at=items.findIndex(x=>x.row.id===state.focus);
    if(items.length)selectFocus(items[(at+1)%items.length].row);
  }
  async function focusDecision(verification){
    if(!state.edit?.can_edit||!state.evidenceReady||state.working||state.editing||state.edit.lifecycle==='archived')return;
    invalidate();
    await work(async(active,signal)=>{
      const row=state.edit,items=focusRows(),at=items.findIndex(x=>x.row.id===row.id),next=items[at+1]?.row.id;
      const reason=(verification==='confirmed'?'人工已核对本题答案与原文依据，确认正确。':'人工标为不正确，待修改。')+'原核对提示：'+(row.verification_reason||'');
      // One human click; the server still performs its existing preview/version/ACL checks.
      // No editor field, quantity, date, visibility or lifecycle can be changed here.
      const plan=await request('update/preview',{id:row.id,expected_version:row.version,changes:{verification,verification_reason:reason.slice(0,500)}},signal);
      if(!active())return;
      const sameBody=plan.before&&plan.after&&Object.keys(plan.before).every(key=>['verification','verification_reason'].includes(key)||JSON.stringify(plan.before[key])===JSON.stringify(plan.after[key]));
      if(!sameBody||plan.lifecycle!==row.lifecycle||plan.after.verification!==verification||(plan.warnings||[]).length){review(plan);message('本次变更需要额外核对，请查看预览后再保存。');return;}
      await request('confirm',{token:plan.token,confirmed:true,acknowledge_warnings:false},signal);
      if(!active())return;
      const result=await request('list',{include_archived:true},signal);if(!active())return;
      state.rows=result.records;state.focus=next||null;state.evidence.clear();state.editing=false;renderRecords();
      message('“'+row.title+'”'+(verification==='confirmed'?'已确认，可用于问答。':'已标为不正确，可在“待修改”中处理。')+' 已进入下一条。');
    });
  }
  async function previewEdit(options={}){
    invalidate();await work(async(active,signal)=>{
      const row=state.edit;if(!row)return;
      const changes={title:$('v2EditTitle').value,summary:$('v2EditSummary').value,detail:$('v2EditDetail').value,verification:$('v2EditStatus').value,verification_reason:$('v2EditReason').value,lifecycle:options.statusDecision?row.lifecycle:($('v2EditArchived').checked?'archived':'active')};
      if(row.fact)changes.fact=readFactFields(row);
      const plan=await request('update/preview',{id:row.id,expected_version:row.version,changes},signal);if(active()){
        review(plan);$('v2ReviewHost').append($('v2Review'));
        if(changes.verification==='confirmed'&&plan.after.verification==='pending')message('正文或事实已改动，本次保存仍为待核实；保存后可重新核对并确认。');
      }
    });
  }
  for(const id of ['v2FocusStatus','v2FocusCategory'])$(id).onchange=()=>{if(!state.working)renderFocus();};
  $('v2FocusSearch').oninput=()=>{if(!state.working)renderFocus();};
  $('v2FocusRefresh').onclick=refresh;
  $('v2FocusRestore').onclick=()=>{if(!state.edit?.can_edit||state.working)return;$('v2EditArchived').checked=false;previewEdit();};
  $('v2FocusEvent').onclick=()=>openEventEditor(state.edit);
  $('v2FocusConfirm').onclick=()=>focusDecision('confirmed');$('v2FocusWrong').onclick=()=>focusDecision('disputed');
  $('v2FocusPending').onclick=nextFocus;
  $('v2FocusModify').onclick=()=>{if(!state.edit?.can_edit||state.working)return;state.editing=true;$('v2Edit').hidden=false;controls();};
  $('v2FocusNext').onclick=nextFocus;
  $('v2Login').onclick=()=>workspaceOpenAccount('memoryV2');
  $('v2StartAdd').onclick=()=>{if(!sharedState.token){workspaceOpenAccount('memoryV2');return;}$('v2ImportDisclosure').open=true;$('v2DraftTitle').focus();};
  $('v2StartReview').onclick=()=>{if(!sharedState.token){workspaceOpenAccount('memoryV2');return;}$('v2FocusPanel').scrollIntoView?.({block:'start'});refresh();};
  $('v2StartChat').onclick=()=>showWorkspacePage('chat');
  $('v2Draft').onclick=()=>work(async(active,signal)=>{
    if(!String($('v2DraftText').value).trim())throw new Error('请先填写想保留的文字。');
    const result=await request('draft',{title:$('v2DraftTitle').value,content:$('v2DraftText').value},signal);
    if(!active())return;setDocument(result);$('v2ImportDisclosure').open=true;
    message('候选已生成，尚未保存。核对下方文字后，点击“查看保存预览”，确认归属与可见范围再保存。');
    $('v2Candidates').scrollIntoView?.({block:'nearest'});
  });
  if(typeof createMarkdownImport==='function')mdImporter=createMarkdownImport({request,work,setDocument,controls,abort:()=>state.abort?.abort(),invalidate:()=>{invalidate();state.document=null;$('v2Candidates').replaceChildren();$('v2Packet').value='';controls();}});
  $('v2Scope').onchange=()=>{clear();controls();refresh();};
  for(const id of ['v2DraftTitle','v2DraftText'])$(id).oninput=()=>{invalidate();state.document=null;$('v2Candidates').replaceChildren();$('v2Packet').value='';message('文字已修改，请重新生成候选再预览。');controls();};
  $('v2File').onchange=loadFile;
  $('v2Validate').onclick=()=>work(async(active,signal)=>{const result=await request('validate',{document:JSON.parse($('v2Packet').value)},signal);if(active())setDocument(result);});
  $('v2Packet').oninput=()=>{invalidate();state.document=null;controls();};
  $('v2Visibility').onchange=()=>{invalidate();controls();};
  $('v2Preview').onclick=()=>work(async(active,signal)=>{
    const selected_keys=[...$('v2Candidates').querySelectorAll('input:checked')].map(x=>x.value);
    const plan=await request('preview',{document:state.document,selected_keys,visibility:$('v2Visibility').value,edit_policy:'author'},signal);
    if(!active())return;if(plan.duplicate_only){message('选中候选都已导入，本次没有新增。');return;}review(plan);
  });
  $('v2Confirm').onclick=()=>work(async(active,signal)=>{
    const result=await request('confirm',{token:state.plan.token,confirmed:$('v2Consent').checked,acknowledge_warnings:$('v2WarningsConsent').checked},signal);
    if(!active())return;invalidate();state.edit=null;$('v2Edit').hidden=true;$('v2EventEdit').hidden=true;message(result.imported?'已导入'+result.imported+'条记忆。':'修改已保存。');
    const rows=await request('list',{include_archived:true},signal);if(active()){state.rows=rows.records;state.evidence.clear();renderRecords();}
  });
  $('v2EditPreview').onclick=()=>previewEdit();
  $('v2EventPreview').onclick=()=>work(async(active,signal)=>{const plan=await request('event/preview',{id:state.edit.id,expected_version:state.edit.version,changes:readEventFields()},signal);if(active()){review(plan);$('v2ReviewHost').append($('v2Review'));}});
  $('v2EventChanges').oninput=invalidate;$('v2UseAdvancedEvent').onchange=invalidate;
  $('v2UseAdvancedFact').onchange=invalidate;
  for(const id of ['v2EditTitle','v2EditSummary','v2EditDetail','v2EditStatus','v2EditReason','v2EditFact','v2EditArchived'])$(id).oninput=invalidate;
  $('v2Cancel').onclick=()=>{invalidate();controls();message('已取消预览，未写入。');};
  $('v2Consent').onchange=controls;$('v2WarningsConsent').onchange=controls;$('v2Reload').onclick=refresh;$('v2Archived').onchange=refresh;
  $('v2Export').onclick=()=>{
    if(!state.document)return;
    const blob=new Blob([JSON.stringify(state.document,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='memory-candidates-v2.json';link.click();URL.revokeObjectURL(url);
  };
  sync();return {sync,refresh};
})();
