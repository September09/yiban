const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
  constructor(){this.value='';this.textContent='';this.hidden=false;this.checked=false;this.disabled=false;this.children=[];this.className='';this.focused=false;}
  append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}
  querySelectorAll(selector){return this.children.flatMap(n=>[...(selector==='button'&&n.tag==='button'?[n]:[]),...n.querySelectorAll(selector)]);}
  focus(){this.focused=true;}
}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
const state={token:null,account:null,spaces:[],space:null,epoch:0,busy:false,asking:false};let requestStatus=null,pending=true,delayed=null;const calls=[],navigation=[];
const context=vm.createContext({$,sharedState:state,busy:false,recording:false,enrolling:false,console,Date,Error,Promise,
 document:{createElement:tag=>Object.assign(new Node(),{tag})},
 sharedRun:async fn=>{state.busy=true;try{await fn();}catch{}finally{state.busy=false;}},
 sharedApi:async(path,data)=>{calls.push({path,data});
  if(path==='/joining/lookup'){if(delayed)await delayed;return{preview_token:'app-preview',space_name:'虚构团队',already_member:false};}
  if(path==='/joining/request'){requestStatus='pending';return{status:'pending',message:'等待管理员'};}
  if(path==='/joining/mine')return{requests:requestStatus?[{id:'request',space_name:'虚构团队',status:requestStatus,role:null}]:[]};
  if(path==='/joining/inbox')return{code_active:false,code_expires:null,requests:pending?[{id:'request',account_name:'虚构申请人'}]:[]};
  if(path==='/joining/review/preview')return{preview_token:'approve-preview',space_name:'虚构团队',account_name:'虚构申请人',decision:data.decision,effect:'只读成员；个人资料保持独立'};
  if(path==='/joining/review/confirm'){pending=false;return{status:'approved'};}
  throw new Error('Unexpected path '+path);
 },
 workspaceOpenAccount:target=>navigation.push('account:'+target),workspaceOpenMemory:()=>navigation.push('memory'),showWorkspacePage:key=>navigation.push(key),
 sharedRefresh:async()=>{},companionMemorySetMode:async mode=>navigation.push('mode:'+mode),sharedLock:()=>{state.token=null;state.account=null;state.epoch++;}
});
const run=code=>vm.runInContext(code,context);
(async()=>{
 run(fs.readFileSync('static/member-onboarding.js','utf8'));
 assert.equal(calls.length,0,'Visitor guide must not fetch or grant access');
 await $('gettingStartedAsk').onclick();assert.deepEqual(navigation,['account:chat']);
 state.token='applicant-token';state.account={name:'虚构申请人'};run('membershipUI.controls();gettingStarted.refresh()');
 assert.match($('gettingStartedHint').textContent,/尚未加入团队/);
 $('joinCode').value='request-code-only';await $('joinLookup').onclick();
 assert.equal($('joinRequestReview').hidden,false);assert.equal($('joinRequestSubmit').disabled,true);
 assert(!calls.some(x=>x.path==='/joining/request'),'Lookup must not submit or approve');
 await $('joinRequestSubmit').onclick();assert(!calls.some(x=>x.path==='/joining/request'));
 $('joinRequestConsent').checked=true;$('joinRequestConsent').onchange();assert.equal($('joinRequestSubmit').disabled,false);
 await $('joinRequestSubmit').onclick();assert.equal(calls.filter(x=>x.path==='/joining/request').length,1);
 assert(!calls.some(x=>x.path.includes('/members/set')||x.path==='/joining/review/confirm'));
 state.space={id:'team',role:'owner'};await run('membershipUI.refresh()');
 const approve=$('joinInbox').querySelectorAll('button')[0];await approve.onclick();
 assert.match($('joinApprovalSummary').textContent,/虚构申请人/);assert.equal($('joinApprovalConfirm').disabled,true);
 await $('joinApprovalConfirm').onclick();assert(!calls.some(x=>x.path==='/joining/review/confirm'));
 $('joinApprovalConsent').checked=true;$('joinApprovalConsent').onchange();await $('joinApprovalConfirm').onclick();
 assert.equal(calls.filter(x=>x.path==='/joining/review/confirm').length,1);
 let release;delayed=new Promise(r=>release=r);$('joinCode').value='another-code';const lookup=$('joinLookup').onclick();
 state.token=null;state.account=null;state.space=null;state.epoch++;run('membershipUI.controls()');release();await lookup;
 assert.equal($('joinRequestReview').hidden,true);assert.equal($('joinApprovalReview').hidden,true);assert.equal($('joinCode').value,'');assert.equal($('joinCurrentCode').value,'');
 assert.equal($('joinMine').children.length,0,'Logout must erase request metadata');
 console.log('Member onboarding: no visitor requests, explicit application and approval, automatic-query navigation, stale results and logout cleanup passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
