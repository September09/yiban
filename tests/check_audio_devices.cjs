// Simulated devices only: zero real capture, playback, gain changes or permission requests.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{constructor(){this.value='';this.checked=false;this.disabled=false;this.children=[];this.textContent='';}append(...x){this.children.push(...x);}replaceChildren(){this.children=[];}}
const ids=Object.fromEntries([...fs.readFileSync('static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const builtinIn={kind:'audioinput',deviceId:'builtin-in',label:'内置麦克风'},builtinOut={kind:'audiooutput',deviceId:'builtin-out',label:'内置扬声器'},xvfIn={kind:'audioinput',deviceId:'xvf-in',label:'ReSpeaker XVF3800'},xvfOut={kind:'audiooutput',deviceId:'xvf-out',label:'ReSpeaker XVF3800'};
let deviceList=[builtinIn,builtinOut,xvfIn,xvfOut],mediaCalls=0,plays=0,gainChanges=0,enumerations=0,authCalls=0,permissionState='granted',saved={},storageFail=false,systemCalls=0;
const events={},permissionDescriptor={state:'granted'};
class Player{constructor(){this.sinkId='';this.paused=0;}async setSinkId(id){this.sinkId=id;}pause(){this.paused++;}async play(){plays++;}set volume(value){gainChanges++;throw Error('gain changed');}}
const context=vm.createContext({document:{createElement:()=>new Element()},$:id=>ids[id],HTMLMediaElement:Player,
 navigator:{mediaDevices:{enumerateDevices:async()=>{enumerations++;return deviceList;},getUserMedia:async()=>{mediaCalls++;throw Error('Never capture during self check');},selectAudioOutput:async()=>{authCalls++;return xvfOut;},addEventListener:(type,fn)=>events[type]=fn},permissions:{query:async()=>{permissionDescriptor.state=permissionState;return permissionDescriptor;}}},
 localStorage:{getItem:k=>{if(storageFail)throw Error();return saved[k]??null;},setItem:(k,v)=>{if(storageFail)throw Error();saved[k]=v;}},
 window:{addEventListener:(type,fn)=>events[type]=fn},busy:false,recording:false,speaking:false,enrolling:false,wakeEnabled:false,wakeStarting:false,
 fetch:async()=>{systemCalls++;return{ok:true,json:async()=>({available:true,devices:[{name:'内置扬声器',input_channels:0,output_channels:2,default_output:true,volume_percent:2,muted:false}]})};},console});
vm.runInContext(fs.readFileSync('static/audio-devices.js','utf8'),context);
const call=code=>vm.runInContext(code,context);
async function apply(profile){ids.audioProfile.value=profile;ids.audioProfile.onchange();await ids.audioApply.onclick();}
(async()=>{
 assert.equal(mediaCalls,0);assert.equal(enumerations,0);assert.equal(systemCalls,0);
 await call('audioDevices.refresh()');assert.equal(mediaCalls,0);assert.equal(plays,0);assert.equal(authCalls,0);
 assert.ok(ids.audioChecks.children.some(p=>p.textContent.includes('音量很低')));
 assert.match(ids.audioActualInput.textContent,/未采集/);assert.match(ids.audioActualOutput.textContent,/未验证/);
 const defaultPlan=await call('audioDevices.capturePlan()');assert.equal(defaultPlan.constraints.deviceId,undefined,'unsaved route preserves default');
 for(const [profile,inID,outID] of [['builtin','builtin-in','builtin-out'],['respeaker','xvf-in','xvf-out'],['mixed','xvf-in','builtin-out']]){
  await apply(profile);assert.equal(JSON.parse(saved['yiban-audio-route-v1']).profile,profile);
  const plan=await call('audioDevices.capturePlan()');assert.equal(plan.constraints.deviceId.exact,inID);
  const player=new Player();context.player=player;await call('audioDevices.routeOutput(player)');assert.equal(player.sinkId,outID);assert.equal(plays,0);
 }
 assert.equal(mediaCalls,0);assert.equal(gainChanges,0);assert.equal(authCalls,0);
 // Recorded actual settings differ from requested capture/processing rates.
 const plan=await call('audioDevices.capturePlan()');context.plan=plan;let lostInput=0;
 const track={label:'ReSpeaker XVF3800',readyState:'live',getSettings:()=>({deviceId:'xvf-in',sampleRate:48000,channelCount:6,echoCancellation:false}),stop(){this.readyState='ended';}};
 context.track=track;context.onLost=()=>{lostInput++;};call('audioDevices.captureStarted(track,plan,onLost)');
 assert.match(ids.audioActualInput.textContent,/48000Hz/);assert.match(ids.audioActualInput.textContent,/16000Hz/);
 assert.ok(ids.audioChecks.children.some(p=>p.textContent.includes('未开启')));
 const output=new Player();context.player=output;await call('audioDevices.routeOutput(player)');call('audioDevices.playbackStarted(player)');
 deviceList=[builtinIn,xvfOut];await call('audioDevices.deviceChanged()');
 assert.equal(track.readyState,'ended');assert.equal(lostInput,1);assert.ok(output.paused>0,'disconnected selected output pauses');
 await assert.rejects(call('audioDevices.capturePlan()'),/指定输入未找到/);
 // Same family reappearing under another ID must not silently replace saved choice.
 deviceList=[builtinIn,builtinOut,{...xvfIn,deviceId:'new-xvf-id'},xvfOut];await call('audioDevices.refresh()');
 await assert.rejects(call('audioDevices.capturePlan()'),/指定输入未找到/);
 // Explicit user re-selection may adopt the new identifier.
 await apply('mixed');assert.equal(JSON.parse(saved['yiban-audio-route-v1']).input_id,'new-xvf-id');
 // No gain or capture even when applying profiles / checking missing permission.
 deviceList=[{kind:'audioinput',deviceId:'',label:''}];permissionState='denied';await call('audioDevices.refresh()');
 await assert.rejects(call('audioDevices.capturePlan()'),/权限被拒绝/);
 // Unsupported sink never falls back to an arbitrary output.
 deviceList=[builtinIn,builtinOut,xvfIn,xvfOut];permissionState='granted';await call('audioDevices.refresh()');await apply('respeaker');
 const original=Player.prototype.setSinkId;delete Player.prototype.setSinkId;
 await assert.rejects(call('audioDevices.routeOutput(player)'),/不支持指定输出/);Player.prototype.setSinkId=original;
 // Storage changes invalidate an in-flight route before it can play.
 let release;const slow=new Player();slow.setSinkId=id=>new Promise(resolve=>{release=()=>{slow.sinkId=id;resolve();};});context.slow=slow;
 const pending=call('audioDevices.routeOutput(slow)');await new Promise(r=>setImmediate(r));
 saved['yiban-audio-route-v1']=JSON.stringify({profile:'system',input_id:'',output_id:''});events.storage({key:'yiban-audio-route-v1'});release();
 await assert.rejects(pending,/档位在准备期间发生变化/);
 saved['yiban-audio-route-v1']='{broken';events.storage({key:'yiban-audio-route-v1'});
 await assert.rejects(call('audioDevices.capturePlan()'),/损坏或无法读取/);
 // Clear corruption only via an explicit saved choice, and preserve original default behavior.
 await apply('system');const back=await call('audioDevices.capturePlan()');assert.equal(back.profile,'system');
 storageFail=true;await apply('builtin');assert.match(ids.audioStatus.textContent,/未应用/);storageFail=false;
 assert.equal(plays,0);assert.equal(mediaCalls,0);assert.equal(authCalls,0);assert.equal(gainChanges,0);
 // Exercise the application's real Microphone class with fake tracks and a silent processing stub.
 await apply('builtin');let requested=null,stops=0;
 const live={label:'内置麦克风',readyState:'live',getSettings:()=>({deviceId:'builtin-in',sampleRate:44100,channelCount:2,echoCancellation:true}),stop(){this.readyState='ended';stops++;}};
 context.navigator.mediaDevices.getUserMedia=async constraints=>{mediaCalls++;requested=constraints;return{getAudioTracks:()=>[live],getTracks:()=>[live]};};
 context.AudioContext=class{constructor(){this.sampleRate=16000;this.state='running';this.audioWorklet={addModule:async()=>{}};this.destination={};}async resume(){}createMediaStreamSource(){return{connect(){},disconnect(){}};}async close(){this.state='closed';}};
 context.AudioWorkletNode=class{constructor(){this.port={};}connect(){}disconnect(){}};
 const source=fs.readFileSync('static/app.js','utf8');vm.runInContext(source.slice(source.indexOf('class Microphone{'),source.indexOf('class WakeListener{')),context);
 await call('var integrationMic=new Microphone();integrationMic.start(()=>{})');assert.equal(requested.audio.deviceId.exact,'builtin-in');
 assert.match(ids.audioActualInput.textContent,/44100Hz/);permissionState='denied';permissionDescriptor.state='denied';permissionDescriptor.onchange();await new Promise(r=>setImmediate(r));assert.equal(live.readyState,'ended','revoked permission stops capture');await call('integrationMic.stop()');assert.ok(stops>0);permissionState='granted';await call('audioDevices.refresh()');
 // A pending metadata read canceled before acquisition must never ask for capture.
 let releaseList;context.navigator.mediaDevices.enumerateDevices=()=>new Promise(resolve=>releaseList=()=>resolve(deviceList));
 const beforeCalls=mediaCalls;const opening=call('var cancelledMic=new Microphone();cancelledMic.start(()=>{})');await new Promise(r=>setImmediate(r));await call('cancelledMic.stop()');releaseList();await opening;
 assert.equal(mediaCalls,beforeCalls);assert.equal(plays,0);assert.equal(gainChanges,0);
 console.log('Audio device checks passed: exact routes, read-only self check, disconnect/cancel, real capture class with simulated tracks; no real media or gain.');
})().catch(error=>{console.error(error);process.exitCode=1;});
