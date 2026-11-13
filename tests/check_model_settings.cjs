// Consent, cancellation and late-result protection without media or network access.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{constructor(){this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.children=[];this.textContent='';}append(...x){this.children.push(...x);}replaceChildren(){this.children=[];}}
const ids=Object.fromEntries([...fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
ids.modelsProfile.value='local';ids.modelsCompareScope.value='general';
const cfg={backend:'ollama',base_url:'http://127.0.0.1:11434',model:'local-test',vision_model:'',api_key_env:'MODEL_TEST_KEY',key_available:false};
const settings={revision:'synthetic-revision',active:'local',profiles:{local:cfg,cloud:{...cfg,backend:'openai_compatible',base_url:'https://example.invalid/v1',model:'cloud-test'}}};
const requests=[];let resolveLate=null,late=false;
const context=vm.createContext({$:id=>ids[id],document:{createElement:()=>new Element()},sharedState:{token:'test-token',epoch:0,space:{id:'test-space',name:'虚构团队'}},sharedStatusNames:{pending:'待核实'},crypto:require('node:crypto').webcrypto,AbortController,
fetch:async(path,options)=>{
 const data=options?.body?JSON.parse(options.body):null;requests.push({path,data});
 const reply=d=>({ok:true,json:async()=>d});
 if(path==='/api/models/settings')return reply(settings);
 if(path==='/api/models/compare/preview')return reply({preview_token:'synthetic-preview',query:data.query,sources:[],scope:data.scope,models:{local:'local-test',cloud:'cloud-test'},notice:'预览',destinations:{local:cfg.base_url,cloud:'https://example.invalid/v1'}});
 if(path==='/api/models/compare/cancel'||path==='/api/turn/cancel')return reply({cancelled:true});
 if(path==='/api/models/compare/confirm'){
  const result={sources:[],scope:'general',results:[{profile:'local',model:'local-test',ok:true,answer:'虚构文字回答',elapsed_ms:1}],notice:'文字对比完成'};
  if(late)return await new Promise(r=>{resolveLate=()=>r(reply(result));});return reply(result);
 }
 throw Error('Unexpected request '+path);
},localStorage:{getItem(){throw Error('no browser storage');},setItem(){throw Error('no browser storage');}},console});
vm.runInContext(fs.readFileSync('static/model-settings.js','utf8'),context);
(async()=>{
 await vm.runInContext('modelSettingsUI.load()',context);
 ids.modelsCompareQuery.value='测试问题';await ids.modelsComparePreview.onclick();
 assert.equal(requests.filter(r=>r.path.endsWith('/confirm')).length,0);
 assert.equal(ids.modelsCompareConfirm.disabled,true);
 ids.modelsCompareConsent.checked=true;ids.modelsCompareConsent.onchange();
 assert.equal(ids.modelsCompareConfirm.disabled,false);await ids.modelsCompareConfirm.onclick();
 assert.equal(requests.at(-1).data.allow_remote,true);
 assert.ok(ids.modelsCompareResult.children.length>0);
 vm.runInContext('modelSettingsUI.leave()',context);assert.equal(ids.modelsCompareResult.children.length,0);
 await ids.modelsComparePreview.onclick();ids.modelsCompareConsent.checked=true;late=true;
 const pending=ids.modelsCompareConfirm.onclick();await new Promise(r=>setImmediate(r));
 vm.runInContext('modelSettingsUI.leave()',context);resolveLate();await pending;
 assert.equal(ids.modelsCompareResult.children.length,0,'late result after leaving is discarded');
 assert.ok(requests.every(r=>!r.path.includes('speak')&&!r.path.includes('transcribe')));
 await ids.modelsComparePreview.onclick();await ids.modelsCompareCancel.onclick();
 assert.ok(requests.some(r=>r.path.endsWith('/compare/cancel')));
 console.log('Model settings frontend passed: preview consent, cancellation, late replies, no storage/media.');
})().catch(error=>{console.error(error);process.exitCode=1;});
