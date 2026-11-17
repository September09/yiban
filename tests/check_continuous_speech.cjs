const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const tick=()=>new Promise(r=>setImmediate(r));
function fixture({badPlay=false,missingDone=false,routeFailure=false,sink='',contextSink=false}={}){
  const contexts=[],audios=[],routes=[],starts=[],notices=[],requests=[],timers=[];let finished=0;
  class Context{
    constructor(){this.currentTime=0;this.sources=[];this.destination={};if(contextSink)this.setSinkId=async id=>{this.sinkId=id;};contexts.push(this);}
    createMediaStreamDestination(){return {stream:{getTracks:()=>[{stop:()=>{this.trackStopped=true;}}]}};}
    async resume(){if(badPlay)throw new Error('blocked');}async close(){this.closed=true;}
    createBuffer(ch,n,rate){return {duration:n/rate,copyToChannel(){}};}
    createBufferSource(){const c=this,s={connect(){},disconnect(){},start(t){this.startTime=t;},stop(){this.stopped=true;}};c.sources.push(s);return s;}
  }
  class Audio{constructor(){audios.push(this);}async play(){this.played=true;if(badPlay)throw new Error('blocked');}pause(){this.paused=true;}}
  const frame=seq=>({type:'audio',sequence:seq,format:'f32le',sample_rate:24000,samples:12000,audio:Buffer.alloc(48000).toString('base64')});
  const completion={type:'done',chunks:3,total_samples:36000,model_calls:1};
  const context=vm.createContext({window:{AudioContext:Context},Audio,atob:s=>Buffer.from(s,'base64').toString('binary'),Uint8Array,Float32Array,DataView,TextDecoder,AbortController,
    setTimeout:(f)=>{timers.push(f);return timers.length;},clearTimeout(){},
    audioDevices:{routeOutput:async p=>{if(routeFailure)throw new Error('route lost');p.sinkId=sink;routes.push(p);},playbackStarted(){},releaseOutput(){}},
    turnVersion:1,turnId:'synthetic-turn-0001',ttsAbort:null,continuousPlayback:null,audio:null,speaking:false,wakeEnabled:false,wakeListener:null,
    $:()=>({checked:false}),pauseWake:async()=>{},monitorInterrupt:async()=>{},newTurnId:()=>'',notice:t=>notices.push(t),state(){},
    stopAudio(){const p=context.continuousPlayback;context.continuousPlayback=null;p?.stop();},
    conversationDiagnostics:{stage(){},playbackStarted:()=>starts.push(true),finish:()=>finished++,refresh(){},headers:()=>({})},
    api:async(url,data,type,signal)=>{requests.push({url,data});let i=0;return {body:{getReader:()=>({read:async()=>{
      await tick();if(signal.aborted){const e=new Error('abort');e.name='AbortError';throw e;}
      if(i<3)return {done:false,value:new TextEncoder().encode(JSON.stringify(frame(i++))+'\n')};
      if(i++===3&&!missingDone)return {done:false,value:new TextEncoder().encode(JSON.stringify(completion)+'\n')};
      return {done:true};},cancel:async()=>{}})}};}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/speech-stream.js'),'utf8'),context);
  return {context,contexts,audios,routes,starts,notices,requests,timers,frame,completion,get finished(){return finished;}};
}
async function run(){
  const f=fixture(),p=vm.runInContext('new ContinuousSpeechPlayer({onStart:()=>conversationDiagnostics.playbackStarted()})',f.context);
  await p.push(f.frame(0));f.timers.forEach(fn=>fn());assert.equal(f.starts.length,1,'first audio starts before remaining audio/done');
  await p.push(f.frame(1));await p.push(f.frame(2));assert.equal(f.contexts.length,1);assert.equal(f.audios.length,1);assert.equal(f.routes.length,1);
  const c=f.contexts[0];assert.deepEqual(c.sources.map(s=>s.startTime),[.12,.62,1.12]);
  let ended=false;p.finish(f.completion).then(()=>ended=true);await tick();assert.equal(ended,false);
  for(const s of c.sources)s.onended();await tick();assert.equal(ended,true);assert.equal(c.closed,true);
  const stop=fixture(),sp=vm.runInContext('new ContinuousSpeechPlayer()',stop.context);await sp.push(stop.frame(0));sp.stop();await sp.push(stop.frame(1));
  assert.equal(stop.contexts[0].sources.length,1);assert.ok(stop.contexts[0].sources[0].stopped);assert.ok(stop.contexts[0].closed);assert.equal(await sp.done,false);
  const direct=fixture({sink:'chosen-output',contextSink:true}),dp=vm.runInContext('new ContinuousSpeechPlayer()',direct.context);await dp.push(direct.frame(0));
  assert.equal(direct.contexts[0].sinkId,'chosen-output');assert.equal(dp.bridged,false);assert.ok(!direct.audios[0].played);dp.stop();
  const bridge=fixture({sink:'chosen-output'}),bp=vm.runInContext('new ContinuousSpeechPlayer()',bridge.context);await bp.push(bridge.frame(0));
  assert.equal(bp.bridged,true);assert.equal(bridge.audios[0].played,true);bp.stop();assert.equal(bridge.contexts[0].trackStopped,true);
  const invalid=fixture(),ip=vm.runInContext('new ContinuousSpeechPlayer()',invalid.context);await assert.rejects(ip.push(invalid.frame(1)),/顺序/);
  await assert.rejects(ip.push({...invalid.frame(0),audio:'AAAA'}),/长度/);assert.throws(()=>ip.finish(invalid.completion),/完整/);
  const whole=fixture(),speech=vm.runInContext('openContinuousReplySpeech(1,"trace")',whole.context);
  speech.feed('first sentence');assert.equal(whole.requests.length,0);
  const running=speech.end('first sentence; second sentence.');for(let i=0;i<20;i++)await tick();
  assert.equal(whole.requests.length,1);assert.equal(whole.requests[0].url,'/api/speak/stream');assert.equal(whole.requests[0].data.text,'first sentence; second sentence.');
  for(const fn of whole.timers)fn();assert.equal(whole.starts.length,1);
  for(const s of whole.contexts[0].sources)s.onended();assert.equal(await running,true);assert.equal(whole.finished,1);
  for(const opts of [{missingDone:true},{badPlay:true},{routeFailure:true}]){
    const err=fixture(opts),s=vm.runInContext('openContinuousReplySpeech(1,"trace")',err.context);assert.equal(await s.end('reply'),false);assert.equal(s.failed,true);assert.ok(err.notices.some(Boolean));assert.equal(err.finished,1);
    assert.equal(err.starts.length,0);assert.ok(err.contexts[0].closed);
  }
  const cancel=fixture(),cs=vm.runInContext('openContinuousReplySpeech(1,"trace")',cancel.context),pending=cs.end('reply');
  for(let i=0;i<10&&!cancel.contexts[0]?.sources.length;i++)await tick();
  cancel.context.turnVersion++;cancel.context.ttsAbort.abort();assert.equal(await pending,false);assert.equal(cs.failed,false);assert.ok(cancel.contexts[0].sources.every(s=>s.stopped));
  console.log('Continuous speech checks passed: one utterance, contiguous PCM, early playback, cancellation and explicit errors.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
