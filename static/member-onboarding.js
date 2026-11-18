// Tokens, request codes and previews remain in page memory; no permission is inferred from voice.
const membershipUI=(()=>{
  let binding='',sequence=0,working=false,requestPlan=null,approvalPlan=null,codeExpires=null,codeActive=false;
  const ids=['joinLookup','joinCode','joinRefresh','joinCodeCreate','joinCodeDisable','joinInboxRefresh','joinApprovalCancel'];
  const current=()=>sharedState.token+'|'+(sharedState.space?.id||'');
  const owner=()=>!!sharedState.token&&sharedState.space?.role==='owner';
  async function api(path,data={}){
    const epoch=sharedState.epoch,token=sharedState.token;
    const result=await sharedApi('/joining/'+path,data);
    if(epoch!==sharedState.epoch||token!==sharedState.token)throw Object.assign(new Error('账号已切换，本次页面结果已丢弃。'),{stale:true});
    return result;
  }
  function tell(text,target='joinStatus'){$(target).textContent=text;}
  function discardRequest(){requestPlan=null;$('joinRequestReview').hidden=true;$('joinRequestConsent').checked=false;$('joinRequestSummary').textContent='';}
  function discardApproval(){approvalPlan=null;$('joinApprovalReview').hidden=true;$('joinApprovalConsent').checked=false;$('joinApprovalSummary').textContent='';}
  function clear(){
    sequence++;discardRequest();discardApproval();codeExpires=null;codeActive=false;
    $('joinCode').value='';$('joinCurrentCode').value='';$('joinCodeResult').hidden=true;
    for(const id of ['joinMine','joinInbox'])$(id).replaceChildren();
    for(const id of ['joinStatus','joinAdminStatus','joinCodeState'])$(id).textContent='';
  }
  function controls(){
    const next=current();if(binding!==next){clear();binding=next;}
    const locked=working||sharedState.busy||sharedState.asking||busy||recording||enrolling;
    $('joinAdminPanel').hidden=!owner();
    for(const id of ids)$(id).disabled=!sharedState.token||locked||((id.startsWith('joinCodeC')||id==='joinCodeDisable'||id==='joinInboxRefresh'||id==='joinApprovalCancel')&&!owner());
    $('joinCodeDisable').disabled=!owner()||locked||!codeActive;
    $('joinRequestConsent').disabled=locked||!requestPlan;
    $('joinRequestSubmit').disabled=locked||!requestPlan||!$('joinRequestConsent').checked;
    $('joinApprovalConsent').disabled=locked||!approvalPlan;
    $('joinApprovalConfirm').disabled=locked||!owner()||!approvalPlan||!$('joinApprovalConsent').checked;
    for(const button of [...$('joinMine').querySelectorAll('button'),...$('joinInbox').querySelectorAll('button')])button.disabled=locked;
  }
  async function operate(task,target='joinStatus'){
    if(working||sharedState.busy||sharedState.asking)return;
    working=true;controls();
    try{await sharedRun(async()=>{try{await task();}catch(error){if(error.stale)return;if(error.status===409){discardRequest();discardApproval();}tell(error.message||'操作未完成，请重试。',target);throw error;}});}
    finally{working=false;controls();}
  }
  function text(parent,value){const node=document.createElement('span');node.textContent=value;parent.append(node);}
  function action(parent,label,handler){const button=document.createElement('button');button.type='button';button.className='text-button';button.textContent=label;button.onclick=handler;parent.append(button);}
  function renderMine(rows){
    const list=$('joinMine');list.replaceChildren();
    if(!rows.length){const li=document.createElement('li');li.textContent='还没有加入申请。可以向管理员索取申请码，也可以先使用个人记忆。';list.append(li);return;}
    const labels={pending:'等待管理员批准',rejected:'管理员未通过，可联系管理员核对',cancelled:'申请已撤回',approved:'申请已处理'};
    for(const row of rows){
      const li=document.createElement('li');text(li,row.space_name+' · '+(row.role?'已加入':row.status==='approved'?'当前已不是成员，需要重新申请':labels[row.status]));
      if(row.status==='pending')action(li,'撤回申请',()=>operate(async()=>{await api('cancel',{request_id:row.id});await refresh();tell('申请已撤回。');}));
      if(row.role)action(li,'刷新并开始提问',()=>operate(async()=>{await sharedRefresh();await companionMemorySetMode('auto');showWorkspacePage('chat');$('input').focus();}));
      list.append(li);
    }
  }
  function renderInbox(result){
    codeActive=result.code_active;
    if(codeExpires!==result.code_expires){$('joinCurrentCode').value='';$('joinCodeResult').hidden=true;codeExpires=result.code_expires;}
    $('joinCodeState').textContent=codeActive?'申请码已启用，有效至 '+new Date(result.code_expires*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）。更换后旧码失效，现有成员不受影响。':'申请码未启用。生成后自行转交给希望加入的成员。';
    const list=$('joinInbox');list.replaceChildren();
    if(!result.requests.length){const li=document.createElement('li');li.textContent='暂无待批准申请。';list.append(li);}
    for(const row of result.requests){
      const li=document.createElement('li');text(li,row.account_name+' · 申请加入当前空间');
      for(const [decision,label] of [['approve','预览批准（只读）'],['reject','预览拒绝']])action(li,label,()=>operate(async()=>{
        const epoch=sharedState.epoch,space=sharedState.space?.id;
        const plan=await api('review/preview',{space_id:space,request_id:row.id,decision});
        if(epoch!==sharedState.epoch||space!==sharedState.space?.id)return;
        discardApproval();approvalPlan=plan;
        $('joinApprovalSummary').textContent='申请人：'+plan.account_name+'\n空间：'+plan.space_name+'\n处理：'+(plan.decision==='approve'?'通过申请':'拒绝申请')+(plan.decision==='approve'?'\n角色：'+({owner:'管理员',editor:'编辑成员',reader:'只读成员'}[plan.role]||'保持现有角色')+'\n'+plan.effect:'\n拒绝本次申请，不更改现有成员权限。');
        $('joinApprovalReview').hidden=false;controls();
      },'joinAdminStatus'));
      list.append(li);
    }
  }
  async function refresh(){
    controls();if(!sharedState.token)return;
    const identity=current(),epoch=sharedState.epoch,version=++sequence,space=sharedState.space?.id,isOwner=owner();
    try{
      const [mine,inbox]=await Promise.all([api('mine'),isOwner?api('inbox',{space_id:space}):Promise.resolve(null)]);
      if(identity!==current()||epoch!==sharedState.epoch||version!==sequence)return;
      renderMine(mine.requests);if(inbox)renderInbox(inbox);controls();
    }catch(error){
      if(identity!==current()||version!==sequence)return;
      discardApproval();$('joinInbox').replaceChildren();$('joinCurrentCode').value='';$('joinCodeResult').hidden=true;
      if(error.status===401)sharedLock('登录已失效，请重新登录。');
      else tell(error.message||'申请列表暂时无法加载，请刷新重试。',isOwner?'joinAdminStatus':'joinStatus');
      controls();
    }
  }
  function open(){workspaceOpenAccount('join');if(sharedState.token)$('joinCode').focus();}
  $('joinCode').oninput=()=>{discardRequest();controls();};
  $('joinRequestConsent').onchange=controls;$('joinApprovalConsent').onchange=controls;
  $('joinLookup').onclick=()=>operate(async()=>{
    discardRequest();const epoch=sharedState.epoch,identity=current(),code=$('joinCode').value.trim();
    if(!code){tell('请填写管理员提供的申请码。');$('joinCode').focus();return;}
    const plan=await api('lookup',{code});if(epoch!==sharedState.epoch||identity!==current()||code!==$('joinCode').value.trim())return;
    $('joinCode').value='';
    if(plan.already_member){await sharedRefresh();tell('你已经是「'+plan.space_name+'」的成员，可以回到对话自动查询获准资料。');return;}
    requestPlan=plan;$('joinRequestSummary').textContent='申请账号：'+sharedState.account.name+'\n申请空间：'+plan.space_name+'\n通过后为只读成员。批准前不能查看团队资料，个人记忆不会公开给管理员或其他成员。';
    $('joinRequestReview').hidden=false;tell('请核对账号与空间，再确认提交。');controls();
  });
  $('joinRequestSubmit').onclick=()=>operate(async()=>{
    if(!requestPlan||!$('joinRequestConsent').checked)return;
    const result=await api('request',{preview_token:requestPlan.preview_token,confirmed:true});discardRequest();await refresh();tell(result.message||'申请已处理，请刷新状态。');
  });
  $('joinRefresh').onclick=()=>operate(async()=>{await sharedRefresh();tell('申请状态和可访问空间已刷新。');});
  $('joinInboxRefresh').onclick=()=>operate(refresh,'joinAdminStatus');
  $('joinCodeCreate').onclick=()=>operate(async()=>{
    const space=sharedState.space?.id,epoch=sharedState.epoch;
    const result=await api('code/create',{space_id:space});
    if(epoch!==sharedState.epoch||space!==sharedState.space?.id)return;
    codeExpires=result.expires;codeActive=true;$('joinCurrentCode').value=result.code;$('joinCodeResult').hidden=false;
    await refresh();tell('已生成新申请码。请自行转交；旧码已失效，已有成员不受影响。','joinAdminStatus');
  },'joinAdminStatus');
  $('joinCodeDisable').onclick=()=>operate(async()=>{await api('code/disable',{space_id:sharedState.space.id});codeExpires=null;codeActive=false;$('joinCurrentCode').value='';$('joinCodeResult').hidden=true;await refresh();tell('已停用申请码。待处理申请和已有成员仍保留。','joinAdminStatus');},'joinAdminStatus');
  $('joinApprovalCancel').onclick=()=>{discardApproval();controls();};
  $('joinApprovalConfirm').onclick=()=>operate(async()=>{
    if(!approvalPlan||!$('joinApprovalConsent').checked)return;
    const result=await api('review/confirm',{preview_token:approvalPlan.preview_token,confirmed:true});discardApproval();await sharedRefresh();tell(result.status==='approved'?'申请已通过。新成员刷新后可查询获准的团队资料；个人记忆保持独立。':'已拒绝本次申请。','joinAdminStatus');
  },'joinAdminStatus');
  return {refresh,controls,open};
})();

const gettingStarted=(()=>{
  function refresh(){
    const account=sharedState.token&&sharedState.account,spaces=sharedState.spaces||[];
    $('gettingStartedAccount').textContent=account?'1 · 已登录 '+account.name:'1 · 登录自己的账号';
    $('gettingStartedSummary').textContent=account?'开始使用 · '+account.name+' · '+(spaces.length?'已连接 '+spaces.length+' 个空间':'当前可使用个人记忆'):'开始使用 · 可以直接聊天，也可以登录使用记忆';
    $('gettingStartedHint').textContent=account?(spaces.length?'选择“开始自动查询”，同时使用自己的个人记忆和有权查看的团队记忆。':'尚未加入团队也可以保存并查询自己的个人记忆。申请加入团队需管理员批准。'):'普通聊天不要求登录。登录后才能使用账号记忆；录入声纹不会自动登录或获得团队权限。';
    $('gettingStartedLogin').textContent=account?'管理我的账号':'登录／注册账号';
    for(const id of ['gettingStartedLogin','gettingStartedJoin','gettingStartedAdd','gettingStartedAsk'])$(id).disabled=busy||recording||enrolling||sharedState.busy||sharedState.asking;
  }
  $('gettingStartedLogin').onclick=()=>workspaceOpenAccount('chat');
  $('gettingStartedJoin').onclick=()=>membershipUI.open();
  $('gettingStartedAdd').onclick=()=>{
    if(!sharedState.token){workspaceOpenAccount('memoryAdd');return;}
    workspaceOpenMemory();$('v2ImportDisclosure').open=true;
  };
  $('gettingStartedAsk').onclick=async()=>{
    if(!sharedState.token){workspaceOpenAccount('chat');return;}
    await companionMemorySetMode('auto');showWorkspacePage('chat');$('input').focus();
  };
  refresh();return {refresh};
})();
membershipUI.controls();
