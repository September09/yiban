const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
class Element{constructor(){this.value='';this.children=[];this.disabled=false;this.checked=false;this.hidden=false;}replaceChildren(){this.children=[];}append(e){this.children.push(e);}checkValidity(){const n=Number(this.value);return Number.isFinite(n)&&(!this.value.includes('nan'));}}
const ids=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const defaults={seed:42,temperature:.5,top_k:20,top_p:.85,repetition_penalty:1.05};
let data={available:true,current:{voice:'Serena',rate:195,generation:{...defaults}},revision:'r1',generation_defaults:{...defaults},voices:[{name:'Serena',locale:'zh_CN'}],supports_rate:false,engine:'Qwen3-TTS',runtime:{state:'ready'}};
const saved=[];
const context=vm.createContext({$:id=>ids[id],document:{createElement:()=>new Element()},window:{addEventListener(){}},
  busy:false,recording:false,speaking:false,enrolling:false,wakeStarting:false,wakePhase:'',
  fetch:async()=>({ok:true,json:async()=>data}),api:async(url,payload)=>{saved.push(payload);data={...data,current:{voice:payload.voice,rate:payload.rate,generation:payload.generation},revision:'r2'};return {json:async()=>data};}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/speech-settings.js'),'utf8'),context);
(async()=>{
 await vm.runInContext('loadSpeechSettings()',context);
 assert.equal(ids.speechGenerationPanel.hidden,false);assert.equal(ids.speechSeed.value,42);assert.equal(ids.speechRate.hidden,true);
 ids.speechTemperature.value='0.6';ids.speechSeed.value='7';ids.speechTopK.value='25';ids.speechTopP.value='0.9';ids.speechVoice.value='Serena';
 await vm.runInContext('saveSpeechSettings()',context);assert.equal(saved.length,1);assert.equal(saved[0].generation.seed,7);assert.equal(saved[0].generation.temperature,.6);
 ids.speechRestoreGeneration.onclick();assert.equal(ids.speechSeed.value,42);assert.equal(saved.length,1,'restore edits draft only');
 ids.speechSeed.value='';await vm.runInContext('saveSpeechSettings()',context);assert.equal(saved.length,1);assert.match(ids.speechSettingsStatus.textContent,/未保存/);
 vm.runInContext('speaking=true;speechSettingsControls()',context);assert.equal(ids.speechTemperature.disabled,true);
 vm.runInContext('speaking=false',context);
 data={...data,current:{voice:'Bright_Female',rate:195,generation:{...defaults}},voices:[{name:'Bright_Female',label:'明亮女声',locale:'zh_CN'},{name:'Serena',locale:'zh_CN'}]};
 await vm.runInContext('loadSpeechSettings()',context);
 assert.equal(ids.speechVoice.value,'Bright_Female');assert.match(ids.speechCurrent.textContent,/明亮女声/);
 assert.ok(ids.speechVoice.children.some(option=>option.value==='Bright_Female'&&option.textContent.includes('明亮女声')));
 data={...data,generation_defaults:null,engine:'macOS',supports_rate:true};
 await vm.runInContext('loadSpeechSettings()',context);assert.equal(ids.speechGenerationPanel.hidden,true);
 console.log('Speech settings checks passed: defaults, explicit save, draft restore, validation and playback locks.');
})().catch(e=>{console.error(e);process.exitCode=1;});
