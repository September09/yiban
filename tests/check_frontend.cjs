// Logic-level checks with DOM/audio stubs; this does not launch or control a browser.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
class Element{
  constructor(){this.children=[];this.textContent='';this.className='';this.hidden=false;this.checked=false;this.disabled=false;this.value='';this.classList={toggle(){},remove(){}};}
  append(...children){this.children.push(...children);}replaceChildren(){this.children=[];}
  setAttribute(k,v){this[k]=v;}querySelector(){return new Element();}
  animate(frames,options){this.animation={frames,options};return{cancel(){}};}
}
const ids=Object.fromEntries([...fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const calls=[],saved={};let micCalls=0;
const context=vm.createContext({
  document:{getElementById:id=>{assert.ok(ids[id],id);return ids[id];},createElement:()=>new Element(),querySelectorAll:()=>[],body:{className:''}},
  localStorage:{getItem:()=>null,setItem:(k,v)=>saved[k]=v,removeItem:k=>delete saved[k]},
  window:{matchMedia:()=>({matches:false}),addEventListener(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>{micCalls++;throw new Error('No device in logic test');}}},
  fetch:async(path,options)=>{
    calls.push({path,data:options?.body?JSON.parse(options.body):null});
    if(path==='/api/status')return{json:async()=>({chat:true,asr:true,wake:true,vision:true})};
    if(path==='/api/turn/cancel')return{ok:true,json:async()=>({cancelled:true})};
    if(path==='/api/profiles/list')return{ok:true,json:async()=>({profiles:[]})};
    if(path==='/api/memory/list')return{ok:true,json:async()=>({memories:[]})};
    if(path==='/api/chat'){
      let consumed=false;return{ok:true,body:{getReader:()=>({read:async()=>consumed?{done:true}:(consumed=true,{done:false,value:new TextEncoder().encode(JSON.stringify({message:{content:'已收到'},done:true})+'\n')})})}};
    }
    throw new Error('Unexpected fetch '+path);
  },
  TextDecoder,TextEncoder,Float32Array,Uint8Array,ArrayBuffer,DataView,AbortController,crypto:require('node:crypto').webcrypto,
  performance:{now:()=>0},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},
  console,URL, btoa:s=>Buffer.from(s,'binary').toString('base64')
});
(async()=>{
  vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
  vm.runInContext(fs.readFileSync('static/personal.js','utf8'),context);
  await new Promise(r=>setImmediate(r));
  assert.equal(micCalls,0,'page load must not acquire media');
  assert.equal(vm.runInContext("isVisualRequest('请向左看看')",context),false);
  assert.equal(vm.runInContext("isVisualRequest('帮我看看这是什么')",context),true);
  assert.equal(vm.runInContext("isVisualRequest('不要拍照')",context),false);
  assert.equal(vm.runInContext("isVisualRequest('请看看能不能认出我的声音')",context),false,'voice identification must not open camera');
  assert.equal(vm.runInContext("isVisualRequest('帮我看看我是谁')",context),false);
  assert.equal(vm.runInContext("isVisualRequest('看看照片里我是谁')",context),true,'explicit image requests remain visual');
  await vm.runInContext("send('请向左看看')",context);
  assert.equal(calls.filter(c=>c.path==='/api/chat').length,1,'left action must not require camera');
  assert.match(ids.speakerDiagnostics.textContent,/文字发送/);
  assert.match(ids.speakerDiagnostics.textContent,/不采集声音/);
  vm.runInContext("transcriptionDiagnostic({text:'不应进入诊断的私人话语',diagnostics:{code:'insufficient_speech',audio_seconds:8,speech_seconds:.7,min_speech_seconds:1.2,trace_id:'test-trace'}},'manual')",context);
  assert.match(ids.speakerDiagnostics.textContent,/有效语音不足/);
  assert.match(ids.speakerDiagnostics.textContent,/0.7/);
  assert.ok(!ids.speakerDiagnostics.textContent.includes('不应进入诊断的私人话语'));
  vm.runInContext("for(let i=0;i<15;i++)speakerDiagnostic('诊断'+i)",context);
  assert.equal(vm.runInContext('speakerDiagnosticSteps.length',context),10);
  const count=calls.length;
  await vm.runInContext("send('帮我看看这是什么')",context);
  assert.equal(calls.length,count,'no picture captured or sent with camera closed');
  assert.match(ids.notice.textContent,/开启摄像头/);
  await vm.runInContext("send('照片里是什么？',{image:'test-image-in-memory'})",context);
  assert.equal(calls.at(-1).data.image,'test-image-in-memory');
  assert.ok(!saved['xiaoban-history'].includes('test-image-in-memory'),'image must never be persisted');
  vm.runInContext("animateAction({target:'screen',action:'nod',label:'点头',duration_ms:99999})",context);
  assert.equal(ids.robot.animation.options.duration,2400,'bound animation duration');
  const before=ids.robot.animation;
  vm.runInContext("animateAction({target:'hardware',action:'nod',duration_ms:1000})",context);
  assert.equal(ids.robot.animation,before,'screen adapter ignores hardware target');
  const wav=vm.runInContext('encodeWav([new Float32Array([0,1,-1])],16000)',context);
  assert.equal(wav.byteLength,50);assert.equal(new DataView(wav).getUint32(24,true),16000);
  assert.equal(new DataView(wav).getInt16(46,true),32767);
  vm.runInContext("profiles=[{id:'alice',name:'甲'},{id:'bob',name:'乙'}];switchProfile('alice');messages=[{role:'user',content:'甲的私有上下文'}];applySpeaker({matched:false,id:null,reason:'不确定'});",context);
  assert.equal(vm.runInContext('activeProfile',context),'');
  assert.equal(vm.runInContext('messages.length',context),0,'unknown voice clears prior identity and history');
  // Only successful voice matches get one greeting per person per conversation.
  vm.runInContext("profiles=[{id:'alice',name:'甲',salutation:'王先生'},{id:'bob',name:'乙',salutation:'李女士'}];applySpeaker({matched:true,id:'alice',name:'甲'});",context);
  assert.equal(vm.runInContext("voiceGreeting('text')",context),null);
  await vm.runInContext("send('测试问候',{source:'manual'})",context);
  assert.equal(vm.runInContext('messages.at(-1).content',context),'王先生，你好。已收到');
  vm.runInContext("applySpeaker({matched:true,id:'alice',name:'甲'})",context);
  await vm.runInContext("send('再问一次',{source:'manual'})",context);
  assert.equal(vm.runInContext('messages.at(-1).content',context),'已收到');
  vm.runInContext("applySpeaker({matched:true,id:'bob',name:'乙'})",context);
  await vm.runInContext("send('测试第二位用户',{source:'wake'})",context);
  assert.equal(vm.runInContext('messages.at(-1).content',context),'李女士，你好。已收到');
  vm.runInContext("applySpeaker({matched:false,id:null,reason:'不确定'})",context);
  assert.equal(vm.runInContext("voiceGreeting('manual')",context),null);
  vm.runInContext("applySpeaker({matched:true,id:'alice',name:'甲'})",context);
  assert.equal(vm.runInContext("voiceGreeting('manual')",context),null,'switching back must not repeat greeting');
  vm.runInContext("$('clear').onclick()",context);
  assert.equal(vm.runInContext("voiceGreeting('manual').text",context),'王先生，你好。');
  vm.runInContext("switchProfile('alice')",context);
  assert.equal(vm.runInContext("voiceGreeting('manual')",context),null,'manual selection is not a voice match');
  // A delayed old response must never enter the new conversation after cancellation.
  let release;
  context.fetch=async(path,options)=>{
    if(path==='/api/turn/cancel')return{ok:true,json:async()=>({cancelled:true})};
    if(path==='/api/chat')return{ok:true,body:{getReader:()=>({cancel:async()=>{},read:()=>new Promise(resolve=>{release=resolve;})})}};
    throw new Error('Unexpected path '+path);
  };
  vm.runInContext("applySpeaker({matched:true,id:'alice',name:'甲'})",context);
  const pending=vm.runInContext("send('请讲一个故事',{source:'manual'})",context);
  await new Promise(r=>setImmediate(r));
  vm.runInContext('cancelTurn()',context);
  release({done:false,value:new TextEncoder().encode('{"message":{"content":"旧回复"},"done":true}\n')});
  await pending;
  assert.equal(vm.runInContext("messages.some(m=>m.content==='旧回复')",context),false);
  assert.equal(vm.runInContext('busy',context),false);
  assert.equal(vm.runInContext("voiceGreeting('manual').text",context),'王先生，你好。','cancel before first response must not consume greeting');
  context.fetch=async()=>({json:async()=>({chat:true,asr:false,wake:false,vision:false,tts:false})});
  await vm.runInContext('status()',context);
  assert.equal(ids.autoplay.disabled,true,'text-only configuration disables speech');
  await vm.runInContext("speak('test')",context);
  assert.match(ids.notice.textContent,/未启用语音播放/);
  console.log('Frontend checks passed: permissions, camera, profile isolation, cancellation, PCM and disabled speech.');
})().catch(e=>{console.error(e);process.exitCode=1;});
