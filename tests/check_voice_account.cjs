const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{constructor(){this.value='';this.textContent='';this.hidden=false;this.checked=false;this.disabled=false;this.children=[];}
 append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}scrollIntoView(){}}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
const state={token:null,account:null,epoch:0,busy:false,asking:false};const calls=[],navigation=[];
let authorized=false,delay=null;
const status=()=>({account:{id:'account-a',name:'User A'},binding:authorized?{profile_id:'profile-a',profile_name:'Voice A'}:null,
 profiles:[{id:'profile-a',name:'Voice A'}],spaces:[{name:'Allowed Team',role:'reader'}],authorized,message:authorized?'Authorized':'Not authorized'});
const context=vm.createContext({$,sharedState:state,busy:false,recording:false,enrolling:false,console,Set,Error,Promise,
 document:{createElement:()=>new Node()},window:{confirm:()=>true},
 fetch:async(path,options)=>{const data=JSON.parse(options.body);calls.push({path,data,headers:options.headers});
  if(path.endsWith('/status'))return{ok:true,json:async()=>status()};
  if(path.endsWith('/preview')){if(delay)await delay;return{ok:true,json:async()=>({preview_token:'preview',profile_name:'Voice A',account_name:'User A',spaces:['Allowed Team'],effect:'Scope does not expand.'})};}
  if(path.endsWith('/confirm')){authorized=true;return{ok:true,json:async()=>status()};}
  if(path.endsWith('/revoke')){authorized=false;return{ok:true,json:async()=>status()};}
  throw new Error(path);},
 sharedLock:()=>{state.token=null;state.account=null;state.epoch++;},
 workspaceOpenAccount:target=>navigation.push('account:'+target),
 companionMemorySetMode:async mode=>navigation.push('mode:'+mode),
 showWorkspacePage:page=>navigation.push(page),notice:()=>{},controls:()=>{},cancelTurn:()=>{},render:()=>{}
});
const run=code=>vm.runInContext(code,context),settle=()=>new Promise(r=>setImmediate(r));
(async()=>{
 run(fs.readFileSync('static/voice-account.js','utf8'));
 assert.equal(calls.length,0,'No guest status requests or grants');
 state.token='token-a';state.account={name:'User A'};run('voiceMemoryUI.sync()');await settle();
 $('voiceMemoryProfile').value='profile-a';$('voiceMemoryProfile').onchange();await $('voiceMemoryPreview').onclick();
 assert.equal($('voiceMemoryReview').hidden,false);assert.equal($('voiceMemoryConfirm').disabled,true);
 assert.match($('voiceMemorySummary').textContent,/Voice A.*User A/);
 assert.equal(calls.filter(c=>c.path.endsWith('/confirm')).length,0);
 await $('voiceMemoryConfirm').onclick();assert.equal(authorized,false);
 $('voiceMemoryConsent').checked=true;$('voiceMemoryConsent').onchange();await $('voiceMemoryConfirm').onclick();
 assert.equal(authorized,true);assert.equal($('voiceMemoryStart').disabled,false);
 assert.deepEqual(calls.at(-1).data,{preview_token:'preview',confirmed:true,consent:true});
 await $('voiceMemoryStart').onclick();assert.deepEqual(navigation,['mode:auto','chat']);
 assert.equal($('speakerAuto').checked,true);
 assert(calls.every(c=>c.path.startsWith('/api/voice-memory/')),'Setup does not start a microphone, ASR or TTS request');
 const oldToken=state.token;
 run("voiceMemoryUI.accept({matched:true,account_name:'Other',profile_name:'Other'})");
 assert.equal($('workspaceSpeakerResult').textContent,'');
 run("voiceMemoryUI.accept({matched:true,account_name:'User A',profile_name:'Voice A'})");
 assert.match($('workspaceSpeakerResult').textContent,/Voice A/);assert.equal(state.token,oldToken,'Voice result must not swap or logout authenticated account');
 await $('voiceMemoryRevoke').onclick();assert.equal(authorized,false);assert.equal($('voiceMemoryStart').disabled,true);
 let release;delay=new Promise(r=>release=r);$('voiceMemoryProfile').value='profile-a';
 const pending=$('voiceMemoryPreview').onclick();const duringPreview=calls.length;await run('voiceMemoryUI.refresh()');assert.equal(calls.length,duringPreview,'Background status cannot race a binding operation');state.token=null;state.account=null;state.epoch++;run('voiceMemoryUI.sync()');release();await pending;
 assert.equal($('voiceMemoryReview').hidden,true);assert.equal($('voiceMemorySummary').textContent,'');
 assert.equal($('voiceMemoryProfile').children.length,0);assert.equal($('workspaceSpeakerResult').hidden,true);
 state.token='new-session';state.account={name:'User A'};delay=null;run('voiceMemoryUI.sync()');await settle();
 assert.equal($('voiceMemoryStart').disabled,true,'A new session does not inherit an old authorization');
 console.log('Voice account UI: explicit preview/consent, session isolation, no automatic login, no mic/TTS, and stale/logout cleanup passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
